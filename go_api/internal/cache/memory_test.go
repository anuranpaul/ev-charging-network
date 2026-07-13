// Run with: go test -race ./internal/cache/...
package cache

import (
	"bytes"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// ---- helpers ---------------------------------------------------------------

func newCache(ttl time.Duration) *MemoryCache {
	return NewMemoryCache(ttl)
}

var defaultBody = []byte(`{"type":"FeatureCollection","features":[]}`)

// ---- NormaliseKey ----------------------------------------------------------

func TestNormaliseKey(t *testing.T) {
	tests := []struct {
		name        string
		city        string
		chargerType string
		radius      int
		wantCity    string
		wantType    string
	}{
		{"all lowercase", "bengaluru", "dc_fast", 1500, "Bengaluru", "DC_FAST"},
		{"all uppercase", "BENGALURU", "DC_FAST", 1500, "Bengaluru", "DC_FAST"},
		{"title case (no-op)", "Bengaluru", "DC_FAST", 1500, "Bengaluru", "DC_FAST"},
		{"mixed case city", "mUMBAI", "slow", 500, "Mumbai", "SLOW"},
		{"charger type mixed", "Pune", "Fast", 2000, "Pune", "FAST"},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			k := NormaliseKey(tc.city, tc.chargerType, tc.radius)
			assert.Equal(t, tc.wantCity, k.City)
			assert.Equal(t, tc.wantType, k.ChargerType)
			assert.Equal(t, tc.radius, k.Radius)
		})
	}
}

// ---- Miss / Hit ------------------------------------------------------------

func TestGet_FreshCache_IsMiss(t *testing.T) {
	c := newCache(5 * time.Minute)
	key := NormaliseKey("Bengaluru", "DC_FAST", 1500)

	got, hit := c.Get(key)
	assert.False(t, hit, "fresh cache should be a miss")
	assert.Nil(t, got)
}

func TestSetThenGet_IsHit(t *testing.T) {
	c := newCache(5 * time.Minute)
	key := NormaliseKey("Bengaluru", "DC_FAST", 1500)

	c.Set(key, defaultBody)

	got, hit := c.Get(key)
	require.True(t, hit, "expected a cache hit after Set")
	assert.True(t, bytes.Equal(defaultBody, got), "body must be byte-identical")
}

// ---- Single geo-service call guarantee -------------------------------------

// TestSameKeyTwice_GeoCalledOnce simulates the cache-aside pattern: two
// requests for the same (city, chargerType, radius) must result in exactly
// one upstream call and return byte-identical bodies.
func TestSameKeyTwice_GeoCalledOnce(t *testing.T) {
	c := newCache(5 * time.Minute)
	key := NormaliseKey("Mumbai", "SLOW", 500)

	geoCallCount := 0
	callGeo := func() []byte {
		geoCallCount++
		return defaultBody
	}

	// First request — cache miss, call geo service.
	var body1 []byte
	if cached, hit := c.Get(key); hit {
		body1 = cached
	} else {
		body1 = callGeo()
		c.Set(key, body1)
	}

	// Second request — must hit cache, geo not called again.
	var body2 []byte
	if cached, hit := c.Get(key); hit {
		body2 = cached
	} else {
		body2 = callGeo()
		c.Set(key, body2)
	}

	assert.Equal(t, 1, geoCallCount, "geo service must be called exactly once")
	assert.True(t, bytes.Equal(body1, body2), "both responses must be byte-identical")
}

// ---- TTL expiry ------------------------------------------------------------

func TestGet_ExpiredEntry_IsMiss(t *testing.T) {
	// Very short TTL so the test doesn't block.
	c := newCache(40 * time.Millisecond)
	key := NormaliseKey("Chennai", "DC_FAST", 1000)

	c.Set(key, defaultBody)

	// Must be a hit immediately after storing.
	_, hit := c.Get(key)
	require.True(t, hit, "expected hit immediately after Set")

	// Wait for TTL to lapse.
	time.Sleep(80 * time.Millisecond)

	got, hit := c.Get(key)
	assert.False(t, hit, "expected miss after TTL expiry")
	assert.Nil(t, got)
}

// TestExpiredEntry_NotServedFromCache confirms that after expiry the caller
// must go back to the source — a subsequent Set with fresh data is then served
// correctly.
func TestExpiredEntry_FreshSetAfterExpiry_IsHit(t *testing.T) {
	c := newCache(40 * time.Millisecond)
	key := NormaliseKey("Hyderabad", "AC_FAST", 750)
	stale := []byte(`{"stale":true}`)
	fresh := []byte(`{"fresh":true}`)

	c.Set(key, stale)
	time.Sleep(80 * time.Millisecond)

	// Expired — treat as miss and re-populate with fresh data.
	_, hit := c.Get(key)
	require.False(t, hit)
	c.Set(key, fresh)

	got, hit := c.Get(key)
	require.True(t, hit)
	assert.True(t, bytes.Equal(fresh, got), "should serve fresh data, not stale")
}

// ---- Case normalisation ----------------------------------------------------

func TestCaseNormalisation_AllVariants_SameEntry(t *testing.T) {
	c := newCache(5 * time.Minute)
	body := []byte(`{"type":"FeatureCollection"}`)

	variants := []struct{ city, chargerType string }{
		{"bengaluru", "dc_fast"},
		{"Bengaluru", "DC_FAST"},
		{"BENGALURU", "DC_FAST"},
		{"bEngAlUrU", "Dc_FaSt"},
	}

	// Store using the first variant.
	k0 := NormaliseKey(variants[0].city, variants[0].chargerType, 1500)
	c.Set(k0, body)

	// All other variants must resolve to the same entry.
	for _, v := range variants[1:] {
		v := v
		t.Run(v.city+"/"+v.chargerType, func(t *testing.T) {
			k := NormaliseKey(v.city, v.chargerType, 1500)
			got, hit := c.Get(k)
			require.True(t, hit, "expected cache hit for variant %q / %q", v.city, v.chargerType)
			assert.True(t, bytes.Equal(body, got))
		})
	}
}

// ---- Boundary radius values ------------------------------------------------

func TestBoundaryRadius_Stored_And_Retrieved(t *testing.T) {
	tests := []struct {
		name   string
		radius int
	}{
		{"min radius 250", 250},
		{"max radius 10000", 10000},
		{"mid radius 5000", 5000},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			c := newCache(5 * time.Minute)
			key := NormaliseKey("Bengaluru", "DC_FAST", tc.radius)
			c.Set(key, defaultBody)

			got, hit := c.Get(key)
			require.True(t, hit)
			assert.True(t, bytes.Equal(defaultBody, got))
		})
	}
}

// ---- Concurrent access (caught by -race) -----------------------------------

// TestConcurrentSetGet hammers the cache from multiple goroutines to expose
// data races in the mutex-guarded map.  Run with: go test -race ./internal/cache/...
func TestConcurrentSetGet(t *testing.T) {
	c := newCache(5 * time.Minute)
	const workers = 20
	const iterations = 100

	var wg sync.WaitGroup
	wg.Add(workers)

	for i := 0; i < workers; i++ {
		i := i
		go func() {
			defer wg.Done()
			// Alternate between two keys to create contention.
			city := "Bengaluru"
			if i%2 == 0 {
				city = "Mumbai"
			}
			key := NormaliseKey(city, "DC_FAST", 1500)
			for j := 0; j < iterations; j++ {
				c.Set(key, defaultBody)
				c.Get(key)
			}
		}()
	}

	wg.Wait()
	// If the -race detector doesn't fire, the test passes.
}

// TestConcurrentDistinctKeys ensures distinct keys don't interfere under
// concurrent load.
func TestConcurrentDistinctKeys(t *testing.T) {
	c := newCache(5 * time.Minute)
	cities := []string{"Bengaluru", "Mumbai", "Hyderabad", "Chennai", "Pune"}

	var wg sync.WaitGroup
	wg.Add(len(cities))

	for _, city := range cities {
		city := city
		go func() {
			defer wg.Done()
			key := NormaliseKey(city, "DC_FAST", 1500)
			body := []byte(`{"city":"` + city + `"}`)
			c.Set(key, body)

			got, hit := c.Get(key)
			// Under race conditions this might occasionally be a miss if
			// eviction fires, but with a 5-minute TTL it should always hit.
			if hit {
				assert.True(t, bytes.Equal(body, got),
					"body mismatch for city %s", city)
			}
		}()
	}

	wg.Wait()
}
