/**
 * SelectionPanel — floating card composing CityDropdown, ChargerTypeSelector,
 * and RadiusInput.
 *
 * Behaviour (Requirement 1):
 * - Cities from GET /cities; charger types fixed enum; radius 250–10 000 m.
 * - Validation on submit, inline messages per field.
 * - City change resets chargerType → null and radius → default.
 *
 * Presentation: styled via SelectionPanel.module.css; all token values come
 * from tokens.css — no inline style props in this file.
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
import s from './SelectionPanel.module.css';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ValidationErrors {
  city?: string;
  chargerType?: string;
  radius?: string;
}

export interface SelectionPanelProps {
  onSubmit: (selection: Required<SelectionState>) => void;
  isLoading?: boolean;
}

// ---------------------------------------------------------------------------
// Validation — instrument-style messages, no generic copy
// ---------------------------------------------------------------------------

function validate(
  city: string | null,
  chargerType: ChargerType | null,
  radius: number,
): ValidationErrors {
  const errors: ValidationErrors = {};

  if (!city) {
    errors.city = 'Select a city to continue.';
  }

  if (!chargerType) {
    errors.chargerType = 'Select a charger type.';
  }

  if (!Number.isInteger(radius) || radius < RADIUS_MIN || radius > RADIUS_MAX) {
    errors.radius =
      `Radius must be between ${RADIUS_MIN.toLocaleString()} and ` +
      `${RADIUS_MAX.toLocaleString()} metres.`;
  }

  return errors;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SelectionPanel({ onSubmit, isLoading = false }: SelectionPanelProps) {
  const [city, setCity]             = useState<string | null>(null);
  const [chargerType, setChargerType] = useState<ChargerType | null>(null);
  const [radius, setRadius]         = useState<number>(RADIUS_DEFAULT);
  const [errors, setErrors]         = useState<ValidationErrors>({});

  const handleCityChange = useCallback((newCity: string) => {
    setCity(newCity);
    setChargerType(null);
    setRadius(RADIUS_DEFAULT);
    setErrors({});
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
      onSubmit({
        city: city as string,
        chargerType: chargerType as ChargerType,
        radius,
      });
    },
    [city, chargerType, radius, onSubmit],
  );

  return (
    <form
      className={s.panel}
      onSubmit={handleSubmit}
      noValidate
      aria-label="Charging station recommendation parameters"
    >
      {/* Card title */}
      <div className={s.header}>
        <p className={s.title}>Find charging locations</p>
      </div>

      <div className={s.body}>
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
      </div>

      {/* Submit */}
      <div className={s.footer}>
        <button
          type="submit"
          className={s.submitBtn}
          disabled={isLoading}
          aria-busy={isLoading}
        >
          {isLoading ? 'Finding locations…' : 'Find locations'}
        </button>
      </div>
    </form>
  );
}
