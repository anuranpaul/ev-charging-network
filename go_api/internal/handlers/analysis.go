package handlers

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
)

// validChargerTypes is the set of charger type strings accepted by the geo
// service.  Kept in sync with the Pydantic ChargerType enum.
var validChargerTypes = map[string]bool{
	"DC_FAST": true,
	"AC_SLOW": true,
	"AC_FAST": true,
}

// AnalysisHandler proxies GET /analysis?city=&chargerType= to the geo service.
// Protected — requires X-API-Key.
//
// Validation:
//   - unsupported city        → 422 {"message": "...", "supported": [...]}
//   - unsupported chargerType → 422 {"message": "...", "supported": [...]}
func AnalysisHandler(geoProxy *proxy.GeoClient) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		city := strings.TrimSpace(r.URL.Query().Get("city"))
		chargerType := strings.TrimSpace(r.URL.Query().Get("chargerType"))

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

		normType := strings.ToUpper(chargerType)
		if chargerType == "" || !validChargerTypes[normType] {
			supported := make([]string, 0, len(validChargerTypes))
			for k := range validChargerTypes {
				supported = append(supported, k)
			}
			writeJSON(w, http.StatusUnprocessableEntity, map[string]interface{}{
				"message":   "chargerType not supported.",
				"supported": supported,
			})
			return
		}

		upstream := fmt.Sprintf("%s/analysis?city=%s&chargerType=%s", geoProxy.BaseURL, normCity, normType)
		result := geoProxy.CallGET(r, upstream)

		if result.Err != nil || result.StatusCode >= 500 {
			proxy.Write503(w)
			return
		}

		// Translate any 422 from geo service into our canonical envelope.
		if result.StatusCode == http.StatusUnprocessableEntity {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]interface{}{
				"message":   "City or charger type not supported.",
				"supported": SupportedCities,
			})
			return
		}

		// Forward upstream headers, skipping hop-by-hop entries (RFC 7230 §6.1).
		proxy.CopyHeaders(w.Header(), result.Headers)
		w.WriteHeader(result.StatusCode)
		w.Write(result.Body)
	}
}
