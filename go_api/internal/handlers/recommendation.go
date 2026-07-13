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

// fieldError is a single field-level validation error for the errors array.
type fieldError struct {
	Field   string `json:"field"`
	Message string `json:"message"`
}

// RecommendationHandler implements cache-aside:
//
//	check cache → miss → proxy → store 2xx → return with X-Cache header.
//
// Protected — requires X-API-Key.
//
// Validation contract (Req 4 AC-3/AC-4):
//   - Missing/invalid field(s)  → 400 {"errors": [...]}
//   - Unsupported city          → 422 {"message": "...", "supported": [...]}
func RecommendationHandler(geoProxy *proxy.GeoClient, memCache *cache.MemoryCache) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		// Cap incoming body to 1 MB — a valid recommendation request is under
		// 200 bytes; anything larger is malformed or malicious.
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
		body, err := io.ReadAll(r.Body)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]interface{}{
				"errors": []fieldError{{Field: "body", Message: "failed to read request body"}},
			})
			return
		}
		defer r.Body.Close()

		var req recommendationRequest
		if err := json.Unmarshal(body, &req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]interface{}{
				"errors": []fieldError{{Field: "body", Message: "invalid JSON body"}},
			})
			return
		}

		// Collect all field-level validation errors before returning.
		var errs []fieldError

		if strings.TrimSpace(req.City) == "" {
			errs = append(errs, fieldError{Field: "city", Message: "city is required"})
		}
		if strings.TrimSpace(req.ChargerType) == "" {
			errs = append(errs, fieldError{Field: "chargerType", Message: "chargerType is required"})
		}
		// Design doc Req 4 AC-3: valid range is 250–10000 m.
		if req.Radius < 250 || req.Radius > 10000 {
			errs = append(errs, fieldError{
				Field:   "radius",
				Message: "radius must be between 250 and 10000 metres",
			})
		}

		if len(errs) > 0 {
			writeJSON(w, http.StatusBadRequest, map[string]interface{}{"errors": errs})
			return
		}

		// Validate city against canonical registry (Req 4 AC-4).
		normCity := normaliseCity(req.City)
		if !isSupportedCity(normCity) {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]interface{}{
				"message":   "City not supported.",
				"supported": SupportedCities,
			})
			return
		}

		cacheKey := cache.NormaliseKey(normCity, req.ChargerType, req.Radius)

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

		// Translate any 422 that leaks up from the geo service into our
		// canonical format so the API contract is consistent regardless of
		// what the upstream returns.
		if result.StatusCode == http.StatusUnprocessableEntity {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]interface{}{
				"message":   "City not supported.",
				"supported": SupportedCities,
			})
			return
		}

		// Forward upstream headers, skipping hop-by-hop entries (RFC 7230 §6.1).
		proxy.CopyHeaders(w.Header(), result.Headers)
		w.Header().Set("X-Cache", "MISS")

		if result.StatusCode >= 200 && result.StatusCode < 300 {
			memCache.Set(cacheKey, result.Body)
		}

		w.WriteHeader(result.StatusCode)
		w.Write(result.Body)
	}
}

// normaliseCity returns the city name title-cased for canonical comparison
// (e.g. "BENGALURU" and "bengaluru" both become "Bengaluru").
func normaliseCity(city string) string {
	city = strings.TrimSpace(city)
	if city == "" {
		return ""
	}
	return strings.ToUpper(city[:1]) + strings.ToLower(city[1:])
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
