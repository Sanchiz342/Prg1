package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/sanchiz342/prg1/link-shortener/internal/shortcode"
	"github.com/sanchiz342/prg1/link-shortener/internal/store"
	"github.com/sanchiz342/prg1/link-shortener/internal/urlcheck"
)

const (
	maxBody       = 64 << 10
	maxExpiry     = 10 * 365 * 24 * time.Hour
	maxStatsRange = 366
)

type linkJSON struct {
	Code      string     `json:"code"`
	ShortURL  string     `json:"short_url"`
	URL       string     `json:"url"`
	Status    string     `json:"status"` // active | expired | disabled
	Clicks    int64      `json:"clicks"`
	CreatedAt time.Time  `json:"created_at"`
	UpdatedAt time.Time  `json:"updated_at"`
	ExpiresAt *time.Time `json:"expires_at"`
}

func (s *Server) toJSON(l store.Link) linkJSON {
	status := "active"
	switch {
	case l.Disabled:
		status = "disabled"
	case l.ExpiresAt != nil && !s.now().Before(*l.ExpiresAt):
		status = "expired"
	}
	return linkJSON{
		Code: l.Code, ShortURL: s.cfg.BaseURL.String() + "/" + l.Code, URL: l.TargetURL, Status: status,
		Clicks: l.ClickCount, CreatedAt: l.CreatedAt, UpdatedAt: l.UpdatedAt, ExpiresAt: l.ExpiresAt,
	}
}

func decodeJSON(w http.ResponseWriter, r *http.Request, v any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return false
	}
	return true
}

func (s *Server) validateExpiry(t time.Time) error {
	now := s.now()
	if !t.After(now) {
		return errors.New("expiry must be in the future")
	}
	if t.Sub(now) > maxExpiry {
		return errors.New("expiry must be within 10 years")
	}
	return nil
}

// POST /api/links {"url": "...", "alias": "optional", "expires_at": "RFC3339" | "expires_in": seconds}
func (s *Server) createLink(w http.ResponseWriter, r *http.Request) {
	var req struct {
		URL       string     `json:"url"`
		Alias     string     `json:"alias"`
		ExpiresAt *time.Time `json:"expires_at"`
		ExpiresIn int64      `json:"expires_in"`
	}
	if !decodeJSON(w, r, &req) {
		return
	}
	target, err := urlcheck.Validate(req.URL, s.cfg.BaseURL.Host)
	if err != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid_url", err.Error())
		return
	}
	expires := req.ExpiresAt
	if req.ExpiresIn != 0 {
		if expires != nil {
			writeError(w, http.StatusUnprocessableEntity, "invalid_expiry", "use either expires_at or expires_in")
			return
		}
		t := s.now().Add(time.Duration(req.ExpiresIn) * time.Second)
		expires = &t
	}
	if expires != nil {
		if err := s.validateExpiry(*expires); err != nil {
			writeError(w, http.StatusUnprocessableEntity, "invalid_expiry", err.Error())
			return
		}
		t := expires.UTC().Truncate(time.Second)
		expires = &t
	}

	owner := ownerFrom(r.Context())
	var link store.Link
	if req.Alias != "" {
		if err := shortcode.ValidateAlias(req.Alias); err != nil {
			writeError(w, http.StatusUnprocessableEntity, "invalid_alias", err.Error())
			return
		}
		link, err = s.st.CreateLink(r.Context(), req.Alias, target, owner, expires)
		if errors.Is(err, store.ErrConflict) {
			writeError(w, http.StatusConflict, "alias_taken", "this alias is already in use")
			return
		}
	} else {
		// Random codes collide astronomically rarely; retry a few times rather than check-then-insert (racy).
		for attempt := 0; attempt < 5; attempt++ {
			link, err = s.st.CreateLink(r.Context(), shortcode.New(), target, owner, expires)
			if !errors.Is(err, store.ErrConflict) {
				break
			}
		}
	}
	if err != nil {
		s.internalError(w, "create link", err)
		return
	}
	// Someone may have requested this code before it existed: drop the negative cache entry.
	if err := s.cache.Invalidate(r.Context(), link.Code); err != nil {
		slog.Warn("cache invalidation failed", "code", link.Code, "err", err)
	}
	w.Header().Set("Location", "/api/links/"+link.Code)
	writeJSON(w, http.StatusCreated, s.toJSON(link))
}

// GET /api/links?limit=20&cursor=<opaque>
func (s *Server) listLinks(w http.ResponseWriter, r *http.Request) {
	limit := 20
	if v := r.URL.Query().Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 100 {
			writeError(w, http.StatusBadRequest, "invalid_limit", "limit must be 1..100")
			return
		}
		limit = n
	}
	var before int64
	if v := r.URL.Query().Get("cursor"); v != "" {
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil || n <= 0 {
			writeError(w, http.StatusBadRequest, "invalid_cursor", "invalid cursor")
			return
		}
		before = n
	}
	links, err := s.st.ListLinks(r.Context(), ownerFrom(r.Context()), before, limit+1)
	if err != nil {
		s.internalError(w, "list links", err)
		return
	}
	var next *string
	if len(links) > limit {
		links = links[:limit]
		c := strconv.FormatInt(links[len(links)-1].ID, 10)
		next = &c
	}
	items := make([]linkJSON, 0, len(links))
	for _, l := range links {
		items = append(items, s.toJSON(l))
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": items, "next_cursor": next})
}

// ownedLink loads a link and hides links of other owners behind 404 (no existence leak).
func (s *Server) ownedLink(w http.ResponseWriter, r *http.Request) (store.Link, bool) {
	l, err := s.st.LinkByCode(r.Context(), r.PathValue("code"))
	if errors.Is(err, store.ErrNotFound) || (err == nil && l.OwnerID != ownerFrom(r.Context())) {
		writeError(w, http.StatusNotFound, "not_found", "link not found")
		return l, false
	}
	if err != nil {
		s.internalError(w, "get link", err)
		return l, false
	}
	return l, true
}

func (s *Server) getLink(w http.ResponseWriter, r *http.Request) {
	if l, ok := s.ownedLink(w, r); ok {
		writeJSON(w, http.StatusOK, s.toJSON(l))
	}
}

// PATCH /api/links/{code} {"url"?: "...", "expires_at"?: "RFC3339" | null, "disabled"?: bool}
func (s *Server) updateLink(w http.ResponseWriter, r *http.Request) {
	var raw map[string]json.RawMessage
	if !decodeJSON(w, r, &raw) {
		return
	}
	var p store.LinkPatch
	for field, value := range raw {
		switch field {
		case "url":
			var u string
			if json.Unmarshal(value, &u) != nil {
				writeError(w, http.StatusBadRequest, "invalid_json", "url must be a string")
				return
			}
			target, err := urlcheck.Validate(u, s.cfg.BaseURL.Host)
			if err != nil {
				writeError(w, http.StatusUnprocessableEntity, "invalid_url", err.Error())
				return
			}
			p.TargetURL = &target
		case "expires_at":
			if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
				p.ClearExpiry = true
				continue
			}
			var t time.Time
			if json.Unmarshal(value, &t) != nil {
				writeError(w, http.StatusBadRequest, "invalid_json", "expires_at must be an RFC 3339 time or null")
				return
			}
			if err := s.validateExpiry(t); err != nil {
				writeError(w, http.StatusUnprocessableEntity, "invalid_expiry", err.Error())
				return
			}
			t = t.UTC().Truncate(time.Second)
			p.ExpiresAt = &t
		case "disabled":
			var d bool
			if json.Unmarshal(value, &d) != nil {
				writeError(w, http.StatusBadRequest, "invalid_json", "disabled must be a boolean")
				return
			}
			p.Disabled = &d
		default:
			writeError(w, http.StatusBadRequest, "invalid_json", fmt.Sprintf("unknown field %q", field))
			return
		}
	}
	code := r.PathValue("code")
	l, err := s.st.UpdateLink(r.Context(), ownerFrom(r.Context()), code, p)
	if errors.Is(err, store.ErrNotFound) {
		writeError(w, http.StatusNotFound, "not_found", "link not found")
		return
	}
	if err != nil {
		s.internalError(w, "update link", err)
		return
	}
	if err := s.cache.Invalidate(r.Context(), code); err != nil {
		slog.Warn("cache invalidation failed; stale redirect until TTL", "code", code, "err", err)
	}
	writeJSON(w, http.StatusOK, s.toJSON(l))
}

func (s *Server) deleteLink(w http.ResponseWriter, r *http.Request) {
	code := r.PathValue("code")
	err := s.st.DeleteLink(r.Context(), ownerFrom(r.Context()), code)
	if errors.Is(err, store.ErrNotFound) {
		writeError(w, http.StatusNotFound, "not_found", "link not found")
		return
	}
	if err != nil {
		s.internalError(w, "delete link", err)
		return
	}
	if err := s.cache.Invalidate(r.Context(), code); err != nil {
		slog.Warn("cache invalidation failed; stale redirect until TTL", "code", code, "err", err)
	}
	w.WriteHeader(http.StatusNoContent)
}

// GET /api/links/{code}/stats?from=YYYY-MM-DD&to=YYYY-MM-DD (UTC, inclusive; default: last 30 days)
func (s *Server) linkStats(w http.ResponseWriter, r *http.Request) {
	l, ok := s.ownedLink(w, r)
	if !ok {
		return
	}
	today := s.now().UTC().Truncate(24 * time.Hour)
	to, from := today, today.AddDate(0, 0, -29)
	parse := func(name string, dst *time.Time) bool {
		v := r.URL.Query().Get(name)
		if v == "" {
			return true
		}
		t, err := time.Parse("2006-01-02", v)
		if err != nil {
			writeError(w, http.StatusBadRequest, "invalid_date", name+" must be YYYY-MM-DD")
			return false
		}
		*dst = t
		return true
	}
	if !parse("from", &from) || !parse("to", &to) {
		return
	}
	days := int(to.Sub(from).Hours()/24) + 1
	if days < 1 || days > maxStatsRange {
		writeError(w, http.StatusBadRequest, "invalid_range", fmt.Sprintf("range must be 1..%d days", maxStatsRange))
		return
	}
	st, err := s.st.LinkStats(r.Context(), l.ID, from, to.AddDate(0, 0, 1), 10)
	if err != nil {
		s.internalError(w, "link stats", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"code":         l.Code,
		"from":         from.Format("2006-01-02"),
		"to":           to.Format("2006-01-02"),
		"total_clicks": l.ClickCount, // all time, including bots
		"stats":        st,
		"note":         "clicks are written asynchronously and usually appear within a second or two",
	})
}
