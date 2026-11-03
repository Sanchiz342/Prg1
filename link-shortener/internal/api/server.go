// Package api is the HTTP layer: the redirect endpoint, the JSON management API and operational endpoints.
package api

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math"
	"net"
	"net/http"
	"net/netip"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"

	"github.com/sanchiz342/prg1/link-shortener/internal/analytics"
	"github.com/sanchiz342/prg1/link-shortener/internal/cache"
	"github.com/sanchiz342/prg1/link-shortener/internal/config"
	"github.com/sanchiz342/prg1/link-shortener/internal/metrics"
	"github.com/sanchiz342/prg1/link-shortener/internal/ratelimit"
	"github.com/sanchiz342/prg1/link-shortener/internal/store"
	"github.com/sanchiz342/prg1/link-shortener/web"
)

type Server struct {
	cfg     *config.Config
	st      *store.Store
	rdb     *redis.Client
	cache   *cache.LinkCache
	limiter *ratelimit.Limiter
	pub     *analytics.Publisher
	now     func() time.Time
}

func New(cfg *config.Config, st *store.Store, rdb *redis.Client, pub *analytics.Publisher) *Server {
	return &Server{
		cfg:     cfg,
		st:      st,
		rdb:     rdb,
		cache:   cache.New(rdb, cfg.RedisPrefix, cfg.CacheTTL, cfg.NegativeCacheTTL, st.LinkByCode),
		limiter: ratelimit.New(rdb, cfg.RedisPrefix),
		pub:     pub,
		now:     time.Now,
	}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	handle := func(pattern string, h http.HandlerFunc) { mux.Handle(pattern, instrument(pattern, h)) }

	handle("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/app/", http.StatusFound)
	})
	mux.Handle("GET /app/", http.StripPrefix("/app/", web.Handler()))
	handle("GET /healthz", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]string{"status": "ok"}) })
	handle("GET /readyz", s.ready)
	mux.Handle("GET /metrics", promhttp.Handler())

	handle("POST /api/links", s.authed("create", s.cfg.CreateLimit, s.createLink))
	handle("GET /api/links", s.authed("api", s.cfg.APILimit, s.listLinks))
	handle("GET /api/links/{code}", s.authed("api", s.cfg.APILimit, s.getLink))
	handle("PATCH /api/links/{code}", s.authed("api", s.cfg.APILimit, s.updateLink))
	handle("DELETE /api/links/{code}", s.authed("api", s.cfg.APILimit, s.deleteLink))
	handle("GET /api/links/{code}/stats", s.authed("api", s.cfg.APILimit, s.linkStats))

	handle("GET /{code}", s.redirect)
	return securityHeaders(mux)
}

// ---- middleware ----------------------------------------------------------------------------------------------

type statusRecorder struct {
	http.ResponseWriter
	code int
}

func (r *statusRecorder) WriteHeader(code int) { r.code = code; r.ResponseWriter.WriteHeader(code) }

func instrument(route string, h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, code: 200}
		h.ServeHTTP(rec, r)
		metrics.HTTPDuration.WithLabelValues(route).Observe(time.Since(start).Seconds())
		metrics.HTTPRequests.WithLabelValues(route, strconv.Itoa(rec.code)).Inc()
	})
}

func securityHeaders(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		h.ServeHTTP(w, r)
	})
}

type ctxKey struct{}

func ownerFrom(ctx context.Context) int64 { id, _ := ctx.Value(ctxKey{}).(int64); return id }

// authed checks the Bearer API key, then applies the per-key rate limit for the given bucket.
func (s *Server) authed(bucket string, limit config.Limit, h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		key, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
		if !ok || key == "" {
			writeError(w, http.StatusUnauthorized, "unauthorized", "missing 'Authorization: Bearer <api key>' header")
			return
		}
		owner, err := s.st.Authenticate(r.Context(), strings.TrimSpace(key))
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, http.StatusUnauthorized, "unauthorized", "invalid or revoked API key")
			return
		}
		if err != nil {
			s.internalError(w, "authenticate", err)
			return
		}
		if !s.allow(w, r, bucket, strconv.FormatInt(owner, 10), limit) {
			return
		}
		h(w, r.WithContext(context.WithValue(r.Context(), ctxKey{}, owner)))
	}
}

// allow applies a rate limit and writes the X-RateLimit-* headers; on rejection it writes the 429 response.
func (s *Server) allow(w http.ResponseWriter, r *http.Request, bucket, id string, limit config.Limit) bool {
	d, err := s.limiter.Allow(r.Context(), bucket, id, limit.Rate, limit.Burst)
	if err != nil {
		metrics.RateLimitErrors.Inc()
		slog.Warn("rate limiter unavailable", "bucket", bucket, "err", err, "fail_open", s.cfg.RateLimitFailOpen)
		if s.cfg.RateLimitFailOpen {
			return true // availability over strictness: Redis trouble must not take redirects down
		}
		writeError(w, http.StatusServiceUnavailable, "unavailable", "rate limiter unavailable")
		return false
	}
	w.Header().Set("X-RateLimit-Limit", strconv.Itoa(d.Limit))
	w.Header().Set("X-RateLimit-Remaining", strconv.Itoa(max(d.Remaining, 0)))
	if d.Allowed {
		return true
	}
	metrics.RateLimited.WithLabelValues(bucket).Inc()
	w.Header().Set("Retry-After", strconv.Itoa(int(math.Ceil(d.RetryAfter.Seconds()))))
	writeError(w, http.StatusTooManyRequests, "rate_limited", "too many requests, retry after "+d.RetryAfter.Round(time.Millisecond).String())
	return false
}

// clientIP is the TCP peer, or X-Real-IP when running behind a trusted proxy that sets it.
func (s *Server) clientIP(r *http.Request) string {
	if s.cfg.TrustProxy {
		if ip, err := netip.ParseAddr(strings.TrimSpace(r.Header.Get("X-Real-IP"))); err == nil {
			return ip.String()
		}
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

// ---- operational ---------------------------------------------------------------------------------------------

func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), time.Second)
	defer cancel()
	status := map[string]string{"postgres": "ok", "redis": "ok"}
	code := http.StatusOK
	if err := s.st.Ping(ctx); err != nil {
		status["postgres"], code = err.Error(), http.StatusServiceUnavailable
	}
	if err := s.rdb.Ping(ctx).Err(); err != nil {
		status["redis"], code = err.Error(), http.StatusServiceUnavailable
	}
	writeJSON(w, code, status)
}

// ---- responses -----------------------------------------------------------------------------------------------

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{"error": map[string]string{"code": code, "message": message}})
}

func (s *Server) internalError(w http.ResponseWriter, op string, err error) {
	slog.Error("request failed", "op", op, "err", err)
	writeError(w, http.StatusInternalServerError, "internal", "internal error")
}
