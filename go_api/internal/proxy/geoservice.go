package proxy

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"
)

type GeoClient struct {
	BaseURL    string
	TimeoutSec int
	HTTPClient *http.Client
}

func NewGeoClient(baseURL string, timeoutSec int) *GeoClient {
	return &GeoClient{
		BaseURL:    baseURL,
		TimeoutSec: timeoutSec,
		HTTPClient: &http.Client{},
	}
}

// CallResult is the result of a proxied call to the geo service.
// Body is non-nil only on 2xx responses.
type CallResult struct {
	StatusCode int
	Body       []byte
	Headers    http.Header
	Err        error
}

// Call POSTs body to the geo service /recommendation endpoint and returns
// the raw response. Callers decide whether to cache the result.
func (c *GeoClient) Call(r *http.Request, rawBody []byte) CallResult {
	correlationID := r.Header.Get("X-Correlation-ID")
	if correlationID == "" {
		correlationID = uuid.NewString()
	}

	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(c.TimeoutSec)*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/recommendation", bytes.NewReader(rawBody))
	if err != nil {
		return CallResult{Err: err}
	}

	ct := r.Header.Get("Content-Type")
	if ct == "" {
		ct = "application/json"
	}
	req.Header.Set("Content-Type", ct)
	req.Header.Set("X-Correlation-ID", correlationID)

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return CallResult{Err: err}
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return CallResult{Err: err}
	}

	return CallResult{
		StatusCode: resp.StatusCode,
		Body:       body,
		Headers:    resp.Header,
	}
}

// Write503 writes a standard 503 with Retry-After: 30 to w.
func Write503(w http.ResponseWriter) {
	w.Header().Set("Retry-After", "30")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusServiceUnavailable)
	w.Write([]byte(`{"message":"geo service unavailable"}`))
}
