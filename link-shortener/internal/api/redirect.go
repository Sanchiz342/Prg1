package api

import (
	"errors"
	"html/template"
	"net/http"

	"github.com/sanchiz342/prg1/link-shortener/internal/analytics"
	"github.com/sanchiz342/prg1/link-shortener/internal/metrics"
	"github.com/sanchiz342/prg1/link-shortener/internal/shortcode"
	"github.com/sanchiz342/prg1/link-shortener/internal/store"
)

var notice = template.Must(template.New("notice").Parse(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{.Title}}</title>
<style>body{font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;margin:0;
background:#f6f7f9;color:#1c2330}@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e6eaf0}}
main{text-align:center;padding:16px}h1{font-size:22px}</style></head>
<body><main><h1>{{.Title}}</h1><p>{{.Text}}</p></main></body></html>`))

func page(w http.ResponseWriter, status int, title, text string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = notice.Execute(w, map[string]string{"Title": title, "Text": text})
}

// GET /{code} — the hot path. One Redis round-trip for the rate limit, one for the cache (usually), no DB write.
func (s *Server) redirect(w http.ResponseWriter, r *http.Request) {
	code := r.PathValue("code")
	if !shortcode.LooksValid(code) {
		page(w, http.StatusNotFound, "Link not found", "This short link does not exist.")
		return
	}
	ip := s.clientIP(r)
	if !s.allow(w, r, "redirect", ip, s.cfg.RedirectLimit) {
		return
	}

	entry, result, err := s.cache.Get(r.Context(), code)
	metrics.CacheLookups.WithLabelValues(string(result)).Inc()
	switch {
	case errors.Is(err, store.ErrNotFound):
		page(w, http.StatusNotFound, "Link not found", "This short link does not exist.")
		return
	case err != nil:
		s.internalError(w, "resolve link", err)
		return
	case entry.Disabled:
		page(w, http.StatusGone, "Link disabled", "The owner has disabled this short link.")
		return
	case entry.Expired(s.now()):
		page(w, http.StatusGone, "Link expired", "This short link has expired.")
		return
	}

	// HEAD requests (uptime monitors, link checkers) are answered but not counted.
	if r.Method == http.MethodGet {
		now := s.now().UTC()
		ua := r.UserAgent()
		device, browser, bot := analytics.ParseUA(ua)
		s.pub.Publish(analytics.Event{
			ID:       analytics.NewEventID(),
			LinkID:   entry.ID,
			TS:       now,
			Visitor:  analytics.VisitorHash(s.cfg.VisitorSalt, ip, ua, now),
			Country:  analytics.Country(r, s.cfg.GeoHeader, s.cfg.TrustProxy),
			Referrer: analytics.ReferrerHost(r.Referer()),
			Device:   device,
			Browser:  browser,
			Bot:      bot,
		})
	}
	// 302 + no-store: browsers must come back to us every time, otherwise repeat clicks are invisible (a 301
	// would be cached forever and the link could never be edited or expired for that visitor).
	w.Header().Set("Cache-Control", "private, no-store")
	w.Header().Set("Referrer-Policy", "no-referrer-when-downgrade")
	http.Redirect(w, r, entry.URL, http.StatusFound)
}
