package handlers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
)

// ChargersHandler proxies GET /chargers?city= to the geo service.
// Protected — requires X-API-Key.
func ChargersHandler(geoProxy *proxy.GeoClient) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		city := strings.TrimSpace(r.URL.Query().Get("city"))
		if city == "" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnprocessableEntity)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"message":   "city query parameter is required",
				"supported": SupportedCities,
			})
			return
		}

		if !isSupportedCity(city) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnprocessableEntity)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"message":   "City not supported.",
				"supported": SupportedCities,
			})
			return
		}

		// Build request to geo service GET /chargers?city=<city>
		upstream := fmt.Sprintf("%s/chargers?city=%s", geoProxy.BaseURL, city)
		req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, upstream, nil)
		if err != nil {
			proxy.Write503(w)
			return
		}
		if corrID := r.Header.Get("X-Correlation-ID"); corrID != "" {
			req.Header.Set("X-Correlation-ID", corrID)
		}

		resp, err := geoProxy.HTTPClient.Do(req)
		if err != nil || resp.StatusCode >= 500 {
			if resp != nil {
				resp.Body.Close()
			}
			proxy.Write503(w)
			return
		}
		defer resp.Body.Close()

		for k, vv := range resp.Header {
			for _, v := range vv {
				w.Header().Add(k, v)
			}
		}
		w.WriteHeader(resp.StatusCode)

		buf := make([]byte, 32*1024)
		for {
			n, readErr := resp.Body.Read(buf)
			if n > 0 {
				w.Write(buf[:n])
			}
			if readErr != nil {
				break
			}
		}
	}
}
