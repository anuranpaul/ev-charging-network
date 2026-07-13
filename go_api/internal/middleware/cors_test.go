package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func dummyHandler(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}

func TestCORSMiddleware_Wildcard(t *testing.T) {
	mw := CORSMiddleware([]string{"*"})
	handler := mw(http.HandlerFunc(dummyHandler))

	req, _ := http.NewRequest(http.MethodGet, "/health", nil)
	req.Header.Set("Origin", "https://example.com")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "*" {
		t.Errorf("expected *, got %q", got)
	}
}

func TestCORSMiddleware_SpecificOrigin_Allowed(t *testing.T) {
	mw := CORSMiddleware([]string{"https://app.chargewise.example"})
	handler := mw(http.HandlerFunc(dummyHandler))

	req, _ := http.NewRequest(http.MethodGet, "/cities", nil)
	req.Header.Set("Origin", "https://app.chargewise.example")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "https://app.chargewise.example" {
		t.Errorf("expected origin reflected back, got %q", got)
	}
}

func TestCORSMiddleware_SpecificOrigin_Blocked(t *testing.T) {
	mw := CORSMiddleware([]string{"https://app.chargewise.example"})
	handler := mw(http.HandlerFunc(dummyHandler))

	req, _ := http.NewRequest(http.MethodGet, "/cities", nil)
	req.Header.Set("Origin", "https://evil.example.com")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	// No ACAO header set for unlisted origin.
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "" {
		t.Errorf("expected no ACAO header for blocked origin, got %q", got)
	}
	// Request is still processed — CORS headers are advisory.
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
}

func TestCORSMiddleware_PreFlight_Returns204(t *testing.T) {
	mw := CORSMiddleware([]string{"*"})
	handler := mw(http.HandlerFunc(dummyHandler))

	req, _ := http.NewRequest(http.MethodOptions, "/recommendation", nil)
	req.Header.Set("Origin", "https://app.example.com")
	req.Header.Set("Access-Control-Request-Method", "POST")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusNoContent {
		t.Fatalf("expected 204 for pre-flight, got %d", rec.Code)
	}
	if got := rec.Header().Get("Access-Control-Allow-Methods"); got == "" {
		t.Error("expected Access-Control-Allow-Methods header on pre-flight")
	}
	if got := rec.Header().Get("Access-Control-Allow-Headers"); got == "" {
		t.Error("expected Access-Control-Allow-Headers header on pre-flight")
	}
}

func TestParseOrigins_Wildcard(t *testing.T) {
	origins := ParseOrigins("*")
	if len(origins) != 1 || origins[0] != "*" {
		t.Errorf("unexpected result: %v", origins)
	}
}

func TestParseOrigins_Empty_DefaultsToWildcard(t *testing.T) {
	origins := ParseOrigins("")
	if len(origins) != 1 || origins[0] != "*" {
		t.Errorf("expected [*] for empty input, got %v", origins)
	}
}

func TestParseOrigins_CommaSeparated(t *testing.T) {
	origins := ParseOrigins("https://a.example.com, https://b.example.com")
	if len(origins) != 2 {
		t.Fatalf("expected 2 origins, got %d: %v", len(origins), origins)
	}
}
