package urlcheck

import (
	"errors"
	"strings"
	"testing"
)

func TestValidate(t *testing.T) {
	cases := []struct {
		in   string
		want error
		norm string
	}{
		{"https://Example.com/Path?q=1#frag", nil, "https://example.com/Path?q=1#frag"},
		{"  http://go.dev  ", nil, "http://go.dev"},
		{"HTTPS://example.com", nil, "https://example.com"},
		{"https://[2001:db8::1]/x", nil, "https://[2001:db8::1]/x"},
		{"ftp://example.com", ErrInvalid, ""},
		{"javascript:alert(1)", ErrInvalid, ""},
		{"data:text/html,<b>x</b>", ErrInvalid, ""},
		{"/relative/path", ErrInvalid, ""},
		{"https://", ErrInvalid, ""},
		{"https://bank.example@evil.example/", ErrCredentials, ""},
		{"http://localhost:8080/admin", ErrPrivateHost, ""},
		{"http://127.0.0.1/", ErrPrivateHost, ""},
		{"http://10.1.2.3/", ErrPrivateHost, ""},
		{"http://192.168.0.1/", ErrPrivateHost, ""},
		{"http://169.254.169.254/latest/meta-data", ErrPrivateHost, ""},
		{"http://[::1]/", ErrPrivateHost, ""},
		{"http://[::ffff:127.0.0.1]/", ErrPrivateHost, ""},
		{"http://100.64.1.1/", ErrPrivateHost, ""},
		{"http://router/", ErrPrivateHost, ""},
		{"http://2130706433/", ErrPrivateHost, ""}, // decimal form of 127.0.0.1
		{"http://printer.local/", ErrPrivateHost, ""},
		{"https://sho.rt/abc", ErrSelfLink, ""},
		{"https://example.com/" + strings.Repeat("a", MaxLength), ErrTooLong, ""},
	}
	for _, c := range cases {
		got, err := Validate(c.in, "sho.rt")
		if !errors.Is(err, c.want) {
			t.Errorf("%q: err = %v, want %v", c.in, err, c.want)
			continue
		}
		if c.want == nil && got != c.norm {
			t.Errorf("%q: normalised to %q, want %q", c.in, got, c.norm)
		}
	}
}
