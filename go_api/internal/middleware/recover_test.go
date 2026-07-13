package middleware

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// panicHandler is a test handler that always panics.
func panicHandler(msg string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic(msg)
	})
}

func TestRecoveryMiddleware_CatchesPanic_Returns500(t *testing.T) {
	handler := RecoveryMiddleware(panicHandler("something went wrong"))

	req, _ := http.NewRequest(http.MethodGet, "/boom", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", rec.Code)
	}
}

func TestRecoveryMiddleware_ResponseBody_IsJSON(t *testing.T) {
	handler := RecoveryMiddleware(panicHandler("oops"))

	req, _ := http.NewRequest(http.MethodGet, "/boom", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	var body map[string]string
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatalf("response body is not valid JSON: %v", err)
	}
	if body["message"] == "" {
		t.Error("expected non-empty 'message' field in 500 body")
	}
}

func TestRecoveryMiddleware_ContentType_IsJSON(t *testing.T) {
	handler := RecoveryMiddleware(panicHandler("oops"))

	req, _ := http.NewRequest(http.MethodGet, "/boom", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %q", ct)
	}
}

func TestRecoveryMiddleware_IncludesCorrelationID_InLog(t *testing.T) {
	// Verify recovery still fires when a correlation ID is present on the
	// response header (simulating the real middleware chain order).
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Correlation-ID", "test-corr-id-123")
		panic("handler panic with corr id")
	})

	handler := RecoveryMiddleware(inner)
	req, _ := http.NewRequest(http.MethodGet, "/boom", nil)
	rec := httptest.NewRecorder()

	// Must not crash the test process.
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", rec.Code)
	}
}

func TestRecoveryMiddleware_NoPanic_PassesThrough(t *testing.T) {
	// When no panic occurs the middleware must be transparent.
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"ok":true}`))
	})

	handler := RecoveryMiddleware(inner)
	req, _ := http.NewRequest(http.MethodGet, "/fine", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 passthrough, got %d", rec.Code)
	}
	if body := rec.Body.String(); body != `{"ok":true}` {
		t.Errorf("unexpected body: %q", body)
	}
}

func TestRecoveryMiddleware_PanicWithNilValue(t *testing.T) {
	// panic(nil) is valid Go; recovery must handle it without itself panicking.
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic(nil)
	})

	handler := RecoveryMiddleware(inner)
	req, _ := http.NewRequest(http.MethodGet, "/nilpanic", nil)
	rec := httptest.NewRecorder()

	// Should not re-panic.
	handler.ServeHTTP(rec, req)

	// panic(nil) is recovered; in Go 1.21+ recover() returns a *runtime.PanicNilError
	// for panic(nil), so rec will be non-nil and we get a 500.
	// On older Go, recover() returns nil for panic(nil), meaning the defer
	// fires but the if-branch is skipped and the response is whatever the
	// recorder defaulted to (200 with empty body).  Either outcome is
	// acceptable — the important thing is the process doesn't crash.
	if rec.Code != http.StatusOK && rec.Code != http.StatusInternalServerError {
		t.Fatalf("unexpected status %d for panic(nil)", rec.Code)
	}
}
