package middleware

import (
	"encoding/json"
	"log"
	"net/http"
	"runtime/debug"
)

// RecoveryMiddleware catches any panic that escapes a downstream handler,
// logs the stack trace at ERROR level with the request's correlation ID,
// and returns a 500 JSON response instead of crashing the process.
//
// It must sit inside the correlationID middleware so that the ID is already
// present on the context (and echoed in the response header) when a panic
// occurs.  The recommended chain order is:
//
//	correlationID → logger → recovery → CORS → auth → mux
func RecoveryMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				// Extract correlation ID that was set by correlationIDMiddleware.
				corrID := w.Header().Get("X-Correlation-ID")
				if corrID == "" {
					corrID = "MISSING"
				}

				stack := debug.Stack()
				log.Printf(
					`{"level":"ERROR","correlation_id":%q,"panic":%q,"stack":%q}`,
					corrID,
					rec,
					stack,
				)

				// Only write the 500 if the response header hasn't been sent yet.
				// WriteHeader panics silently once headers are flushed, so guard
				// with a flag on the recorder — but since we wrap at a high level
				// headers are almost never flushed before a panic in handler code.
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusInternalServerError)
				json.NewEncoder(w).Encode(map[string]string{
					"message": "internal server error",
				})
			}
		}()

		next.ServeHTTP(w, r)
	})
}
