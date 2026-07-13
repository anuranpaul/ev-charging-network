package auth

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const testKey = "test-secret-key-abc123"

// downstream is a simple handler that records it was reached.
func downstream(t *testing.T, reached *bool) http.Handler {
	t.Helper()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*reached = true
		w.WriteHeader(http.StatusOK)
	})
}

func TestAPIKeyMiddleware(t *testing.T) {
	tests := []struct {
		name           string
		path           string
		key            string // empty string = no header sent
		wantStatus     int
		wantDownstream bool   // whether the next handler should be called
		wantMessage    string // substring expected in body on 401
	}{
		{
			name:           "no header on protected route returns 401",
			path:           "/recommendation",
			key:            "",
			wantStatus:     http.StatusUnauthorized,
			wantDownstream: false,
			wantMessage:    "missing or invalid",
		},
		{
			name:           "wrong key on protected route returns 401",
			path:           "/chargers",
			key:            "completely-wrong",
			wantStatus:     http.StatusUnauthorized,
			wantDownstream: false,
			wantMessage:    "missing or invalid",
		},
		{
			name:           "correct key on protected route passes through",
			path:           "/recommendation",
			key:            testKey,
			wantStatus:     http.StatusOK,
			wantDownstream: true,
		},
		{
			name:           "/health requires no key",
			path:           "/health",
			key:            "", // deliberately omitted
			wantStatus:     http.StatusOK,
			wantDownstream: true,
		},
		{
			name:           "/cities requires no key",
			path:           "/cities",
			key:            "", // deliberately omitted
			wantStatus:     http.StatusOK,
			wantDownstream: true,
		},
		{
			name:           "correct key on /health still passes through",
			path:           "/health",
			key:            testKey,
			wantStatus:     http.StatusOK,
			wantDownstream: true,
		},
		{
			name:           "wrong key on /health still passes through (public path)",
			path:           "/health",
			key:            "wrong",
			wantStatus:     http.StatusOK,
			wantDownstream: true,
		},
		{
			name:           "analysis endpoint protected",
			path:           "/analysis",
			key:            "",
			wantStatus:     http.StatusUnauthorized,
			wantDownstream: false,
			wantMessage:    "missing or invalid",
		},
	}

	for _, tc := range tests {
		tc := tc // capture range variable
		t.Run(tc.name, func(t *testing.T) {
			reached := false
			mw := APIKeyMiddleware(testKey)
			handler := mw(downstream(t, &reached))

			req, err := http.NewRequest(http.MethodGet, tc.path, nil)
			require.NoError(t, err)
			if tc.key != "" {
				req.Header.Set("X-API-Key", tc.key)
			}

			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)

			assert.Equal(t, tc.wantStatus, rec.Code)
			assert.Equal(t, tc.wantDownstream, reached,
				"downstream handler called mismatch")

			if tc.wantMessage != "" {
				assert.Contains(t, rec.Body.String(), tc.wantMessage)
			}
		})
	}
}

// TestAPIKeyMiddleware_ConstantTime confirms the middleware does not leak the
// expected key value in any response body or header, regardless of what the
// caller supplies.
func TestAPIKeyMiddleware_NoKeyLeakInResponse(t *testing.T) {
	reached := false
	mw := APIKeyMiddleware(testKey)
	handler := mw(downstream(t, &reached))

	req, err := http.NewRequest(http.MethodGet, "/recommendation", nil)
	require.NoError(t, err)
	req.Header.Set("X-API-Key", "attacker-supplied-value")

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	require.Equal(t, http.StatusUnauthorized, rec.Code)
	assert.NotContains(t, rec.Body.String(), testKey,
		"expected key must never appear in error response")
	assert.NotContains(t, rec.Body.String(), "attacker-supplied-value",
		"supplied key must never be echoed back")
}
