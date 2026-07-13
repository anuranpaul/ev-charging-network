// Package middleware provides HTTP middleware for the Go API gateway.
package middleware

import (
	"net/http"
	"strings"
)

// CORSMiddleware sets CORS response headers based on the allowedOrigins list.
//
// If allowedOrigins is ["*"], every origin is permitted and the
// Access-Control-Allow-Origin header is set to "*".
//
// Otherwise the incoming Origin header is matched case-insensitively against
// the list; matching origins are reflected back so that cookies/credentials
// can work correctly when a specific origin is configured.
//
// Pre-flight OPTIONS requests are answered with 204 No Content and the full
// set of CORS headers so that the browser does not block the actual request.
func CORSMiddleware(allowedOrigins []string) func(http.Handler) http.Handler {
	wildcard := len(allowedOrigins) == 1 && allowedOrigins[0] == "*"

	// Normalise to lowercase for case-insensitive matching.
	normalised := make([]string, len(allowedOrigins))
	for i, o := range allowedOrigins {
		normalised[i] = strings.ToLower(strings.TrimSpace(o))
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")

			if wildcard {
				w.Header().Set("Access-Control-Allow-Origin", "*")
			} else if origin != "" {
				for _, allowed := range normalised {
					if strings.ToLower(origin) == allowed {
						w.Header().Set("Access-Control-Allow-Origin", origin)
						w.Header().Add("Vary", "Origin")
						break
					}
				}
			}

			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type, X-API-Key, X-Correlation-ID")
			w.Header().Set("Access-Control-Expose-Headers", "X-Cache, X-Correlation-ID, Retry-After")

			// Short-circuit pre-flight requests.
			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}

// ParseOrigins splits a comma-separated origins string into a slice,
// trimming whitespace from each entry. Returns ["*"] for an empty string.
func ParseOrigins(raw string) []string {
	raw = strings.TrimSpace(raw)
	if raw == "" || raw == "*" {
		return []string{"*"}
	}
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if trimmed := strings.TrimSpace(p); trimmed != "" {
			out = append(out, trimmed)
		}
	}
	if len(out) == 0 {
		return []string{"*"}
	}
	return out
}
