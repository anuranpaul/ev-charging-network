package handlers

import (
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
)

// validLayerIDs is the set of layer identifiers the geo-service exposes via
// GET /layers/{layer_id}.  Kept in sync with the Python _LAYER_MAP in
// app/routers/layers.py.
var validLayerIDs = map[string]bool{
	"ev_chargers":    true,
	"fuel_stations":  true,
	"roads":          true,
	"parking":        true,
	"metro_stations": true,
	"malls":          true,
	"tech_parks":     true,
}

// LayersHandler proxies GET /layers/{layer_id}?city= to the geo service.
// Protected — requires X-API-Key.
//
// The Go mux pattern "/layers/" matches any path rooted at /layers/,
// so the handler extracts the layer_id by stripping the prefix.
//
// Validation:
//   - missing/unknown layer_id → 404 {"message": "...", "available": [...]}
//   - missing/unsupported city → 422 {"message": "...", "supported": [...]}
func LayersHandler(geoProxy *proxy.GeoClient) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		// Extract layer_id from the URL path: /layers/{layer_id}
		layerID := strings.TrimPrefix(r.URL.Path, "/layers/")
		layerID = strings.TrimSuffix(layerID, "/") // tolerate trailing slash
		layerID = strings.TrimSpace(layerID)

		if layerID == "" || !validLayerIDs[layerID] {
			available := make([]string, 0, len(validLayerIDs))
			for k := range validLayerIDs {
				available = append(available, k)
			}
			writeJSON(w, http.StatusNotFound, map[string]interface{}{
				"message":   fmt.Sprintf("Layer '%s' is not available.", layerID),
				"available": available,
			})
			return
		}

		city := strings.TrimSpace(r.URL.Query().Get("city"))
		if city == "" {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]interface{}{
				"message":   "city query parameter is required",
				"supported": SupportedCities,
			})
			return
		}

		normCity := normaliseCity(city)
		if !isSupportedCity(normCity) {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]interface{}{
				"message":   "City not supported.",
				"supported": SupportedCities,
			})
			return
		}

		upstream := fmt.Sprintf("%s/layers/%s?city=%s", geoProxy.BaseURL, layerID, normCity)
		// Layer serialisation (especially roads.geojson) can take several seconds.
		// Use a 30 s timeout instead of the default 3 s recommendation timeout.
		result := geoProxy.CallGETWithTimeout(r, upstream, 30*time.Second)

		if result.Err != nil || result.StatusCode >= 500 {
			proxy.Write503(w)
			return
		}

		// Forward upstream headers (Content-Type: application/json, etc.),
		// skipping hop-by-hop entries (RFC 7230 §6.1).
		proxy.CopyHeaders(w.Header(), result.Headers)
		w.WriteHeader(result.StatusCode)
		w.Write(result.Body)
	}
}
