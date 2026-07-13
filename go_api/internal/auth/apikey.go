package auth

import (
	"crypto/subtle"
	"encoding/json"
	"net/http"
)

func APIKeyMiddleware(expectedKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Public endpoints never require a key — checked by path here;
			// once this moves to a router (chi/gorilla), express this as
			// route registration instead of an if-branch.
			if r.URL.Path == "/health" || r.URL.Path == "/cities" {
				next.ServeHTTP(w, r)
				return
			}

			supplied := r.Header.Get("X-API-Key")
			if subtle.ConstantTimeCompare([]byte(supplied), []byte(expectedKey)) != 1 {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusUnauthorized)
				json.NewEncoder(w).Encode(map[string]string{
					"message": "missing or invalid X-API-Key",
				})
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
