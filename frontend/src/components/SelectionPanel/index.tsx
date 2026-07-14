/**
 * SelectionPanel — top-level form that composes CityDropdown,
 * ChargerTypeSelector, and RadiusInput.
 *
 * Behaviour spec (Requirement 1):
 * - Cities loaded from GET /cities (public endpoint, no auth required).
 * - Charger types are the fixed enum SLOW / FAST / DC_FAST.
 * - Radius is a free integer input constrained to [250, 10 000].
 * - Validation fires on submit; inline error messages are shown next to
 *   each field.
 * - Changing city resets chargerType to null and radius to the default.
 */

import { type FormEvent, useCallback, useState } from 'react';
import {
  RADIUS_DEFAULT,
  RADIUS_MAX,
  RADIUS_MIN,
  type ChargerType,
  type SelectionState,
} from '../../types/domain';
import { ChargerTypeSelector } from './ChargerTypeSelector';
import { CityDropdown } from './CityDropdown';
import { RadiusInput } from './RadiusInput';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ValidationErrors {
  city?: string;
  chargerType?: string;
  radius?: string;
}

export interface SelectionPanelProps {
  /**
   * Called with the validated selection when the user submits the form.
   * Only fired when all three fields pass validation.
   */
  onSubmit: (selection: Required<SelectionState>) => void;
  /** Set to true while a recommendation request is in-flight. */
  isLoading?: boolean;
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

function validate(
  city: string | null,
  chargerType: ChargerType | null,
  radius: number,
): ValidationErrors {
  const errors: ValidationErrors = {};

  if (!city) {
    errors.city = 'Please select a city.';
  }

  if (!chargerType) {
    errors.chargerType = 'Please select a charger type.';
  }

  if (!Number.isInteger(radius) || radius < RADIUS_MIN || radius > RADIUS_MAX) {
    errors.radius = `Radius must be a whole number between ${RADIUS_MIN.toLocaleString()} and ${RADIUS_MAX.toLocaleString()} metres.`;
  }

  return errors;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SelectionPanel({ onSubmit, isLoading = false }: SelectionPanelProps) {
  const [city, setCity] = useState<string | null>(null);
  const [chargerType, setChargerType] = useState<ChargerType | null>(null);
  const [radius, setRadius] = useState<number>(RADIUS_DEFAULT);
  const [errors, setErrors] = useState<ValidationErrors>({});

  // When city changes, reset dependent fields and clear their errors.
  const handleCityChange = useCallback((newCity: string) => {
    setCity(newCity);
    setChargerType(null);
    setRadius(RADIUS_DEFAULT);
    setErrors((prev) => ({ ...prev, city: undefined, chargerType: undefined, radius: undefined }));
  }, []);

  const handleChargerTypeChange = useCallback((type: ChargerType) => {
    setChargerType(type);
    setErrors((prev) => ({ ...prev, chargerType: undefined }));
  }, []);

  const handleRadiusChange = useCallback((value: number) => {
    setRadius(value);
    setErrors((prev) => ({ ...prev, radius: undefined }));
  }, []);

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();

      const validationErrors = validate(city, chargerType, radius);
      if (Object.keys(validationErrors).length > 0) {
        setErrors(validationErrors);
        return;
      }

      // All fields are valid at this point.
      onSubmit({
        city: city as string,
        chargerType: chargerType as ChargerType,
        radius,
      });
    },
    [city, chargerType, radius, onSubmit],
  );

  return (
    <form onSubmit={handleSubmit} noValidate aria-label="Charging station recommendation parameters">
      <CityDropdown
        value={city}
        onChange={handleCityChange}
        error={errors.city}
        disabled={isLoading}
      />

      <ChargerTypeSelector
        value={chargerType}
        onChange={handleChargerTypeChange}
        error={errors.chargerType}
        disabled={isLoading}
      />

      <RadiusInput
        value={radius}
        onChange={handleRadiusChange}
        error={errors.radius}
        disabled={isLoading}
      />

      <button type="submit" disabled={isLoading} aria-busy={isLoading}>
        {isLoading ? 'Finding locations…' : 'Find Locations'}
      </button>
    </form>
  );
}
