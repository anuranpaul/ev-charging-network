/**
 * RadiusInput — stepper control for search radius (250–10 000 m).
 *
 * Uses a plain text input (inputMode="numeric") instead of type="number"
 * to avoid platform-native spinner buttons, which look inconsistent across
 * browsers and OSes. Explicit − / + buttons step by RADIUS_STEP and clamp
 * to [RADIUS_MIN, RADIUS_MAX].
 *
 * Keyboard behaviour:
 *   - Type any integer directly into the field.
 *   - Arrow-up / Arrow-down nudge by RADIUS_STEP while the field is focused.
 *   - − / + buttons are also keyboard-reachable and work with Enter / Space.
 *
 * Validation is still deferred to form submit; the parent owns the error.
 */

import { Minus, Plus } from 'lucide-react';
import { useId } from 'react';
import { RADIUS_MAX, RADIUS_MIN } from '../../types/domain';
import s from './SelectionPanel.module.css';

const RADIUS_STEP = 250;

interface RadiusInputProps {
  value: number;
  onChange: (radius: number) => void;
  error?: string;
  disabled?: boolean;
}

function clamp(v: number) {
  return Math.max(RADIUS_MIN, Math.min(RADIUS_MAX, v));
}

export function RadiusInput({ value, onChange, error, disabled }: RadiusInputProps) {
  const id      = useId();
  const errorId = `${id}-error`;
  const hintId  = `${id}-hint`;

  const decrement = () => onChange(clamp(Math.round((value - RADIUS_STEP) / RADIUS_STEP) * RADIUS_STEP));
  const increment = () => onChange(clamp(Math.round((value + RADIUS_STEP) / RADIUS_STEP) * RADIUS_STEP));

  const handleTextChange = (raw: string) => {
    // Allow empty while typing; emit 0 so the parent can show a validation
    // error on submit rather than clamping mid-keystroke.
    const parsed = parseInt(raw, 10);
    onChange(Number.isNaN(parsed) ? 0 : parsed);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowUp')   { e.preventDefault(); increment(); }
    if (e.key === 'ArrowDown') { e.preventDefault(); decrement(); }
  };

  const atMin = value <= RADIUS_MIN;
  const atMax = value >= RADIUS_MAX;

  return (
    <div className={s.section}>
      <label htmlFor={id} className={s.label}>Search radius (metres)</label>

      {/* Stepper row: − | value input | + */}
      <div
        className={`${s.stepper}${error ? ` ${s['stepper--error']}` : ''}`}
        role="group"
        aria-label="Search radius stepper"
      >
        <button
          type="button"
          className={s.stepperBtn}
          onClick={decrement}
          disabled={disabled || atMin}
          aria-label={`Decrease radius by ${RADIUS_STEP.toLocaleString()} m`}
          tabIndex={0}
        >
          <Minus size={13} strokeWidth={2} aria-hidden="true" />
        </button>

        <input
          id={id}
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          className={s.stepperInput}
          value={value === 0 ? '' : value}
          onChange={(e) => handleTextChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          aria-describedby={[error ? errorId : '', hintId].filter(Boolean).join(' ') || undefined}
          aria-invalid={error ? true : undefined}
          aria-valuemin={RADIUS_MIN}
          aria-valuemax={RADIUS_MAX}
          aria-valuenow={value}
        />

        <button
          type="button"
          className={s.stepperBtn}
          onClick={increment}
          disabled={disabled || atMax}
          aria-label={`Increase radius by ${RADIUS_STEP.toLocaleString()} m`}
          tabIndex={0}
        >
          <Plus size={13} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>

      <p id={hintId} className={s.hint}>
        {RADIUS_MIN.toLocaleString()}–{RADIUS_MAX.toLocaleString()} m · step {RADIUS_STEP.toLocaleString()} m
      </p>

      {error && (
        <p id={errorId} className={s.error} role="alert" aria-live="polite">
          {error}
        </p>
      )}
    </div>
  );
}
