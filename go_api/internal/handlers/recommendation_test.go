package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/cache"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
)

// mockGeoService creates a test HTTP server that returns the given status and body.
func mockGeoService(t *testing.T, status int, body string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		w.Write([]byte(body))
	}))
}

func newTestHandler(geoURL string) http.HandlerFunc {
	geoProxy := proxy.NewGeoClient(geoURL, 3)
	memCache := cache.NewMemoryCache(5 * time.Minute)
	return RecommendationHandler(geoProxy, memCache)
}

// postRecommendation is a test helper for building POST /recommendation requests.
func postRecommendation(t *testing.T, payload interface{}) *http.Request {
	t.Helper()
	body, _ := json.Marshal(payload)
	r, err := http.NewRequest(http.MethodPost, "/recommendation", bytes.NewReader(body))
	if err != nil {
		t.Fatalf("could not create request: %v", err)
	}
	r.Header.Set("Content-Type", "application/json")
	return r
}

// hasErrors returns true when the response body contains an "errors" array.
func hasErrors(t *testing.T, body *bytes.Buffer) bool {
	t.Helper()
	var resp map[string]interface{}
	if err := json.NewDecoder(bytes.NewReader(body.Bytes())).Decode(&resp); err != nil {
		return false
	}
	_, ok := resp["errors"]
	return ok
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

func TestRecommendation_ValidRequest(t *testing.T) {
	geoBody := `{"type":"FeatureCollection","features":[]}`
	srv := mockGeoService(t, http.StatusOK, geoBody)
	defer srv.Close()

	handler := newTestHandler(srv.URL)
	req := postRecommendation(t, map[string]interface{}{
		"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 1500,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if rec.Header().Get("X-Cache") != "MISS" {
		t.Errorf("expected X-Cache: MISS on first request, got %q", rec.Header().Get("X-Cache"))
	}
	if ct := rec.Header().Get("Content-Type"); ct == "" {
		t.Error("expected Content-Type header to be set")
	}
}

func TestRecommendation_RadiusBoundary_MinValid(t *testing.T) {
	srv := mockGeoService(t, http.StatusOK, `{"type":"FeatureCollection","features":[]}`)
	defer srv.Close()

	req := postRecommendation(t, map[string]interface{}{
		"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 250,
	})
	rec := httptest.NewRecorder()
	newTestHandler(srv.URL)(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("radius=250 (boundary): expected 200, got %d", rec.Code)
	}
}

func TestRecommendation_RadiusBoundary_MaxValid(t *testing.T) {
	srv := mockGeoService(t, http.StatusOK, `{"type":"FeatureCollection","features":[]}`)
	defer srv.Close()

	req := postRecommendation(t, map[string]interface{}{
		"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 10000,
	})
	rec := httptest.NewRecorder()
	newTestHandler(srv.URL)(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("radius=10000 (boundary): expected 200, got %d", rec.Code)
	}
}

func TestRecommendation_InvalidRadius_TooLow(t *testing.T) {
	handler := newTestHandler("http://unused")
	req := postRecommendation(t, map[string]interface{}{
		"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 249,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("radius=249: expected 400, got %d", rec.Code)
	}
	if !hasErrors(t, rec.Body) {
		t.Error("expected 'errors' array in 400 body")
	}
}

func TestRecommendation_InvalidRadius_TooHigh(t *testing.T) {
	handler := newTestHandler("http://unused")
	req := postRecommendation(t, map[string]interface{}{
		"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 10001,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("radius=10001: expected 400, got %d", rec.Code)
	}
	if !hasErrors(t, rec.Body) {
		t.Error("expected 'errors' array in 400 body")
	}
}

func TestRecommendation_MissingCity_Returns400WithErrors(t *testing.T) {
	handler := newTestHandler("http://unused")
	req := postRecommendation(t, map[string]interface{}{
		"chargerType": "DC_FAST", "radius": 1500,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
	if !hasErrors(t, rec.Body) {
		t.Error("expected 'errors' array in 400 body")
	}
}

func TestRecommendation_MissingChargerType_Returns400WithErrors(t *testing.T) {
	handler := newTestHandler("http://unused")
	req := postRecommendation(t, map[string]interface{}{
		"city": "Bengaluru", "radius": 1500,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
	if !hasErrors(t, rec.Body) {
		t.Error("expected 'errors' array in 400 body")
	}
}

func TestRecommendation_MultipleInvalidFields_AllReported(t *testing.T) {
	// city missing + radius out of range → both errors in the array.
	handler := newTestHandler("http://unused")
	req := postRecommendation(t, map[string]interface{}{
		"chargerType": "DC_FAST", "radius": 50,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
	var body map[string]interface{}
	json.NewDecoder(rec.Body).Decode(&body)
	errs, ok := body["errors"].([]interface{})
	if !ok || len(errs) < 2 {
		t.Errorf("expected at least 2 errors, got %v", body["errors"])
	}
}

func TestRecommendation_UnsupportedCity(t *testing.T) {
	handler := newTestHandler("http://unused")
	req := postRecommendation(t, map[string]interface{}{
		"city": "Delhi", "chargerType": "DC_FAST", "radius": 1500,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("could not decode response: %v", err)
	}
	if _, ok := resp["supported"]; !ok {
		t.Error("expected 'supported' field in 422 response body")
	}
}

func TestRecommendation_GeoService422_TranslatedToCanonical(t *testing.T) {
	// Geo service returning 422 raw should be translated to our contract.
	srv := mockGeoService(t, http.StatusUnprocessableEntity, `{"detail":"some internal validation"}`)
	defer srv.Close()

	handler := newTestHandler(srv.URL)
	req := postRecommendation(t, map[string]interface{}{
		"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 1500,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}
	var resp map[string]interface{}
	json.NewDecoder(rec.Body).Decode(&resp)
	if _, ok := resp["supported"]; !ok {
		t.Error("expected 'supported' field in translated 422 body")
	}
}

func TestRecommendation_GeoServiceFailure_Returns503(t *testing.T) {
	srv := mockGeoService(t, http.StatusInternalServerError, `{"message":"internal error"}`)
	defer srv.Close()

	handler := newTestHandler(srv.URL)
	req := postRecommendation(t, map[string]interface{}{
		"city": "Mumbai", "chargerType": "AC_SLOW", "radius": 500,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", rec.Code)
	}
	if ra := rec.Header().Get("Retry-After"); ra != "30" {
		t.Errorf("expected Retry-After: 30, got %q", ra)
	}
}

func TestRecommendation_GeoServiceDown_Returns503(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	srv.Close()

	handler := newTestHandler(srv.URL)
	req := postRecommendation(t, map[string]interface{}{
		"city": "Pune", "chargerType": "DC_FAST", "radius": 1000,
	})
	rec := httptest.NewRecorder()
	handler(rec, req)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", rec.Code)
	}
}

func TestRecommendation_InvalidJSON(t *testing.T) {
	handler := newTestHandler("http://unused")
	r, _ := http.NewRequest(http.MethodPost, "/recommendation", bytes.NewBufferString("not-json"))
	r.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	handler(rec, r)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
	if !hasErrors(t, rec.Body) {
		t.Error("expected 'errors' array in 400 body for invalid JSON")
	}
}
