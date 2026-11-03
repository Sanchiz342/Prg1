// Package config reads runtime settings from environment variables (see .env.example).
package config

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"log/slog"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

// Limit is a token-bucket rate limit: Rate tokens per second, at most Burst at once.
type Limit struct {
	Rate  float64
	Burst int
}

type Config struct {
	HTTPAddr    string
	BaseURL     *url.URL
	DatabaseURL string
	RedisURL    string
	RedisPrefix string

	// TrustProxy makes the server take the client IP from X-Real-IP and the country from GeoHeader.
	// Enable it only behind a proxy that overwrites those headers (see deploy/nginx.conf).
	TrustProxy bool
	GeoHeader  string
	// VisitorSalt keys the hash used for unique-visitor counting. All instances must share it.
	VisitorSalt string

	CacheTTL         time.Duration
	NegativeCacheTTL time.Duration

	RedirectLimit     Limit // per client IP
	CreateLimit       Limit // per API key
	APILimit          Limit // other API calls, per API key
	RateLimitFailOpen bool

	WorkerEnabled bool
	StreamMaxLen  int64
	EventBuffer   int
	BootstrapKey  string
	BootstrapName string
}

func Load() (*Config, error) {
	base, err := url.Parse(env("BASE_URL", "http://localhost:8080"))
	if err != nil || base.Scheme == "" || base.Host == "" {
		return nil, fmt.Errorf("BASE_URL must be an absolute URL, got %q", os.Getenv("BASE_URL"))
	}
	base.Path = strings.TrimRight(base.Path, "/")

	c := &Config{
		HTTPAddr:          env("HTTP_ADDR", ":8080"),
		BaseURL:           base,
		DatabaseURL:       env("DATABASE_URL", "postgres://postgres:postgres@localhost:5432/shortener?sslmode=disable"),
		RedisURL:          env("REDIS_URL", "redis://localhost:6379/0"),
		RedisPrefix:       env("REDIS_PREFIX", "snip:"),
		TrustProxy:        envBool("TRUST_PROXY", false),
		GeoHeader:         env("GEO_HEADER", "CF-IPCountry"),
		VisitorSalt:       os.Getenv("VISITOR_SALT"),
		CacheTTL:          envDuration("CACHE_TTL", time.Hour),
		NegativeCacheTTL:  envDuration("NEGATIVE_CACHE_TTL", 30*time.Second),
		RedirectLimit:     Limit{Rate: envFloat("RL_REDIRECT_RATE", 20), Burst: envInt("RL_REDIRECT_BURST", 60)},
		CreateLimit:       Limit{Rate: envFloat("RL_CREATE_RATE", 0.5), Burst: envInt("RL_CREATE_BURST", 20)},
		APILimit:          Limit{Rate: envFloat("RL_API_RATE", 10), Burst: envInt("RL_API_BURST", 40)},
		RateLimitFailOpen: envBool("RATE_LIMIT_FAIL_OPEN", true),
		WorkerEnabled:     envBool("WORKER_ENABLED", true),
		StreamMaxLen:      int64(envInt("STREAM_MAXLEN", 1_000_000)),
		EventBuffer:       envInt("EVENT_BUFFER", 10_000),
		BootstrapKey:      os.Getenv("BOOTSTRAP_API_KEY"),
		BootstrapName:     env("BOOTSTRAP_API_KEY_NAME", "bootstrap"),
	}
	if c.VisitorSalt == "" {
		b := make([]byte, 16)
		_, _ = rand.Read(b)
		c.VisitorSalt = hex.EncodeToString(b)
		slog.Warn("VISITOR_SALT is not set; using a random one — unique-visitor counts will differ between instances and restarts")
	}
	return c, nil
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envBool(key string, def bool) bool {
	if v, err := strconv.ParseBool(os.Getenv(key)); err == nil {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(key)); err == nil {
		return v
	}
	return def
}

func envFloat(key string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(key), 64); err == nil {
		return v
	}
	return def
}

func envDuration(key string, def time.Duration) time.Duration {
	if v, err := time.ParseDuration(os.Getenv(key)); err == nil {
		return v
	}
	return def
}
