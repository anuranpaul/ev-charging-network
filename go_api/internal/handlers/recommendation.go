package handlers

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/cache"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
)

// recommendationRequest mirrors the fields needed for cache-key derivation
// and city validation; the full body is forwarded as-is to the geo service.
type recommendationRequest struct {
	City        string `json:"city"`
	ChargerType string `json:"chargerType"`
	Radius      int    `json:"radius"`
}

// RecommendationHandler implements cache-aside:
//
//	check cache → miss → proxy → store 2xx → return with X-Cache header.
//
// Protected — requires X-API-Key.
func RecommendationHandler(geoProxy *proxy.GeoClient, memCache *cache.MemoryCache) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		body, err := io.ReadAll(r.Body)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"message": "failed to read request body"})
			return
		}
		defer r.Body.Close()

		var req recommendationRequest
		if err := json.Unmarshal(body, &req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"message": "invalid JSON body"})
			return
		}

		// Validate radius (geo service constraint: 100–5000 m from design doc).
		if req.Radius < 100 || req.Radius > 5000 {
			writeJSON(w, http.StatusBadRequest, map[string]interface{}{
				"message": "radius must be between 100 and 5000 metres",
			})
			return
		}

		// Validate city against canonical registry.
		normCity := strings.ToTitle(req.City[:1]) + strings.ToLower(req.City[1:])
		if !isSupportedCity(normCity) {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]interface{}{
				"message":   "City not supported.",
				"supported": SupportedCities,
			})
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
		result := geoProxy.Call(r, body)

		if result.Err != nil {
			proxy.Write503(w)
			return
		}
		if result.StatusCode >= 500 {
			proxy.Write503(w)
			return
		}

		for k, vv := range result.Headers {
			for _, v := range vv {
				w.Header().Add(k, v)
			}
		}
		w.Header().Set("X-Cache", "MISS")

		if result.StatusCode >= 200 && result.StatusCode < 300 {
			memCache.Set(cacheKey, result.Body)
		}

		w.WriteHeader(result.StatusCode)
		w.Write(result.Body)
	}
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
