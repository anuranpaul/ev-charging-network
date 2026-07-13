package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"time"
)

// HealthHandler checks the geo service is reachable within 2 s.
// Public — no auth required.
func HealthHandler(geoServiceURL string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()

		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, geoServiceURL+"/data-health", nil)
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
