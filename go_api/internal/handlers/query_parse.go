package handlers

import (
	"io"
	"net/http"

	"github.com/anuranpaul/ev-charging-network/go_api/internal/proxy"
)

// QueryParseHandler proxies POST /query/parse to the geo service.
// Protected — requires X-API-Key.
//
// AI Enhancement 4: Natural Language Query Interface.
// The geo service uses an LLM to extract structured parameters from a
// natural language query. The Go API forwards the request/response.
func QueryParseHandler(geoProxy *proxy.GeoClient) http.HandlerFunc {
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

		// Forward to geo service POST /query/parse
		result := geoProxy.CallPOST(r, geoProxy.BaseURL+"/query/parse", body)

		if result.Err != nil || result.StatusCode >= 500 {
			proxy.Write503(w)
			return
		}

		proxy.CopyHeaders(w.Header(), result.Headers)
		w.WriteHeader(result.StatusCode)
		w.Write(result.Body)
	}
}
