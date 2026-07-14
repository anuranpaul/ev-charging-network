/**
 * RadiusInput — numeric text field for the search radius (250–10 000 m).
 *
 * Validation is deferred to form submit (parent's responsibility for the
 * error message). The component only enforces that the raw string is a
 * non-empty integer; range checking lives in the parent so the same rule
 * is applied consistently.
 */

import { useId } from 'react';
import { RADIUS_MIN, RADIUS_MAX } from '../../types/domain';

interface RadiusInputProps {
  value: number;
  onChange: (radius: number) => void;
  /** Forwarded error message from the parent. */
  error?: string;
  disabled?: boolean;
}

export function RadiusInput({
  value,
  onChange,
  error,
  disabled,
}: RadiusInputProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  return (
    <div>
      <label htmlFor={id}>Search Radius (metres)</label>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        min={RADIUS_MIN}
        max={RADIUS_MAX}
        step={250}
        value={value}
        onChange={(e) => {
          const parsed = parseInt(e.target.value, 10);
          // Pass NaN through as 0 so the parent can detect an invalid entry;
          // the parent owns the validation error string.
          onChange(Number.isNaN(parsed) ? 0 : parsed);
        }}
        disabled={disabled}
        aria-describedby={
          [error ? errorId : '', hintId].filter(Boolean).join(' ') || undefined
        }
        aria-invalid={error ? true : undefined}
      />
      <p id={hintId}>
        Enter a value between {RADIUS_MIN.toLocaleString()} and{' '}
        {RADIUS_MAX.toLocaleString()} metres.
      </p>
      {error && (
        <p id={errorId} role="alert" aria-live="polite">
          {error}
        </p>
      )}
    </div>
  );
}
