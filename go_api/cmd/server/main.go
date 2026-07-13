// cmd/server/main.go — entry point: env validation, middleware chain, route
// registration, and graceful shutdown.
// All handler logic lives in internal/handlers; middleware in internal/middleware.
package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/auth"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/cache"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/config"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/handlers"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/middleware"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
	"github.com/google/uuid"
)

// ---- middleware/correlation.go candidate --------------------------------

type correlationIDKey struct{}

func correlationIDMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Correlation-ID")
		if id == "" || len(id) > 128 {
			id = uuid.NewString()
		}
		w.Header().Set("X-Correlation-ID", id)
		ctx := context.WithValue(r.Context(), correlationIDKey{}, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func correlationIDFromContext(ctx context.Context) string {
	if id, ok := ctx.Value(correlationIDKey{}).(string); ok {
		return id
	}
	return "MISSING"
}

// ---- middleware/logger.go candidate --------------------------------------

func loggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Capture arrival time before handing off to downstream handlers.
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: 200}

		next.ServeHTTP(rec, r)

		entry := map[string]interface{}{
			// timestamp = when the request arrived, not when the response was sent.
			"timestamp":      start.UTC().Format(time.RFC3339),
			"method":         r.Method,
			"path":           r.URL.Path,
			"status":         rec.status,
			"latency_ms":     time.Since(start).Milliseconds(),
			"correlation_id": correlationIDFromContext(r.Context()),
		}
		line, _ := json.Marshal(entry)
		log.Println(string(line))
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}

// ---- main -------------------------------------------------------------------

func main() {
	cfg := config.LoadConfig()

	geoProxy := proxy.NewGeoClient(cfg.GeoServiceURL, cfg.GeoServiceTimeoutSeconds)
	memCache := cache.NewMemoryCache(time.Duration(cfg.CacheTTLSeconds) * time.Second)

	mux := http.NewServeMux()
	mux.HandleFunc("/health", handlers.HealthHandler(cfg.GeoServiceURL))
	mux.HandleFunc("/cities", handlers.CitiesHandler)
	mux.HandleFunc("/chargers", handlers.ChargersHandler(geoProxy))
	mux.HandleFunc("/recommendation", handlers.RecommendationHandler(geoProxy, memCache))
	mux.HandleFunc("/analysis", handlers.AnalysisHandler(geoProxy))

	// Catch-all: return a JSON 404 instead of the default plain-text body so
	// API clients always receive a consistent JSON envelope.
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"message": "not found"})
	})

	// Middleware chain (outermost first):
	//   correlationID → logger → recovery → CORS → auth → mux
	//
	// Recovery sits inside correlationID/logger so that:
	//   - The correlation ID is already on the response header when the 500 is
	//     written (clients can correlate the error back to their request).
	//   - The access log entry is still emitted with the correct status code
	//     (statusRecorder captures the 500 written by RecoveryMiddleware).
	origins := middleware.ParseOrigins(cfg.CORSOrigins)
	var handler http.Handler = mux
	handler = auth.APIKeyMiddleware(cfg.APIKey)(handler)
	handler = middleware.CORSMiddleware(origins)(handler)
	handler = middleware.RecoveryMiddleware(handler)
	handler = loggingMiddleware(handler)
	handler = correlationIDMiddleware(handler)

	srv := &http.Server{
		Addr:    ":" + cfg.Port,
		Handler: handler,
		// ReadHeaderTimeout caps how long a client can take to send just the
		// request headers, closing the Slowloris attack surface before
		// ReadTimeout even starts.
		ReadHeaderTimeout: 5 * time.Second,
		// ReadTimeout covers the full read phase (headers + body).
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 15 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Start the server in a goroutine so main can proceed to the signal block.
	serverErr := make(chan error, 1)
	go func() {
		log.Printf(
			"chargewise-india api-go listening on :%s (geo_service_url=%s)",
			cfg.Port, cfg.GeoServiceURL,
		)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			serverErr <- err
		}
	}()

	// Block until a termination signal arrives or the server exits on its own.
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	select {
	case err := <-serverErr:
		log.Fatalf("server error: %v", err)
	case sig := <-quit:
		log.Printf("received signal %s — shutting down gracefully", sig)
	}

	// Give in-flight requests up to 10 s to complete before forcefully closing.
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Fatalf("graceful shutdown failed: %v", err)
	}
	log.Println("server stopped cleanly")
}
