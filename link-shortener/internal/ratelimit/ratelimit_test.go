package ratelimit

import (
	"context"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func testRedis(t *testing.T) (*redis.Client, string) {
	t.Helper()
	url := os.Getenv("SNIP_TEST_REDIS_URL")
	if url == "" {
		t.Skip("SNIP_TEST_REDIS_URL not set")
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		t.Fatal(err)
	}
	rdb := redis.NewClient(opts)
	t.Cleanup(func() { rdb.Close() })
	return rdb, fmt.Sprintf("test:%s:%d:", t.Name(), time.Now().UnixNano())
}

func TestBurstThenRefill(t *testing.T) {
	rdb, prefix := testRedis(t)
	l := New(rdb, prefix)
	ctx := context.Background()
	for i := 0; i < 5; i++ {
		d, err := l.Allow(ctx, "b", "id", 10, 5)
		if err != nil || !d.Allowed {
			t.Fatalf("request %d: %+v %v", i, d, err)
		}
		if d.Remaining != 4-i {
			t.Fatalf("request %d: remaining %d", i, d.Remaining)
		}
	}
	d, _ := l.Allow(ctx, "b", "id", 10, 5)
	if d.Allowed || d.RetryAfter <= 0 || d.RetryAfter > 100*time.Millisecond {
		t.Fatalf("6th request should be rejected with retry <= 100ms, got %+v", d)
	}
	time.Sleep(220 * time.Millisecond) // 10 tokens/s → ~2 tokens
	if d, _ := l.Allow(ctx, "b", "id", 10, 5); !d.Allowed {
		t.Fatal("bucket should have refilled")
	}
	if d, _ := l.Allow(ctx, "b", "other-id", 10, 5); !d.Allowed || d.Remaining != 4 {
		t.Fatalf("buckets must be independent, got %+v", d)
	}
}

// Many goroutines (standing in for many app instances) share one bucket: exactly `burst` requests may pass.
func TestAtomicUnderConcurrency(t *testing.T) {
	rdb, prefix := testRedis(t)
	l := New(rdb, prefix)
	var allowed atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if d, err := l.Allow(context.Background(), "b", "shared", 0.001, 50); err == nil && d.Allowed {
				allowed.Add(1)
			}
		}()
	}
	wg.Wait()
	if got := allowed.Load(); got != 50 {
		t.Fatalf("allowed %d, want exactly 50", got)
	}
}

func TestErrorWhenRedisDown(t *testing.T) {
	rdb := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1", DialTimeout: 100 * time.Millisecond, MaxRetries: -1})
	defer rdb.Close()
	if _, err := New(rdb, "x:").Allow(context.Background(), "b", "id", 1, 1); err == nil {
		t.Fatal("expected an error so the caller can apply its fail-open/closed policy")
	}
}
