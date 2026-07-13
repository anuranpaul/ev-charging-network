package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"time"
)

// healthClient is a dedicated HTTP client for the /health liveness probe.
// It carries its own transport timeout so a geo service that accepts the
// connection but stalls on the response body cannot hold a goroutine open
// beyond the context deadline.
var healthClient = &http.Client{
	Timeout: 3 * time.Second,
}

// HealthHandler checks the geo service is reachable within 2 s.
// Public — no auth required. Only GET is accepted.
func HealthHandler(geoServiceURL string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()

		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, geoServiceURL+"/data-health", nil)
		resp, err := healthClient.Do(req)

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
