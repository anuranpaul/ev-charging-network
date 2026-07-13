package handlers

import (
	"encoding/json"
	"net/http"
)

// CityInfo is the JSON shape returned by GET /cities.
type CityInfo struct {
	Name string `json:"name"`
	// BoundingBox intentionally omitted for MVP — plug in GeoJSON Polygon
	// per city (e.g. from compute_city_bbox.py output) once available.
}

// CitiesHandler returns the static city registry. Public — no auth required.
func CitiesHandler(w http.ResponseWriter, r *http.Request) {
	cities := make([]CityInfo, len(SupportedCities))
	for i, name := range SupportedCities {
		cities[i] = CityInfo{Name: name}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(cities)
}
