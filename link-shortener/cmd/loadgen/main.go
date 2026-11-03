// Command loadgen creates links through the API and replays realistic redirect traffic against them, then prints
// throughput and latency percentiles. It doubles as a demo-data generator for the dashboard.
//
// The target must run with TRUST_PROXY=true so the synthetic X-Real-IP / CF-IPCountry headers are honoured
// (otherwise every request comes from one IP and hits the per-IP rate limit, which is also worth seeing).
//
//	go run ./cmd/loadgen -base http://localhost:8080 -key snip_... -links 20 -duration 15s -c 64
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"os"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

var (
	countries = []string{"UA", "UA", "UA", "UA", "PL", "PL", "DE", "DE", "US", "US", "GB", "CZ", "CA", "FR", "XX"}
	referrers = []string{"", "", "https://t.me/", "https://www.google.com/", "https://www.google.com/",
		"https://www.reddit.com/r/golang/", "https://twitter.com/", "https://news.ycombinator.com/", "https://www.linkedin.com/feed/"}
	agents = []string{
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0",
		"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
		"Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
		"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
		"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
		"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36",
		"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36",
		"Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
		"Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
		"TelegramBot (like TwitterBot)",
	}
)

func main() {
	base := flag.String("base", "http://localhost:8080", "shortener base URL")
	key := flag.String("key", os.Getenv("SNIP_API_KEY"), "API key used to create links")
	nLinks := flag.Int("links", 20, "links to create")
	duration := flag.Duration("duration", 10*time.Second, "how long to send redirect traffic")
	conc := flag.Int("c", 32, "concurrent workers")
	ips := flag.Int("ips", 20000, "distinct synthetic client IPs")
	missRatio := flag.Float64("miss", 0.02, "share of requests for codes that don't exist")
	flag.Parse()
	if *key == "" {
		fmt.Fprintln(os.Stderr, "-key (or SNIP_API_KEY) is required")
		os.Exit(2)
	}

	codes, err := createLinks(*base, *key, *nLinks)
	if err != nil {
		fmt.Fprintln(os.Stderr, "create links:", err)
		os.Exit(1)
	}
	fmt.Printf("created %d links, sending traffic for %s with %d workers...\n", len(codes), *duration, *conc)

	client := &http.Client{
		Timeout:       5 * time.Second,
		Transport:     &http.Transport{MaxIdleConns: *conc * 2, MaxIdleConnsPerHost: *conc * 2},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	var (
		mu        sync.Mutex
		latencies []time.Duration
		statuses  = map[int]int{}
		errs      atomic.Int64
		wg        sync.WaitGroup
	)
	deadline := time.Now().Add(*duration)
	start := time.Now()
	for w := 0; w < *conc; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			local := make([]time.Duration, 0, 4096)
			localStatus := map[int]int{}
			for time.Now().Before(deadline) {
				code := pick(codes)
				if rand.Float64() < *missRatio {
					code = fmt.Sprintf("zz%06d", rand.IntN(1_000_000))
				}
				req, _ := http.NewRequest(http.MethodGet, *base+"/"+code, nil)
				ip := rand.IntN(*ips)
				req.Header.Set("X-Real-IP", fmt.Sprintf("10.%d.%d.%d", ip>>16&255, ip>>8&255, ip&255))
				req.Header.Set("CF-IPCountry", countries[rand.IntN(len(countries))])
				req.Header.Set("User-Agent", agents[rand.IntN(len(agents))])
				if ref := referrers[rand.IntN(len(referrers))]; ref != "" {
					req.Header.Set("Referer", ref)
				}
				t0 := time.Now()
				resp, err := client.Do(req)
				if err != nil {
					errs.Add(1)
					continue
				}
				_, _ = io.Copy(io.Discard, resp.Body)
				resp.Body.Close()
				local = append(local, time.Since(t0))
				localStatus[resp.StatusCode]++
			}
			mu.Lock()
			latencies = append(latencies, local...)
			for k, v := range localStatus {
				statuses[k] += v
			}
			mu.Unlock()
		}()
	}
	wg.Wait()
	elapsed := time.Since(start)

	sort.Slice(latencies, func(i, j int) bool { return latencies[i] < latencies[j] })
	pct := func(p float64) time.Duration {
		if len(latencies) == 0 {
			return 0
		}
		return latencies[int(float64(len(latencies)-1)*p)]
	}
	fmt.Printf("\nrequests   %d in %s (%.0f req/s), transport errors %d\n",
		len(latencies), elapsed.Round(time.Millisecond), float64(len(latencies))/elapsed.Seconds(), errs.Load())
	fmt.Printf("latency    p50 %s  p90 %s  p99 %s  max %s\n", pct(.5), pct(.9), pct(.99), pct(1))
	keys := make([]int, 0, len(statuses))
	for k := range statuses {
		keys = append(keys, k)
	}
	sort.Ints(keys)
	fmt.Print("statuses  ")
	for _, k := range keys {
		fmt.Printf(" %d×%d", k, statuses[k])
	}
	fmt.Println()
}

// pick favours the first links (rough power law), like real traffic where a few links get most clicks.
func pick(codes []string) string {
	i := int(float64(len(codes)) * rand.Float64() * rand.Float64())
	return codes[i]
}

func createLinks(base, key string, n int) ([]string, error) {
	codes := make([]string, 0, n)
	targets := []string{"https://go.dev/doc/", "https://www.postgresql.org/docs/", "https://redis.io/docs/",
		"https://github.com/", "https://en.wikipedia.org/wiki/URL_shortening"}
	for i := 0; i < n; i++ {
		body, _ := json.Marshal(map[string]any{"url": fmt.Sprintf("%s?ref=loadgen&i=%d", targets[i%len(targets)], i)})
		req, _ := http.NewRequest(http.MethodPost, base+"/api/links", bytes.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+key)
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return nil, err
		}
		var out struct {
			Code  string                    `json:"code"`
			Error *struct{ Message string } `json:"error"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&out)
		resp.Body.Close()
		if resp.StatusCode != http.StatusCreated {
			if resp.StatusCode == http.StatusTooManyRequests {
				return nil, fmt.Errorf("rate limited after %d links (raise RL_CREATE_BURST for load tests)", i)
			}
			msg := resp.Status
			if out.Error != nil {
				msg = out.Error.Message
			}
			return nil, fmt.Errorf("HTTP %s", msg)
		}
		codes = append(codes, out.Code)
	}
	return codes, nil
}
