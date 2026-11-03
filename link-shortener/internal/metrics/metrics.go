// Package metrics defines the Prometheus metrics exposed on /metrics.
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	HTTPRequests = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "snip_http_requests_total", Help: "HTTP requests by route and status code.",
	}, []string{"route", "code"})

	HTTPDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name: "snip_http_request_duration_seconds", Help: "HTTP request latency by route.",
		Buckets: []float64{.0005, .001, .0025, .005, .01, .025, .05, .1, .25, .5, 1},
	}, []string{"route"})

	CacheLookups = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "snip_link_cache_total", Help: "Redirect lookups by cache result (hit, negative_hit, miss, bypass).",
	}, []string{"result"})

	RateLimited = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "snip_rate_limited_total", Help: "Requests rejected with 429, by bucket.",
	}, []string{"bucket"})

	RateLimitErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "snip_rate_limit_errors_total", Help: "Rate-limit checks that failed (Redis unavailable).",
	})

	ClicksPublished = promauto.NewCounter(prometheus.CounterOpts{
		Name: "snip_clicks_published_total", Help: "Click events appended to the Redis stream.",
	})

	ClicksDropped = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "snip_clicks_dropped_total", Help: "Click events lost, by reason.",
	}, []string{"reason"})

	ClicksWritten = promauto.NewCounter(prometheus.CounterOpts{
		Name: "snip_clicks_written_total", Help: "Click rows inserted into PostgreSQL (after de-duplication).",
	})

	StreamPending = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "snip_click_stream_pending", Help: "Delivered but unacknowledged entries in the click stream.",
	})
)
