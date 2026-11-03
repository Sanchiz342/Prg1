// Package analytics turns redirects into click events and moves them asynchronously into PostgreSQL:
//
//	redirect handler ──▶ in-process buffer ──▶ Redis Stream ──▶ consumer group (any instance) ──▶ PostgreSQL
//
// The redirect never waits for the database. The stream decouples request rate from write rate, survives app
// restarts, and lets every instance share the writing work through a consumer group.
package analytics

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

// Event is what one redirect produces. It carries no raw IP address.
type Event struct {
	ID       string    `json:"id"`
	LinkID   int64     `json:"link_id"`
	TS       time.Time `json:"ts"`
	Visitor  string    `json:"visitor"` // hex, see VisitorHash
	Country  string    `json:"country"`
	Referrer string    `json:"referrer"`
	Device   string    `json:"device"`
	Browser  string    `json:"browser"`
	Bot      bool      `json:"bot"`
}

// NewEventID returns a random UUIDv4 string.
func NewEventID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// VisitorHash identifies a visitor for unique counts without storing who they are: an HMAC of IP + User-Agent,
// keyed by a server secret and the UTC date. It rotates daily, so visitors can't be followed across days, and
// without the salt it can't be reversed by brute-forcing the IPv4 space.
func VisitorHash(salt, ip, userAgent string, ts time.Time) string {
	mac := hmac.New(sha256.New, []byte(salt))
	mac.Write([]byte(ts.UTC().Format("2006-01-02")))
	mac.Write([]byte{0})
	mac.Write([]byte(ip))
	mac.Write([]byte{0})
	mac.Write([]byte(userAgent))
	return hex.EncodeToString(mac.Sum(nil)[:16])
}

// ReferrerHost reduces a Referer header to its host ("" = direct / unknown).
func ReferrerHost(ref string) string {
	if ref == "" {
		return ""
	}
	u, err := url.Parse(ref)
	if err != nil || u.Host == "" {
		return ""
	}
	host := strings.ToLower(u.Hostname())
	return strings.TrimPrefix(host, "www.")
}

var countryRe = regexp.MustCompile(`^[A-Za-z]{2}$`)

// Country reads an ISO country code set by a trusted CDN / proxy (e.g. Cloudflare's CF-IPCountry).
// Without one it returns "ZZ" (unknown). A GeoIP database lookup could be plugged in here instead.
func Country(r *http.Request, header string, trusted bool) string {
	if !trusted || header == "" {
		return "ZZ"
	}
	v := strings.TrimSpace(r.Header.Get(header))
	if !countryRe.MatchString(v) || strings.EqualFold(v, "XX") || strings.EqualFold(v, "T1") {
		return "ZZ" // XX / T1 are Cloudflare's "unknown" and "Tor" markers
	}
	return strings.ToUpper(v)
}

var botRe = regexp.MustCompile(`(?i)bot|crawl|spider|slurp|preview|facebookexternalhit|embedly|curl/|wget/|` +
	`python-requests|httpclient|go-http-client|okhttp|headless|lighthouse|monitor`)

// ParseUA classifies a User-Agent with simple, explainable rules (no UA database dependency).
func ParseUA(ua string) (device, browser string, bot bool) {
	l := strings.ToLower(ua)
	switch {
	case ua == "":
		return "other", "other", false
	case botRe.MatchString(ua):
		return "bot", "bot", true
	}
	switch {
	case strings.Contains(l, "ipad") || strings.Contains(l, "tablet") || strings.Contains(l, "kindle") ||
		(strings.Contains(l, "android") && !strings.Contains(l, "mobile")):
		device = "tablet"
	case strings.Contains(l, "mobi") || strings.Contains(l, "iphone") || strings.Contains(l, "android"):
		device = "mobile"
	case strings.Contains(l, "windows") || strings.Contains(l, "macintosh") || strings.Contains(l, "x11") ||
		strings.Contains(l, "linux") || strings.Contains(l, "cros"):
		device = "desktop"
	default:
		device = "other"
	}
	switch {
	case strings.Contains(l, "edg/") || strings.Contains(l, "edga/") || strings.Contains(l, "edgios/"):
		browser = "edge"
	case strings.Contains(l, "opr/") || strings.Contains(l, "opera"):
		browser = "opera"
	case strings.Contains(l, "samsungbrowser/"):
		browser = "samsung"
	case strings.Contains(l, "firefox/") || strings.Contains(l, "fxios/"):
		browser = "firefox"
	case strings.Contains(l, "chrome/") || strings.Contains(l, "crios/"):
		browser = "chrome"
	case strings.Contains(l, "safari/"):
		browser = "safari"
	default:
		browser = "other"
	}
	return device, browser, false
}
