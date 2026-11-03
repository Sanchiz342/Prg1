// Command snip runs the URL shortener.
//
//	snip serve              start the HTTP server and the click writer (default)
//	snip migrate            apply database migrations and exit
//	snip create-key NAME    create an API key and print it (shown only once)
//	snip healthcheck        exit 0 if the local server answers /healthz (for container health checks)
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/sanchiz342/prg1/link-shortener/internal/analytics"
	"github.com/sanchiz342/prg1/link-shortener/internal/api"
	"github.com/sanchiz342/prg1/link-shortener/internal/config"
	"github.com/sanchiz342/prg1/link-shortener/internal/store"
)

func main() {
	if os.Getenv("LOG_FORMAT") == "json" {
		slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stderr, nil)))
	}
	cmd := "serve"
	if len(os.Args) > 1 {
		cmd = os.Args[1]
	}
	var err error
	switch cmd {
	case "serve":
		err = serve()
	case "migrate":
		err = migrate()
	case "healthcheck":
		err = healthcheck()
	case "create-key":
		if len(os.Args) < 3 {
			err = errors.New("usage: snip create-key NAME")
			break
		}
		err = createKey(os.Args[2])
	default:
		err = fmt.Errorf("unknown command %q (serve | migrate | create-key NAME)", cmd)
	}
	if err != nil {
		slog.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func healthcheck() error {
	addr := os.Getenv("HTTP_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	if strings.HasPrefix(addr, ":") {
		addr = "127.0.0.1" + addr
	}
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get("http://" + addr + "/healthz")
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("healthz returned %d", resp.StatusCode)
	}
	return nil
}

func openStore(ctx context.Context, cfg *config.Config) (*store.Store, error) {
	st, err := store.Open(ctx, cfg.DatabaseURL)
	if err != nil {
		return nil, err
	}
	if err := st.Migrate(ctx); err != nil {
		st.Close()
		return nil, fmt.Errorf("migrate: %w", err)
	}
	return st, nil
}

func migrate() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	st, err := openStore(ctx, cfg)
	if err != nil {
		return err
	}
	st.Close()
	fmt.Println("migrations applied")
	return nil
}

func createKey(name string) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	st, err := openStore(ctx, cfg)
	if err != nil {
		return err
	}
	defer st.Close()
	key, id, err := st.NewAPIKey(ctx, name)
	if err != nil {
		return err
	}
	fmt.Printf("API key #%d (%s): %s\nStore it now — only its hash is kept.\n", id, name, key)
	return nil
}

func serve() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	startCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	st, err := openStore(startCtx, cfg)
	if err != nil {
		return err
	}
	defer st.Close()

	ropts, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		return fmt.Errorf("parse REDIS_URL: %w", err)
	}
	rdb := redis.NewClient(ropts)
	defer rdb.Close()
	if err := rdb.Ping(startCtx).Err(); err != nil {
		// Not fatal: redirects keep working from PostgreSQL while Redis is down.
		slog.Warn("redis is not reachable at startup", "err", err)
	}

	if cfg.BootstrapKey != "" {
		if _, err := st.ImportAPIKey(startCtx, cfg.BootstrapName, cfg.BootstrapKey); err != nil {
			return fmt.Errorf("bootstrap api key: %w", err)
		}
	}

	pub := analytics.NewPublisher(rdb, cfg.RedisPrefix, cfg.EventBuffer, cfg.StreamMaxLen)
	workerCtx, stopWorker := context.WithCancel(context.Background())
	defer stopWorker()
	var workers sync.WaitGroup
	if cfg.WorkerEnabled {
		workers.Add(1)
		go func() {
			defer workers.Done()
			analytics.NewWriter(rdb, st, pub.Stream()).Run(workerCtx)
		}()
	}

	srv := &http.Server{
		Addr:              cfg.HTTPAddr,
		Handler:           api.New(cfg, st, rdb, pub).Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	errCh := make(chan error, 1)
	go func() {
		slog.Info("listening", "addr", cfg.HTTPAddr, "base_url", cfg.BaseURL.String(), "worker", cfg.WorkerEnabled)
		errCh <- srv.ListenAndServe()
	}()

	select {
	case err := <-errCh:
		if !errors.Is(err, http.ErrServerClosed) {
			pub.Close()
			stopWorker()
			workers.Wait()
			return err
		}
	case <-ctx.Done():
	}

	// Shutdown order matters: stop accepting requests, flush buffered click events to Redis, then let the
	// writer finish its current batch.
	slog.Info("shutting down")
	shutdownCtx, cancelShutdown := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancelShutdown()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		slog.Warn("http shutdown", "err", err)
	}
	pub.Close()
	stopWorker()
	workers.Wait()
	slog.Info("stopped")
	return nil
}
