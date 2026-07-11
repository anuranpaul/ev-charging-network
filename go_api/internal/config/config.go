package config

import (
	"log"
	"os"
	"strconv"
)

type Config struct {
	Port                     string
	GeoServiceURL            string
	APIKey                   string
	CacheTTLSeconds          int
	CORSOrigins              string
	GeoServiceTimeoutSeconds int
}

func LoadConfig() Config {
	cfg := Config{
		Port:          getEnvDefault("PORT", "8080"),
		GeoServiceURL: os.Getenv("GEO_SERVICE_URL"),
		APIKey:        os.Getenv("API_KEY"),
		CORSOrigins:   getEnvDefault("CORS_ORIGINS", "*"),
	}

	if cfg.GeoServiceURL == "" {
		log.Println("FATAL: GEO_SERVICE_URL is required and was empty")
		os.Exit(1)
	}
	if cfg.APIKey == "" {
		log.Println("FATAL: API_KEY is required and was empty")
		os.Exit(1)
	}

	ttl, err := strconv.Atoi(getEnvDefault("CACHE_TTL_SECONDS", "300"))
	if err != nil || ttl < 1 || ttl > 86400 {
		log.Println("FATAL: CACHE_TTL_SECONDS must be an integer between 1 and 86400")
		os.Exit(1)
	}
	cfg.CacheTTLSeconds = ttl

	timeout, err := strconv.Atoi(getEnvDefault("GEO_SERVICE_TIMEOUT_SECONDS", "3"))
	if err != nil || timeout < 1 {
		log.Println("FATAL: GEO_SERVICE_TIMEOUT_SECONDS must be a positive integer")
		os.Exit(1)
	}
	cfg.GeoServiceTimeoutSeconds = timeout

	return cfg
}

func getEnvDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
