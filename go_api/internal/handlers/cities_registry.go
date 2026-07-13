package handlers

// SupportedCities is the canonical city registry, shared across handlers.
var SupportedCities = []string{
	"Bengaluru",
	"Mumbai",
	"Hyderabad",
	"Chennai",
	"Pune",
}

// isSupportedCity returns true if city is in the supported list (case-sensitive,
// as normalised by NormaliseKey / validated at input).
func isSupportedCity(city string) bool {
	for _, c := range SupportedCities {
		if c == city {
			return true
		}
	}
	return false
}
