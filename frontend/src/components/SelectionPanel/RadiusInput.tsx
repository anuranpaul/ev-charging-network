/**
 * RadiusInput — numeric field for search radius (250–10 000 m).
 * Validation is deferred to form submit; the parent owns the error string.
 */

import { useId } from 'react';
import { RADIUS_MAX, RADIUS_MIN } from '../../types/domain';
import s from './SelectionPanel.module.css';

interface RadiusInputProps {
  value: number;
  onChange: (radius: number) => void;
  error?: string;
  disabled?: boolean;
}

export function RadiusInput({ value, onChange, error, disabled }: RadiusInputProps) {
  const id      = useId();
  const errorId = `${id}-error`;
  const hintId  = `${id}-hint`;

  return (
    <div className={s.section}>
      <label htmlFor={id} className={s.label}>Search radius (metres)</label>

      <input
        id={id}
        type="number"
        className={s.input}
        inputMode="numeric"
        min={RADIUS_MIN}
        max={RADIUS_MAX}
        step={250}
        value={value}
        onChange={(e) => {
          const parsed = parseInt(e.target.value, 10);
          onChange(Number.isNaN(parsed) ? 0 : parsed);
        }}
        disabled={disabled}
        aria-describedby={[error ? errorId : '', hintId].filter(Boolean).join(' ') || undefined}
        aria-invalid={error ? true : undefined}
      />

      <p id={hintId} className={s.hint}>
        {RADIUS_MIN.toLocaleString()}–{RADIUS_MAX.toLocaleString()} m
      </p>

      {error && (
        <p id={errorId} className={s.error} role="alert" aria-live="polite">
          {error}
        </p>
      )}
    </div>
  );
}
