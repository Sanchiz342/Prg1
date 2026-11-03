// Package store is the PostgreSQL persistence layer: API keys, links, clicks and statistics.
package store

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"embed"
	"errors"
	"fmt"
	"io/fs"
	"math/big"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	ErrNotFound = errors.New("not found")
	ErrConflict = errors.New("already exists")
)

//go:embed migrations/*.sql
var migrationFS embed.FS

type Store struct {
	pool *pgxpool.Pool
}

func Open(ctx context.Context, dsn string) (*Store, error) {
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, fmt.Errorf("parse DATABASE_URL: %w", err)
	}
	// Day buckets in statistics are UTC days regardless of the server's configured time zone.
	cfg.ConnConfig.RuntimeParams["timezone"] = "UTC"
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("connect to postgres: %w", err)
	}
	return &Store{pool: pool}, nil
}

func (s *Store) Close()                         { s.pool.Close() }
func (s *Store) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

// Migrate applies embedded migrations in order. An advisory lock makes it safe when several instances start at once.
func (s *Store) Migrate(ctx context.Context) error {
	conn, err := s.pool.Acquire(ctx)
	if err != nil {
		return err
	}
	defer conn.Release()
	const lockID = 7_301_975_001
	if _, err := conn.Exec(ctx, "SELECT pg_advisory_lock($1)", lockID); err != nil {
		return err
	}
	defer conn.Exec(context.Background(), "SELECT pg_advisory_unlock($1)", lockID) //nolint:errcheck

	if _, err := conn.Exec(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (
		version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())`); err != nil {
		return err
	}
	names, err := fs.Glob(migrationFS, "migrations/*.sql")
	if err != nil {
		return err
	}
	sort.Strings(names)
	for _, name := range names {
		version := strings.TrimSuffix(strings.TrimPrefix(name, "migrations/"), ".sql")
		var exists bool
		if err := conn.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=$1)", version).
			Scan(&exists); err != nil {
			return err
		}
		if exists {
			continue
		}
		sql, err := migrationFS.ReadFile(name)
		if err != nil {
			return err
		}
		tx, err := conn.Begin(ctx)
		if err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, string(sql)); err != nil {
			_ = tx.Rollback(ctx)
			return fmt.Errorf("migration %s: %w", version, err)
		}
		if _, err := tx.Exec(ctx, "INSERT INTO schema_migrations(version) VALUES($1)", version); err != nil {
			_ = tx.Rollback(ctx)
			return err
		}
		if err := tx.Commit(ctx); err != nil {
			return err
		}
	}
	return nil
}

// ---- API keys ------------------------------------------------------------------------------------------------

const keyAlphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

func hashKey(key string) []byte {
	sum := sha256.Sum256([]byte(key))
	return sum[:]
}

// NewAPIKey generates a key ("snip_" + 32 base62 chars, ~190 bits) and stores only its hash.
func (s *Store) NewAPIKey(ctx context.Context, name string) (key string, id int64, err error) {
	var b strings.Builder
	b.WriteString("snip_")
	for i := 0; i < 32; i++ {
		n, err := rand.Int(rand.Reader, big.NewInt(int64(len(keyAlphabet))))
		if err != nil {
			return "", 0, err
		}
		b.WriteByte(keyAlphabet[n.Int64()])
	}
	key = b.String()
	id, err = s.ImportAPIKey(ctx, name, key)
	return key, id, err
}

// ImportAPIKey stores a key chosen by the operator (BOOTSTRAP_API_KEY). Idempotent.
func (s *Store) ImportAPIKey(ctx context.Context, name, key string) (int64, error) {
	var id int64
	err := s.pool.QueryRow(ctx, `
		INSERT INTO api_keys(name, key_hash) VALUES($1, $2)
		ON CONFLICT (key_hash) DO UPDATE SET name = api_keys.name
		RETURNING id`, name, hashKey(key)).Scan(&id)
	return id, err
}

// Authenticate returns the key id for a valid, non-revoked key.
func (s *Store) Authenticate(ctx context.Context, key string) (int64, error) {
	var id int64
	err := s.pool.QueryRow(ctx, "SELECT id FROM api_keys WHERE key_hash=$1 AND revoked_at IS NULL", hashKey(key)).
		Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, ErrNotFound
	}
	return id, err
}

// ---- links ---------------------------------------------------------------------------------------------------

type Link struct {
	ID         int64
	Code       string
	TargetURL  string
	OwnerID    int64
	CreatedAt  time.Time
	UpdatedAt  time.Time
	ExpiresAt  *time.Time
	Disabled   bool
	ClickCount int64
}

const linkColumns = "id, code, target_url, owner_id, created_at, updated_at, expires_at, disabled, click_count"

func scanLink(row pgx.Row) (Link, error) {
	var l Link
	err := row.Scan(&l.ID, &l.Code, &l.TargetURL, &l.OwnerID, &l.CreatedAt, &l.UpdatedAt, &l.ExpiresAt, &l.Disabled,
		&l.ClickCount)
	if errors.Is(err, pgx.ErrNoRows) {
		return l, ErrNotFound
	}
	return l, err
}

// CreateLink inserts a link with the given code; ErrConflict if the code is taken.
func (s *Store) CreateLink(ctx context.Context, code, target string, owner int64, expires *time.Time) (Link, error) {
	l, err := scanLink(s.pool.QueryRow(ctx, `
		INSERT INTO links(code, target_url, owner_id, expires_at) VALUES($1, $2, $3, $4)
		ON CONFLICT (code) DO NOTHING
		RETURNING `+linkColumns, code, target, owner, expires))
	if errors.Is(err, ErrNotFound) {
		return l, ErrConflict
	}
	return l, err
}

func (s *Store) LinkByCode(ctx context.Context, code string) (Link, error) {
	return scanLink(s.pool.QueryRow(ctx, "SELECT "+linkColumns+" FROM links WHERE code=$1", code))
}

// ListLinks returns the owner's links, newest first, with keyset pagination (before = id cursor, 0 = first page).
func (s *Store) ListLinks(ctx context.Context, owner, before int64, limit int) ([]Link, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT `+linkColumns+` FROM links
		WHERE owner_id=$1 AND ($2 = 0 OR id < $2)
		ORDER BY id DESC LIMIT $3`, owner, before, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Link
	for rows.Next() {
		l, err := scanLink(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, l)
	}
	return out, rows.Err()
}

// LinkPatch holds optional changes; ClearExpiry removes the expiry date.
type LinkPatch struct {
	TargetURL   *string
	ExpiresAt   *time.Time
	ClearExpiry bool
	Disabled    *bool
}

func (s *Store) UpdateLink(ctx context.Context, owner int64, code string, p LinkPatch) (Link, error) {
	return scanLink(s.pool.QueryRow(ctx, `
		UPDATE links SET
			target_url = COALESCE($3, target_url),
			expires_at = CASE WHEN $5 THEN NULL ELSE COALESCE($4, expires_at) END,
			disabled   = COALESCE($6, disabled),
			updated_at = now()
		WHERE owner_id=$1 AND code=$2
		RETURNING `+linkColumns, owner, code, p.TargetURL, p.ExpiresAt, p.ClearExpiry, p.Disabled))
}

func (s *Store) DeleteLink(ctx context.Context, owner int64, code string) error {
	tag, err := s.pool.Exec(ctx, "DELETE FROM links WHERE owner_id=$1 AND code=$2", owner, code)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// ---- clicks --------------------------------------------------------------------------------------------------

type Click struct {
	EventID      string
	LinkID       int64
	TS           time.Time
	VisitorHash  []byte
	Country      string
	ReferrerHost string
	Device       string
	Browser      string
	IsBot        bool
}

// InsertClicks writes a batch in one statement and bumps links.click_count for the rows actually inserted.
// Duplicates (redelivered stream entries) are ignored via the event_id primary key, and clicks for links deleted
// in the meantime are skipped instead of failing the whole batch on the foreign key.
func (s *Store) InsertClicks(ctx context.Context, batch []Click) (int64, error) {
	if len(batch) == 0 {
		return 0, nil
	}
	n := len(batch)
	ids, links, ts := make([]string, n), make([]int64, n), make([]time.Time, n)
	visitors, countries, refs := make([][]byte, n), make([]string, n), make([]string, n)
	devices, browsers, bots := make([]string, n), make([]string, n), make([]bool, n)
	for i, c := range batch {
		ids[i], links[i], ts[i] = c.EventID, c.LinkID, c.TS
		visitors[i], countries[i], refs[i] = c.VisitorHash, c.Country, c.ReferrerHost
		devices[i], browsers[i], bots[i] = c.Device, c.Browser, c.IsBot
	}
	var inserted int64
	err := s.pool.QueryRow(ctx, `
		WITH input AS (
			SELECT * FROM unnest($1::uuid[], $2::bigint[], $3::timestamptz[], $4::bytea[], $5::text[], $6::text[],
			                     $7::text[], $8::text[], $9::bool[])
			    AS u(event_id, link_id, ts, visitor_hash, country, referrer_host, device, browser, is_bot)
		), ins AS (
			INSERT INTO clicks(event_id, link_id, ts, visitor_hash, country, referrer_host, device, browser, is_bot)
			SELECT i.* FROM input i WHERE EXISTS (SELECT 1 FROM links l WHERE l.id = i.link_id)
			ON CONFLICT (event_id) DO NOTHING
			RETURNING link_id
		), agg AS (
			SELECT link_id, count(*) AS c FROM ins GROUP BY link_id
		), upd AS (
			UPDATE links l SET click_count = l.click_count + agg.c FROM agg WHERE l.id = agg.link_id RETURNING agg.c
		)
		SELECT COALESCE(sum(c), 0)::bigint FROM upd`,
		ids, links, ts, visitors, countries, refs, devices, browsers, bots).Scan(&inserted)
	return inserted, err
}

// ---- statistics ----------------------------------------------------------------------------------------------

type Bucket struct {
	Key    string `json:"key"`
	Clicks int64  `json:"clicks"`
}

type DayPoint struct {
	Date   string `json:"date"`
	Clicks int64  `json:"clicks"`
	Unique int64  `json:"unique"`
}

type Stats struct {
	Clicks     int64      `json:"clicks"`          // human clicks in range
	Unique     int64      `json:"unique_visitors"` // distinct visitors per day, summed (hash rotates daily)
	BotClicks  int64      `json:"bot_clicks"`
	Timeseries []DayPoint `json:"timeseries"`
	Countries  []Bucket   `json:"countries"`
	Referrers  []Bucket   `json:"referrers"`
	Devices    []Bucket   `json:"devices"`
	Browsers   []Bucket   `json:"browsers"`
}

// LinkStats aggregates clicks in [from, to) (UTC days). Bots are counted separately and excluded elsewhere.
func (s *Store) LinkStats(ctx context.Context, linkID int64, from, to time.Time, top int) (Stats, error) {
	st := Stats{Timeseries: []DayPoint{}}
	rows, err := s.pool.Query(ctx, `
		SELECT to_char(d, 'YYYY-MM-DD'),
		       count(c.event_id) FILTER (WHERE NOT c.is_bot),
		       count(DISTINCT c.visitor_hash) FILTER (WHERE NOT c.is_bot),
		       count(c.event_id) FILTER (WHERE c.is_bot)
		FROM generate_series($2::timestamptz, $3::timestamptz - interval '1 day', interval '1 day') AS d
		LEFT JOIN clicks c ON c.link_id = $1 AND c.ts >= d AND c.ts < d + interval '1 day'
		GROUP BY d ORDER BY d`, linkID, from, to)
	if err != nil {
		return st, err
	}
	for rows.Next() {
		var p DayPoint
		var bots int64
		if err := rows.Scan(&p.Date, &p.Clicks, &p.Unique, &bots); err != nil {
			rows.Close()
			return st, err
		}
		st.Timeseries = append(st.Timeseries, p)
		st.Clicks += p.Clicks
		st.Unique += p.Unique
		st.BotClicks += bots
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return st, err
	}

	breakdown := func(column string) ([]Bucket, error) {
		// column is one of a fixed set of identifiers below, never user input.
		rows, err := s.pool.Query(ctx, `
			SELECT `+column+`, count(*) FROM clicks
			WHERE link_id=$1 AND ts >= $2 AND ts < $3 AND NOT is_bot
			GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT $4`, linkID, from, to, top)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		out := []Bucket{}
		for rows.Next() {
			var b Bucket
			if err := rows.Scan(&b.Key, &b.Clicks); err != nil {
				return nil, err
			}
			out = append(out, b)
		}
		return out, rows.Err()
	}
	if st.Countries, err = breakdown("country"); err != nil {
		return st, err
	}
	if st.Referrers, err = breakdown("referrer_host"); err != nil {
		return st, err
	}
	if st.Devices, err = breakdown("device"); err != nil {
		return st, err
	}
	st.Browsers, err = breakdown("browser")
	return st, err
}

// Truncate empties all tables (tests only).
func (s *Store) Truncate(ctx context.Context) error {
	_, err := s.pool.Exec(ctx, "TRUNCATE clicks, links, api_keys RESTART IDENTITY CASCADE")
	return err
}
