package auth

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAPIKeyMiddleware(t *testing.T) {
	expectedKey := "test-secret-key"
	middleware := APIKeyMiddleware(expectedKey)

	// A dummy handler to test if middleware calls the next handler
	dummyHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	handler := middleware(dummyHandler)

	tests := []struct {
		name           string
		path           string
		providedKey    string
		expectedStatus int
	}{
		{
			name:           "Missing key on protected route",
			path:           "/protected",
			providedKey:    "",
			expectedStatus: http.StatusUnauthorized,
		},
		{
			name:           "Wrong key on protected route",
			path:           "/protected",
			providedKey:    "wrong-key",
			expectedStatus: http.StatusUnauthorized,
		},
		{
			name:           "Correct key on protected route",
			path:           "/protected",
			providedKey:    expectedKey,
			expectedStatus: http.StatusOK,
		},
		{
			name:           "No key on /health",
			path:           "/health",
			providedKey:    "",
			expectedStatus: http.StatusOK,
		},
		{
			name:           "No key on /cities",
			path:           "/cities",
			providedKey:    "",
			expectedStatus: http.StatusOK,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(http.MethodGet, tc.path, nil)
			if err != nil {
				t.Fatalf("could not create request: %v", err)
			}

			if tc.providedKey != "" {
				req.Header.Set("X-API-Key", tc.providedKey)
			}

			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)

			if rec.Code != tc.expectedStatus {
				t.Errorf("expected status %d, got %d", tc.expectedStatus, rec.Code)
			}
		})
	}
}
