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

		normCity := normaliseCity(city)
		if !isSupportedCity(normCity) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnprocessableEntity)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"message":   "City not supported.",
				"supported": SupportedCities,
			})
			return
		}

		upstream := fmt.Sprintf("%s/chargers?city=%s", geoProxy.BaseURL, normCity)
		result := geoProxy.CallGET(r, upstream)

		if result.Err != nil || result.StatusCode >= 500 {
			proxy.Write503(w)
			return
		}

		for k, vv := range result.Headers {
			for _, v := range vv {
				w.Header().Add(k, v)
			}
		}
		w.WriteHeader(result.StatusCode)
		w.Write(result.Body)
	}
}
