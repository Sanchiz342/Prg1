// Package urlcheck validates target URLs before they are shortened.
//
// The service never fetches the target (it only redirects to it), so this is not about SSRF; it is about abuse:
// no javascript:/data: schemes, no credentials-in-URL phishing tricks ("https://bank.com@evil.example"), no links into
// the visitor's own network (router admin pages, localhost services), and no redirect loops through ourselves.
package urlcheck

import (
	"errors"
	"net/netip"
	"net/url"
	"strings"
)

const MaxLength = 2048

var (
	ErrTooLong     = errors.New("url is longer than 2048 characters")
	ErrInvalid     = errors.New("url must be an absolute http(s) URL")
	ErrCredentials = errors.New("url must not contain a username or password")
	ErrPrivateHost = errors.New("url points to a local or private network address")
	ErrSelfLink    = errors.New("url points to this shortener")
)

// Validate returns the normalised URL or an error explaining why it is rejected.
func Validate(raw string, selfHost string) (string, error) {
	raw = strings.TrimSpace(raw)
	if len(raw) > MaxLength {
		return "", ErrTooLong
	}
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.Opaque != "" {
		return "", ErrInvalid
	}
	if u.User != nil {
		return "", ErrCredentials
	}
	host := strings.ToLower(strings.TrimSuffix(u.Hostname(), "."))
	if host == "" {
		return "", ErrInvalid
	}
	if isPrivateHost(host) {
		return "", ErrPrivateHost
	}
	if selfHost != "" && strings.EqualFold(u.Host, selfHost) {
		return "", ErrSelfLink
	}
	u.Scheme = strings.ToLower(u.Scheme)
	u.Host = strings.ToLower(u.Host)
	return u.String(), nil
}

func isPrivateHost(host string) bool {
	if host == "localhost" || strings.HasSuffix(host, ".localhost") || strings.HasSuffix(host, ".local") ||
		strings.HasSuffix(host, ".internal") || !strings.Contains(host, ".") && !strings.Contains(host, ":") {
		return true // single-label names ("intranet", "router") only resolve inside a private network
	}
	addr, err := netip.ParseAddr(strings.Trim(host, "[]"))
	if err != nil {
		return false // a domain name
	}
	addr = addr.Unmap()
	return addr.IsLoopback() || addr.IsPrivate() || addr.IsLinkLocalUnicast() || addr.IsLinkLocalMulticast() ||
		addr.IsUnspecified() || addr.IsMulticast() || cgnat.Contains(addr)
}

var cgnat = netip.MustParsePrefix("100.64.0.0/10")
