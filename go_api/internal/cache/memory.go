package cache

import (
	"strings"
	"sync"
	"time"
)

// CacheKey is the composite lookup key, case-normalised before use.
type CacheKey struct {
	City        string
	ChargerType string
	Radius      int
}

// NormaliseKey returns a CacheKey with City title-cased and ChargerType
// uppercased. Empty strings are preserved as-is (callers are expected to
// validate inputs before calling NormaliseKey, but the function must not
// panic on empty input).
func NormaliseKey(city, chargerType string, radius int) CacheKey {
	normCity := city
	if len(city) > 0 {
		normCity = strings.ToUpper(city[:1]) + strings.ToLower(city[1:])
	}
	return CacheKey{
		City:        normCity,
		ChargerType: strings.ToUpper(chargerType),
		Radius:      radius,
	}
}

// CacheEntry holds the serialised response body and its expiry time.
type CacheEntry struct {
	Body      []byte
	ExpiresAt time.Time
}

// MemoryCache is a TTL-based, goroutine-safe in-memory cache.
type MemoryCache struct {
	mu      sync.RWMutex
	entries map[CacheKey]CacheEntry
	ttl     time.Duration
}

// NewMemoryCache creates a MemoryCache and starts a background eviction
// goroutine that sweeps the map every 60 s.
func NewMemoryCache(ttl time.Duration) *MemoryCache {
	c := &MemoryCache{
		entries: make(map[CacheKey]CacheEntry),
		ttl:     ttl,
	}
	go c.evictLoop()
	return c
}

// Get returns the cached body and true on a valid (non-expired) hit.
func (c *MemoryCache) Get(key CacheKey) ([]byte, bool) {
	c.mu.RLock()
	entry, ok := c.entries[key]
	c.mu.RUnlock()

	if !ok || time.Now().After(entry.ExpiresAt) {
		return nil, false
	}
	return entry.Body, true
}

// Set stores body under key with the configured TTL.
func (c *MemoryCache) Set(key CacheKey, body []byte) {
	c.mu.Lock()
	c.entries[key] = CacheEntry{
		Body:      body,
		ExpiresAt: time.Now().Add(c.ttl),
	}
	c.mu.Unlock()
}

// evictLoop runs forever, deleting expired entries every 60 seconds.
func (c *MemoryCache) evictLoop() {
	ticker := time.NewTicker(60 * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		now := time.Now()
		c.mu.Lock()
		for k, v := range c.entries {
			if now.After(v.ExpiresAt) {
				delete(c.entries, k)
			}
		}
		c.mu.Unlock()
	}
}
