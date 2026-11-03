// Package ratelimit is a distributed token-bucket rate limiter on Redis.
//
// The whole check-and-update runs as one Lua script, so it is atomic across any number of app instances, and it
// reads the clock from Redis (TIME) rather than from each instance, so clock skew between servers can't hand out
// extra tokens.
package ratelimit

import (
	"context"
	"math"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// KEYS[1] = bucket; ARGV = rate (tokens/s), burst, cost. Returns {allowed, remaining_tokens, retry_after_ms}.
var script = redis.NewScript(`
local rate  = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local cost  = tonumber(ARGV[3])
local t     = redis.call('TIME')
local now   = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

local state  = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts     = tonumber(state[2])
if tokens == nil then
  tokens = burst
  ts = now
end
tokens = math.min(burst, tokens + math.max(0, now - ts) / 1000 * rate)

local allowed, retry = 0, 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry = math.ceil((cost - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(burst / rate * 1000) + 1000)
return {allowed, tostring(tokens), retry}
`)

type Decision struct {
	Allowed    bool
	Limit      int
	Remaining  int
	RetryAfter time.Duration
}

type Limiter struct {
	rdb    *redis.Client
	prefix string
}

func New(rdb *redis.Client, prefix string) *Limiter {
	return &Limiter{rdb: rdb, prefix: prefix + "rl:"}
}

// Allow takes one token from bucket `name:id`.
func (l *Limiter) Allow(ctx context.Context, name, id string, rate float64, burst int) (Decision, error) {
	res, err := script.Run(ctx, l.rdb, []string{l.prefix + name + ":" + id}, rate, burst, 1).Slice()
	if err != nil {
		return Decision{Allowed: true, Limit: burst}, err
	}
	allowed, _ := res[0].(int64)
	tokens, _ := strconv.ParseFloat(res[1].(string), 64)
	retry, _ := res[2].(int64)
	return Decision{
		Allowed:    allowed == 1,
		Limit:      burst,
		Remaining:  int(math.Floor(tokens)),
		RetryAfter: time.Duration(retry) * time.Millisecond,
	}, nil
}
