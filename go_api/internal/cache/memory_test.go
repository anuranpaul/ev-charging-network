package cache

import (
	"testing"
	"time"
)

func TestGetMissOnFreshCache(t *testing.T) {
	c := NewMemoryCache(5 * time.Minute)
	key := NormaliseKey("Bengaluru", "DC_FAST", 1500)

	_, hit := c.Get(key)
	if hit {
		t.Fatal("expected cache miss on fresh cache, got hit")
	}
}

func TestSetThenGetHit(t *testing.T) {
	c := NewMemoryCache(5 * time.Minute)
	key := NormaliseKey("Bengaluru", "DC_FAST", 1500)
	body := []byte(`{"type":"FeatureCollection"}`)

	c.Set(key, body)

	got, hit := c.Get(key)
	if !hit {
		t.Fatal("expected cache hit, got miss")
	}
	if string(got) != string(body) {
		t.Fatalf("body mismatch: got %s, want %s", got, body)
	}
}

func TestSameKeyReturnsSameBody(t *testing.T) {
	c := NewMemoryCache(5 * time.Minute)
	key := NormaliseKey("Mumbai", "AC_SLOW", 500)
	body := []byte(`{"type":"FeatureCollection","features":[]}`)
	geoCallCount := 0

	// Simulate: check cache, miss → call geo service once, store result
	if _, hit := c.Get(key); !hit {
		geoCallCount++
		c.Set(key, body)
	}

	// Second lookup — should hit without calling geo service again
	got, hit := c.Get(key)
	if !hit {
		t.Fatal("second lookup expected cache hit, got miss")
	}
	if string(got) != string(body) {
		t.Fatalf("body mismatch on second lookup: got %s, want %s", got, body)
	}
	if geoCallCount != 1 {
		t.Fatalf("geo service called %d times, expected exactly 1", geoCallCount)
	}
}

func TestTTLExpiry(t *testing.T) {
	// Use a very short TTL so the test doesn't have to wait long.
	c := NewMemoryCache(50 * time.Millisecond)
	key := NormaliseKey("Pune", "DC_FAST", 1000)
	body := []byte(`{"type":"FeatureCollection"}`)

	c.Set(key, body)

	// Should be a hit immediately
	if _, hit := c.Get(key); !hit {
		t.Fatal("expected hit right after Set, got miss")
	}

	// Wait for TTL to expire
	time.Sleep(100 * time.Millisecond)

	if _, hit := c.Get(key); hit {
		t.Fatal("expected miss after TTL expiry, got hit")
	}
}

func TestCaseNormalisation(t *testing.T) {
	c := NewMemoryCache(5 * time.Minute)
	body := []byte(`{"type":"FeatureCollection"}`)

	// Store with one casing
	k1 := NormaliseKey("bengaluru", "dc_fast", 1500)
	c.Set(k1, body)

	// Retrieve with different casing — should hit after normalisation
	k2 := NormaliseKey("Bengaluru", "DC_FAST", 1500)
	got, hit := c.Get(k2)
	if !hit {
		t.Fatal("expected hit after case-normalised lookup, got miss")
	}
	if string(got) != string(body) {
		t.Fatalf("body mismatch: got %s, want %s", got, body)
	}
}
