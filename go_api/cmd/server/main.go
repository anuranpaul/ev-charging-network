// main.go is a deliberately minimal, single-file starting point.
// As you build out internal/auth, internal/cache, internal/proxy,
// internal/middleware, internal/handlers per the design doc, move the
// corresponding pieces out of here into those packages. The comments
// below mark exactly what goes where.
package main

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/auth"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/cache"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/config"
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

// (auth middleware moved to internal/auth/apikey.go)

// ---- handlers/health.go candidate ------------------------------------------

func healthHandler(cfg config.Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()

		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, cfg.GeoServiceURL+"/data-health", nil)
		resp, err := http.DefaultClient.Do(req)

		geoReachable := err == nil && resp != nil && resp.StatusCode < 500
		if resp != nil {
			resp.Body.Close()
		}

		w.Header().Set("Content-Type", "application/json")
		if geoReachable {
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status":       "ok",
				"dependencies": map[string]string{"geo_service": "reachable"},
			})
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status":       "degraded",
				"dependencies": map[string]string{"geo_service": "unreachable"},
			})
		}
	}
}

// ---- handlers/cities.go candidate ------------------------------------------

type CityInfo struct {
	Name string `json:"name"`
	// BoundingBox intentionally omitted from this skeleton — plug in the
	// GeoJSON Polygon per city (e.g. from compute_city_bbox.py's output)
	// once you wire this up for real.
}

var supportedCities = []CityInfo{
	{Name: "Bengaluru"},
	{Name: "Mumbai"},
	{Name: "Hyderabad"},
	{Name: "Chennai"},
	{Name: "Pune"},
}

func citiesHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(supportedCities)
}

// (Proxy and cache logic wired below in main())

// ---- main -------------------------------------------------------------------

func main() {
	cfg := config.LoadConfig()

	geoProxy := proxy.NewGeoClient(cfg.GeoServiceURL, cfg.GeoServiceTimeoutSeconds)
	memCache := cache.NewMemoryCache(time.Duration(cfg.CacheTTLSeconds) * time.Second)

	mux := http.NewServeMux()
	mux.HandleFunc("/health", healthHandler(cfg))
	mux.HandleFunc("/cities", citiesHandler)
	mux.HandleFunc("/recommendation", recommendationHandler(geoProxy, memCache))

	var handler http.Handler = mux
	handler = auth.APIKeyMiddleware(cfg.APIKey)(handler)
	handler = loggingMiddleware(handler)
	handler = correlationIDMiddleware(handler)

	log.Printf("chargewise-india api-go listening on :%s (geo_service_url=%s)", cfg.Port, cfg.GeoServiceURL)
	if err := http.ListenAndServe(":"+cfg.Port, handler); err != nil {
		log.Fatal(err)
	}
}

// recommendationHandler implements the cache-aside pattern:
// check cache → on miss call geo service → store 2xx → return with X-Cache header.
func recommendationHandler(geoProxy *proxy.GeoClient, memCache *cache.MemoryCache) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		// Parse request body to derive cache key fields.
		body, err := io.ReadAll(r.Body)
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			w.Write([]byte(`{"message":"failed to read request body"}`))
			return
		}
		defer r.Body.Close()

		// Decode only the fields needed for the cache key.
		var req struct {
			City        string `json:"city"`
			ChargerType string `json:"chargerType"`
			Radius      int    `json:"radius"`
		}
		if err := json.Unmarshal(body, &req); err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			w.Write([]byte(`{"message":"invalid JSON body"}`))
			return
		}

		cacheKey := cache.NormaliseKey(req.City, req.ChargerType, req.Radius)

		// Cache hit path.
		if cached, hit := memCache.Get(cacheKey); hit {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("X-Cache", "HIT")
			w.WriteHeader(http.StatusOK)
			w.Write(cached)
			return
		}

		// Cache miss — call geo service.
		// Re-supply the original body to the proxy.
		r.Body = io.NopCloser(io.Reader(io.NopCloser(newBytesReader(body))))
		result := geoProxy.Call(r, body)

		if result.Err != nil {
			proxy.Write503(w)
			return
		}
		if result.StatusCode >= 500 {
			proxy.Write503(w)
			return
		}

		// Forward upstream headers.
		for k, vv := range result.Headers {
			for _, v := range vv {
				w.Header().Add(k, v)
			}
		}
		w.Header().Set("X-Cache", "MISS")

		// Store 2xx responses only.
		if result.StatusCode >= 200 && result.StatusCode < 300 {
			memCache.Set(cacheKey, result.Body)
		}

		w.WriteHeader(result.StatusCode)
		w.Write(result.Body)
	}
}

// newBytesReader is a tiny helper that keeps the import list clean.
func newBytesReader(b []byte) *bytesReader { return &bytesReader{b: b} }

type bytesReader struct{ b []byte; pos int }
func (r *bytesReader) Read(p []byte) (n int, err error) {
	if r.pos >= len(r.b) { return 0, io.EOF }
	n = copy(p, r.b[r.pos:])
	r.pos += n
	return
}
