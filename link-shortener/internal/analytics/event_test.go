package analytics

import (
	"net/http/httptest"
	"regexp"
	"testing"
	"time"
)

func TestParseUA(t *testing.T) {
	cases := []struct {
		ua, device, browser string
		bot                 bool
	}{
		{"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36", "desktop", "chrome", false},
		{"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0", "desktop", "edge", false},
		{"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15", "desktop", "safari", false},
		{"Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0", "desktop", "firefox", false},
		{"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1", "mobile", "safari", false},
		{"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1", "mobile", "chrome", false},
		{"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36", "mobile", "chrome", false},
		{"Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36", "tablet", "chrome", false},
		{"Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "tablet", "safari", false},
		{"Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "bot", "bot", true},
		{"facebookexternalhit/1.1", "bot", "bot", true},
		{"curl/8.5.0", "bot", "bot", true},
		{"", "other", "other", false},
	}
	for _, c := range cases {
		d, b, bot := ParseUA(c.ua)
		if d != c.device || b != c.browser || bot != c.bot {
			t.Errorf("%q => (%s, %s, %v), want (%s, %s, %v)", c.ua, d, b, bot, c.device, c.browser, c.bot)
		}
	}
}

func TestReferrerHost(t *testing.T) {
	for in, want := range map[string]string{
		"":                                  "",
		"https://www.Google.com/search?q=x": "google.com",
		"https://t.me/channel":              "t.me",
		"not a url":                         "",
		"android-app://com.slack":           "com.slack",
	} {
		if got := ReferrerHost(in); got != want {
			t.Errorf("ReferrerHost(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestCountryOnlyFromTrustedHeader(t *testing.T) {
	r := httptest.NewRequest("GET", "/x", nil)
	r.Header.Set("CF-IPCountry", "ua")
	if got := Country(r, "CF-IPCountry", false); got != "ZZ" {
		t.Fatalf("untrusted header must be ignored, got %s", got)
	}
	if got := Country(r, "CF-IPCountry", true); got != "UA" {
		t.Fatalf("got %s", got)
	}
	for _, v := range []string{"XX", "T1", "USA", "1", "<script>"} {
		r.Header.Set("CF-IPCountry", v)
		if got := Country(r, "CF-IPCountry", true); got != "ZZ" {
			t.Errorf("%q => %s, want ZZ", v, got)
		}
	}
}

func TestVisitorHashRotatesDailyAndHidesIP(t *testing.T) {
	day1 := time.Date(2026, 11, 1, 10, 0, 0, 0, time.UTC)
	a := VisitorHash("salt", "203.0.113.7", "UA", day1)
	if a != VisitorHash("salt", "203.0.113.7", "UA", day1.Add(5*time.Hour)) {
		t.Fatal("same visitor on the same day must hash equally")
	}
	if a == VisitorHash("salt", "203.0.113.7", "UA", day1.Add(24*time.Hour)) {
		t.Fatal("hash must rotate daily")
	}
	if a == VisitorHash("other-salt", "203.0.113.7", "UA", day1) {
		t.Fatal("hash must depend on the salt")
	}
	if !regexp.MustCompile(`^[0-9a-f]{32}$`).MatchString(a) {
		t.Fatalf("unexpected format %q", a)
	}
}

func TestNewEventIDIsUUIDv4(t *testing.T) {
	re := regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	for i := 0; i < 100; i++ {
		if id := NewEventID(); !re.MatchString(id) {
			t.Fatalf("bad uuid %q", id)
		}
	}
}
