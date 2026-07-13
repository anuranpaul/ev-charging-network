package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
)

func newAnalysisHandler(geoURL string) http.HandlerFunc {
	geoProxy := proxy.NewGeoClient(geoURL, 3)
	return AnalysisHandler(geoProxy)
}

func mockGeoService(t *testing.T, status int, body string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		w.Write([]byte(body))
	}))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

func TestAnalysis_ValidRequest(t *testing.T) {
	analysisBody := `{"city":"Bengaluru","chargerType":"DC_FAST","total_candidates":50}`
	srv := mockGeoService(t, http.StatusOK, analysisBody)
	defer srv.Close()

	req, _ := http.NewRequest(http.MethodGet, "/analysis?city=Bengaluru&chargerType=DC_FAST", nil)
	rec := httptest.NewRecorder()
	newAnalysisHandler(srv.URL)(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestAnalysis_CityNormalisedCasing(t *testing.T) {
	// "bengaluru" and "BENGALURU" should both be accepted and forwarded.
	srv := mockGeoService(t, http.StatusOK, `{}`)
	defer srv.Close()

	for _, city := range []string{"bengaluru", "BENGALURU", "Bengaluru"} {
		req, _ := http.NewRequest(http.MethodGet, "/analysis?city="+city+"&chargerType=DC_FAST", nil)
		rec := httptest.NewRecorder()
		newAnalysisHandler(srv.URL)(rec, req)
		if rec.Code != http.StatusOK {
			t.Errorf("city=%q: expected 200, got %d", city, rec.Code)
		}
	}
}

func TestAnalysis_ChargerTypeNormalisedCasing(t *testing.T) {
	srv := mockGeoService(t, http.StatusOK, `{}`)
	defer srv.Close()

	for _, ct := range []string{"dc_fast", "Dc_Fast", "DC_FAST"} {
		req, _ := http.NewRequest(http.MethodGet, "/analysis?city=Bengaluru&chargerType="+ct, nil)
		rec := httptest.NewRecorder()
		newAnalysisHandler(srv.URL)(rec, req)
		if rec.Code != http.StatusOK {
			t.Errorf("chargerType=%q: expected 200, got %d", ct, rec.Code)
		}
	}
}

func TestAnalysis_UnsupportedCity_Returns422(t *testing.T) {
	req, _ := http.NewRequest(http.MethodGet, "/analysis?city=Delhi&chargerType=DC_FAST", nil)
	rec := httptest.NewRecorder()
	newAnalysisHandler("http://unused")(rec, req)

	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}
	var body map[string]interface{}
	json.NewDecoder(rec.Body).Decode(&body)
	if _, ok := body["supported"]; !ok {
		t.Error("expected 'supported' field in 422 body")
	}
}

func TestAnalysis_UnsupportedChargerType_Returns422(t *testing.T) {
	req, _ := http.NewRequest(http.MethodGet, "/analysis?city=Bengaluru&chargerType=HYDROGEN", nil)
	rec := httptest.NewRecorder()
	newAnalysisHandler("http://unused")(rec, req)

	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}
	var body map[string]interface{}
	json.NewDecoder(rec.Body).Decode(&body)
	if _, ok := body["supported"]; !ok {
		t.Error("expected 'supported' field in 422 body")
	}
}

func TestAnalysis_MissingCity_Returns422(t *testing.T) {
	req, _ := http.NewRequest(http.MethodGet, "/analysis?chargerType=DC_FAST", nil)
	rec := httptest.NewRecorder()
	newAnalysisHandler("http://unused")(rec, req)

	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}
}

func TestAnalysis_MissingChargerType_Returns422(t *testing.T) {
	req, _ := http.NewRequest(http.MethodGet, "/analysis?city=Bengaluru", nil)
	rec := httptest.NewRecorder()
	newAnalysisHandler("http://unused")(rec, req)

	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}
}

func TestAnalysis_GeoServiceDown_Returns503(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	srv.Close()

	req, _ := http.NewRequest(http.MethodGet, "/analysis?city=Bengaluru&chargerType=DC_FAST", nil)
	rec := httptest.NewRecorder()
	newAnalysisHandler(srv.URL)(rec, req)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", rec.Code)
	}
	if ra := rec.Header().Get("Retry-After"); ra != "30" {
		t.Errorf("expected Retry-After: 30, got %q", ra)
	}
}

func TestAnalysis_GeoService5xx_Returns503(t *testing.T) {
	srv := mockGeoService(t, http.StatusInternalServerError, `{"message":"internal error"}`)
	defer srv.Close()

	req, _ := http.NewRequest(http.MethodGet, "/analysis?city=Bengaluru&chargerType=DC_FAST", nil)
	rec := httptest.NewRecorder()
	newAnalysisHandler(srv.URL)(rec, req)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", rec.Code)
	}
}

func TestAnalysis_GeoService422_TranslatedToCanonical(t *testing.T) {
	// Even if geo service returns a raw 422, we translate it to our contract.
	srv := mockGeoService(t, http.StatusUnprocessableEntity, `{"detail":"some internal error"}`)
	defer srv.Close()

	req, _ := http.NewRequest(http.MethodGet, "/analysis?city=Bengaluru&chargerType=DC_FAST", nil)
	rec := httptest.NewRecorder()
	newAnalysisHandler(srv.URL)(rec, req)

	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}
	var body map[string]interface{}
	json.NewDecoder(rec.Body).Decode(&body)
	if _, ok := body["supported"]; !ok {
		t.Error("expected 'supported' field in translated 422 body")
	}
}

func TestAnalysis_MethodNotAllowed(t *testing.T) {
	req, _ := http.NewRequest(http.MethodPost, "/analysis?city=Bengaluru&chargerType=DC_FAST", nil)
	rec := httptest.NewRecorder()
	newAnalysisHandler("http://unused")(rec, req)

	if rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", rec.Code)
	}
}
