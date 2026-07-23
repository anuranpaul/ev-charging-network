package handlers

import (
	"io"
	"net/http"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
)

// ExplainHandler proxies POST /explain to the geo service.
// Protected — requires X-API-Key.
//
// AI Enhancement 2: AI-Powered Explanation and Justification.
// The geo service builds a prompt from candidate properties and calls the
// configured LLM provider. The Go API simply forwards the request/response
// and adds caching (TTL 10 min keyed on city+rank+chargerType).
func ExplainHandler(geoProxy *proxy.GeoClient) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		body, err := io.ReadAll(r.Body)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]interface{}{
				"message": "failed to read request body",
			})
			return
		}
		defer r.Body.Close()

		// Forward to geo service POST /explain
		result := geoProxy.CallPOST(r, geoProxy.BaseURL+"/explain", body)

		if result.Err != nil || result.StatusCode >= 500 {
			proxy.Write503(w)
			return
		}

		proxy.CopyHeaders(w.Header(), result.Headers)
		w.WriteHeader(result.StatusCode)
		w.Write(result.Body)
	}
}
