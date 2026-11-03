package shortcode

import (
	"errors"
	"testing"
)

func TestNewIsBase62AndVaried(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 2000; i++ {
		c := New()
		if len(c) != Length || !LooksValid(c) {
			t.Fatalf("bad code %q", c)
		}
		seen[c] = true
	}
	if len(seen) != 2000 {
		t.Fatalf("expected 2000 distinct codes, got %d", len(seen))
	}
}

func TestValidateAlias(t *testing.T) {
	ok := []string{"docs", "Q4-report", "a_b", "abc", "x1234567890123456789012345678901"}
	for _, a := range ok {
		if err := ValidateAlias(a); err != nil {
			t.Errorf("%q: unexpected %v", a, err)
		}
	}
	bad := []string{"", "ab", "-start", "has space", "slash/x", "ünicode", "x12345678901234567890123456789012"}
	for _, a := range bad {
		if !errors.Is(ValidateAlias(a), ErrInvalidAlias) {
			t.Errorf("%q: expected ErrInvalidAlias", a)
		}
	}
	for _, a := range []string{"api", "API", "metrics", "healthz"} {
		if !errors.Is(ValidateAlias(a), ErrReservedAlias) {
			t.Errorf("%q: expected ErrReservedAlias", a)
		}
	}
}
