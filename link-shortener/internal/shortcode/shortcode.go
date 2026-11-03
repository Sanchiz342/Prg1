// Package shortcode generates random short codes and validates custom aliases.
package shortcode

import (
	"crypto/rand"
	"errors"
	"math/big"
	"regexp"
	"strings"
)

const (
	alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
	// Length 7 gives 62^7 ≈ 3.5e12 codes: at a million links the chance that a fresh code collides is ~3e-7,
	// and the store retries on the rare collision anyway.
	Length = 7
)

var (
	aliasRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$`)
	// Paths the server itself uses; a link with one of these codes would be unreachable.
	reserved = map[string]bool{
		"api": true, "app": true, "static": true, "healthz": true, "readyz": true, "metrics": true,
		"favicon.ico": true, "robots.txt": true, "admin": true, "login": true,
	}

	ErrInvalidAlias  = errors.New("alias must be 3-32 characters: letters, digits, '-' or '_', starting with a letter or digit")
	ErrReservedAlias = errors.New("alias is reserved")
)

// New returns a uniformly random base62 code (crypto/rand: codes are not guessable/enumerable).
func New() string {
	var b strings.Builder
	b.Grow(Length)
	max := big.NewInt(int64(len(alphabet)))
	for i := 0; i < Length; i++ {
		n, err := rand.Int(rand.Reader, max)
		if err != nil {
			panic("crypto/rand failed: " + err.Error())
		}
		b.WriteByte(alphabet[n.Int64()])
	}
	return b.String()
}

// ValidateAlias checks a user-chosen code.
func ValidateAlias(alias string) error {
	if !aliasRe.MatchString(alias) {
		return ErrInvalidAlias
	}
	if reserved[strings.ToLower(alias)] {
		return ErrReservedAlias
	}
	return nil
}

// LooksValid is a cheap pre-check for incoming redirect paths, so random junk never reaches cache or DB.
func LooksValid(code string) bool {
	return len(code) >= 3 && len(code) <= 32 && aliasRe.MatchString(code)
}
