// cmd/server/main.go — entry point: env validation, middleware chain, route registration.
// All handler logic lives in internal/handlers; middleware in internal/middleware (TODO).
package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"time"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/auth"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/cache"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/config"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/handlers"
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
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: 200}

		next.ServeHTTP(rec, r)

		entry := map[string]interface{}{
			"timestamp":      time.Now().UTC().Format(time.RFC3339),
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

	var handler http.Handler = mux
	handler = auth.APIKeyMiddleware(cfg.APIKey)(handler)
	handler = loggingMiddleware(handler)
	handler = correlationIDMiddleware(handler)

	log.Printf("chargewise-india api-go listening on :%s (geo_service_url=%s)", cfg.Port, cfg.GeoServiceURL)
	if err := http.ListenAndServe(":"+cfg.Port, handler); err != nil {
		log.Fatal(err)
	}
}
