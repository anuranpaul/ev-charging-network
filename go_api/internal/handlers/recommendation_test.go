package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/cache"
	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
)

// ---- test infrastructure ---------------------------------------------------

// mockGeoServer creates a test HTTP server that returns the given status and
// body for every request.  An atomic counter tracks how many times it is hit
// so callers can assert single-call guarantees.
func mockGeoServer(t *testing.T, status int, body string) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	var calls atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv, &calls
}

// newHandler creates a fresh RecommendationHandler wired to geoURL with a
// brand-new cache each time (avoids inter-test cache leakage).
func newHandler(geoURL string) http.HandlerFunc {
	geoProxy := proxy.NewGeoClient(geoURL, 3)
	memCache := cache.NewMemoryCache(5 * time.Minute)
	return RecommendationHandler(geoProxy, memCache)
}

// newHandlerWithCache creates a handler sharing the supplied cache so that
// cache-miss/hit sequencing can be verified across multiple requests.
func newHandlerWithCache(geoURL string, memCache *cache.MemoryCache) http.HandlerFunc {
	geoProxy := proxy.NewGeoClient(geoURL, 3)
	return RecommendationHandler(geoProxy, memCache)
}

// postJSON builds a POST /recommendation request from an arbitrary payload.
func postJSON(t *testing.T, payload interface{}) *http.Request {
	t.Helper()
	b, err := json.Marshal(payload)
	require.NoError(t, err)
	r, err := http.NewRequest(http.MethodPost, "/recommendation", bytes.NewReader(b))
	require.NoError(t, err)
	r.Header.Set("Content-Type", "application/json")
	return r
}

// decodeBody is a convenience helper that decodes the recorder body into a
// generic map so individual fields can be asserted.
func decodeBody(t *testing.T, rec *httptest.ResponseRecorder) map[string]interface{} {
	t.Helper()
	var out map[string]interface{}
	err := json.NewDecoder(rec.Body).Decode(&out)
	require.NoError(t, err, "response body must be valid JSON")
	return out
}

// ---- valid request ---------------------------------------------------------

func TestRecommendation_ValidRequest_Returns200(t *testing.T) {
	geoBody := `{"type":"FeatureCollection","features":[]}`
	srv, _ := mockGeoServer(t, http.StatusOK, geoBody)

	rec := httptest.NewRecorder()
	newHandler(srv.URL)(rec, postJSON(t, map[string]interface{}{
		"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 1500,
	}))

	require.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "MISS", rec.Header().Get("X-Cache"))
	assert.Equal(t, "application/json", rec.Header().Get("Content-Type"))

	body := decodeBody(t, rec)
	assert.Equal(t, "FeatureCollection", body["type"])
}

func TestRecommendation_ValidRequest_CacheHeaderMISS_ThenHIT(t *testing.T) {
	geoBody := `{"type":"FeatureCollection","features":[]}`
	srv, calls := mockGeoServer(t, http.StatusOK, geoBody)

	memCache := cache.NewMemoryCache(5 * time.Minute)
	handler := newHandlerWithCache(srv.URL, memCache)

	payload := map[string]interface{}{
		"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 1500,
	}

	// First request → MISS, geo service called once.
	rec1 := httptest.NewRecorder()
	handler(rec1, postJSON(t, payload))
	require.Equal(t, http.StatusOK, rec1.Code)
	assert.Equal(t, "MISS", rec1.Header().Get("X-Cache"))

	// Second request → HIT, geo service NOT called again.
	rec2 := httptest.NewRecorder()
	handler(rec2, postJSON(t, payload))
	require.Equal(t, http.StatusOK, rec2.Code)
	assert.Equal(t, "HIT", rec2.Header().Get("X-Cache"))

	assert.Equal(t, int64(1), calls.Load(),
		"geo service must be called exactly once for two identical requests")

	// Byte-identical bodies.
	assert.Equal(t, rec1.Body.String(), rec2.Body.String(),
		"cached and fresh bodies must be byte-identical")
}

// ---- radius validation -----------------------------------------------------

func TestRecommendation_Radius(t *testing.T) {
	tests := []struct {
		name       string
		radius     int
		wantStatus int
	}{
		{"exact lower bound 250 is valid", 250, http.StatusOK},
		{"exact upper bound 10000 is valid", 10000, http.StatusOK},
		{"one below lower bound 249 is invalid", 249, http.StatusBadRequest},
		{"one above upper bound 10001 is invalid", 10001, http.StatusBadRequest},
		{"zero is invalid", 0, http.StatusBadRequest},
		{"negative is invalid", -1, http.StatusBadRequest},
		{"mid-range 1500 is valid", 1500, http.StatusOK},
	}

	geoBody := `{"type":"FeatureCollection","features":[]}`

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			// Only create a real server for cases that will reach the geo service.
			geoURL := "http://127.0.0.1:0" // unreachable — validation fires first
			if tc.wantStatus == http.StatusOK {
				srv, _ := mockGeoServer(t, http.StatusOK, geoBody)
				geoURL = srv.URL
			}

			rec := httptest.NewRecorder()
			newHandler(geoURL)(rec, postJSON(t, map[string]interface{}{
				"city": "Bengaluru", "chargerType": "DC_FAST", "radius": tc.radius,
			}))

			require.Equal(t, tc.wantStatus, rec.Code)

			if tc.wantStatus == http.StatusBadRequest {
				body := decodeBody(t, rec)
				errs, ok := body["errors"].([]interface{})
				require.True(t, ok, "400 body must contain an 'errors' array")
				require.NotEmpty(t, errs, "'errors' array must not be empty")

				// At least one error must name the "radius" field.
				foundRadiusField := false
				for _, e := range errs {
					if m, ok := e.(map[string]interface{}); ok {
						if m["field"] == "radius" {
							foundRadiusField = true
						}
					}
				}
				assert.True(t, foundRadiusField,
					"expected an error entry with field='radius', got: %v", errs)
			}
		})
	}
}

// ---- missing / blank fields ------------------------------------------------

func TestRecommendation_MissingFields_Returns400WithErrorsArray(t *testing.T) {
	tests := []struct {
		name          string
		payload       map[string]interface{}
		wantErrFields []string // field names expected in the errors array
	}{
		{
			name:          "missing city",
			payload:       map[string]interface{}{"chargerType": "DC_FAST", "radius": 1500},
			wantErrFields: []string{"city"},
		},
		{
			name:          "missing chargerType",
			payload:       map[string]interface{}{"city": "Bengaluru", "radius": 1500},
			wantErrFields: []string{"chargerType"},
		},
		{
			name:          "missing city and invalid radius",
			payload:       map[string]interface{}{"chargerType": "DC_FAST", "radius": 50},
			wantErrFields: []string{"city", "radius"},
		},
		{
			name:          "all fields missing",
			payload:       map[string]interface{}{},
			wantErrFields: []string{"city", "chargerType", "radius"},
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			rec := httptest.NewRecorder()
			newHandler("http://unused")(rec, postJSON(t, tc.payload))

			require.Equal(t, http.StatusBadRequest, rec.Code)

			body := decodeBody(t, rec)
			errs, ok := body["errors"].([]interface{})
			require.True(t, ok, "400 body must contain 'errors' array")

			actualFields := make([]string, 0, len(errs))
			for _, e := range errs {
				if m, ok := e.(map[string]interface{}); ok {
					if f, ok := m["field"].(string); ok {
						actualFields = append(actualFields, f)
					}
				}
			}

			for _, want := range tc.wantErrFields {
				assert.Contains(t, actualFields, want,
					"expected field %q in errors array, got %v", want, actualFields)
			}
		})
	}
}

func TestRecommendation_InvalidJSON_Returns400(t *testing.T) {
	r, err := http.NewRequest(http.MethodPost, "/recommendation",
		bytes.NewBufferString("{not valid json"))
	require.NoError(t, err)
	r.Header.Set("Content-Type", "application/json")

	rec := httptest.NewRecorder()
	newHandler("http://unused")(rec, r)

	require.Equal(t, http.StatusBadRequest, rec.Code)
	body := decodeBody(t, rec)
	_, hasErrors := body["errors"]
	assert.True(t, hasErrors, "invalid JSON must produce an 'errors' key")
}

// ---- unsupported city ------------------------------------------------------

func TestRecommendation_UnsupportedCity_Returns422(t *testing.T) {
	tests := []struct {
		name string
		city string
	}{
		{"unknown city Delhi", "Delhi"},
		{"empty string after trim", "   "},
		{"city not in registry", "Kolkata"},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			rec := httptest.NewRecorder()
			newHandler("http://unused")(rec, postJSON(t, map[string]interface{}{
				"city": tc.city, "chargerType": "DC_FAST", "radius": 1500,
			}))

			require.Equal(t, http.StatusUnprocessableEntity, rec.Code)

			body := decodeBody(t, rec)
			supported, ok := body["supported"].([]interface{})
			require.True(t, ok, "422 body must contain a 'supported' array")
			require.NotEmpty(t, supported, "'supported' array must not be empty")

			// All canonical cities must be present.
			supportedStrs := make([]string, 0, len(supported))
			for _, s := range supported {
				if str, ok := s.(string); ok {
					supportedStrs = append(supportedStrs, str)
				}
			}
			for _, want := range SupportedCities {
				assert.Contains(t, supportedStrs, want,
					"expected %q in supported list", want)
			}
		})
	}
}

// ---- geo service failure → 503, NOT cached ---------------------------------

func TestRecommendation_GeoServiceFailure_Returns503_NotCached(t *testing.T) {
	tests := []struct {
		name       string
		geoStatus  int
		closeFirst bool // if true, close the server before the request (simulates timeout/dial-error)
	}{
		{"geo service 500", http.StatusInternalServerError, false},
		{"geo service 502", http.StatusBadGateway, false},
		{"geo service 503", http.StatusServiceUnavailable, false},
		{"geo service unreachable (dial error)", 0, true},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			var geoURL string
			var calls atomic.Int64

			if tc.closeFirst {
				// Create and immediately close — any dial will fail.
				srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls.Add(1)
				}))
				srv.Close()
				geoURL = srv.URL
			} else {
				srv, c := mockGeoServer(t, tc.geoStatus, `{"message":"upstream error"}`)
				geoURL = srv.URL
				calls = *c
			}

			memCache := cache.NewMemoryCache(5 * time.Minute)
			handler := newHandlerWithCache(geoURL, memCache)

			payload := map[string]interface{}{
				"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 1500,
			}

			// ── First request: must return 503 ──────────────────────────────
			rec1 := httptest.NewRecorder()
			handler(rec1, postJSON(t, payload))

			require.Equal(t, http.StatusServiceUnavailable, rec1.Code,
				"%s: expected 503 on failure", tc.name)
			assert.Equal(t, "30", rec1.Header().Get("Retry-After"),
				"Retry-After: 30 must be present on 503")

			// ── Confirm failure is NOT in the cache ──────────────────────────
			// Issue the identical request a second time; it must NOT return a
			// cached 503 body (i.e. X-Cache must not be HIT, and the status
			// must still be 503 from a fresh upstream call, not a stale cache
			// entry disguised as 200).
			rec2 := httptest.NewRecorder()
			handler(rec2, postJSON(t, payload))

			assert.NotEqual(t, "HIT", rec2.Header().Get("X-Cache"),
				"a 503 failure must never be served from cache")
			assert.Equal(t, http.StatusServiceUnavailable, rec2.Code,
				"second request to a still-broken upstream must also be 503")
		})
	}
}

// TestRecommendation_503DoesNotPolluteCacheForSuccessfulResponse confirms
// that after a transient failure the cache correctly stores a subsequent
// successful response.
func TestRecommendation_503ThenSuccess_CachesSuccess(t *testing.T) {
	// First: 500 from geo service.
	failSrv, _ := mockGeoServer(t, http.StatusInternalServerError, `{"message":"err"}`)
	successBody := `{"type":"FeatureCollection","features":[]}`

	memCache := cache.NewMemoryCache(5 * time.Minute)
	payload := map[string]interface{}{
		"city": "Mumbai", "chargerType": "AC_SLOW", "radius": 500,
	}

	// Request 1 → 503, nothing cached.
	handler1 := newHandlerWithCache(failSrv.URL, memCache)
	rec1 := httptest.NewRecorder()
	handler1(rec1, postJSON(t, payload))
	require.Equal(t, http.StatusServiceUnavailable, rec1.Code)

	// Request 2 → now the geo service is healthy; response must be stored.
	okSrv, calls := mockGeoServer(t, http.StatusOK, successBody)
	handler2 := newHandlerWithCache(okSrv.URL, memCache)
	rec2 := httptest.NewRecorder()
	handler2(rec2, postJSON(t, payload))
	require.Equal(t, http.StatusOK, rec2.Code)
	assert.Equal(t, "MISS", rec2.Header().Get("X-Cache"),
		"first successful call must be a cache MISS")

	// Request 3 → must hit cache, geo service not called again.
	rec3 := httptest.NewRecorder()
	handler2(rec3, postJSON(t, payload))
	require.Equal(t, http.StatusOK, rec3.Code)
	assert.Equal(t, "HIT", rec3.Header().Get("X-Cache"))
	assert.Equal(t, int64(1), calls.Load(),
		"geo service must be called only once after it became healthy")
}

// ---- geo-service 422 translation -------------------------------------------

func TestRecommendation_GeoService422_TranslatedToCanonical(t *testing.T) {
	// Even if the geo service returns a raw 422, the Go API must translate it
	// into the canonical {"message":..., "supported":[...]} envelope.
	srv, _ := mockGeoServer(t, http.StatusUnprocessableEntity,
		`{"detail":"some internal validation error"}`)

	rec := httptest.NewRecorder()
	newHandler(srv.URL)(rec, postJSON(t, map[string]interface{}{
		"city": "Bengaluru", "chargerType": "DC_FAST", "radius": 1500,
	}))

	require.Equal(t, http.StatusUnprocessableEntity, rec.Code)
	body := decodeBody(t, rec)
	_, hasSupported := body["supported"]
	assert.True(t, hasSupported,
		"translated 422 must contain 'supported' array, got: %v", body)
}

// ---- wrong HTTP method -----------------------------------------------------

func TestRecommendation_WrongMethod_Returns405(t *testing.T) {
	for _, method := range []string{http.MethodGet, http.MethodPut, http.MethodDelete} {
		method := method
		t.Run(method, func(t *testing.T) {
			r, err := http.NewRequest(method, "/recommendation", nil)
			require.NoError(t, err)
			rec := httptest.NewRecorder()
			newHandler("http://unused")(rec, r)
			assert.Equal(t, http.StatusMethodNotAllowed, rec.Code)
		})
	}
}
