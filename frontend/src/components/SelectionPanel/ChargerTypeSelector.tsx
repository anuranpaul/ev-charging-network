/**
 * ChargerTypeSelector — three radio buttons for SLOW / FAST / DC_FAST.
 * The design uses radio inputs (one choice, three mutually exclusive options)
 * rather than a <select> so the options are always visible and immediately
 * actionable.
 */

import { useId } from 'react';
import {
  CHARGER_TYPES,
  CHARGER_TYPE_LABELS,
  type ChargerType,
} from '../../types/domain';

interface ChargerTypeSelectorProps {
  /** Currently selected value, or null when nothing is chosen yet. */
  value: ChargerType | null;
  onChange: (type: ChargerType) => void;
  /** Forwarded error message from the parent. */
  error?: string;
  disabled?: boolean;
}

export function ChargerTypeSelector({
  value,
  onChange,
  error,
  disabled,
}: ChargerTypeSelectorProps) {
  const groupId = useId();
  const errorId = `${groupId}-error`;

  return (
    <fieldset
      aria-describedby={error ? errorId : undefined}
      aria-invalid={error ? true : undefined}
      disabled={disabled}
    >
      <legend>Charger Type</legend>
      {CHARGER_TYPES.map((type) => {
        const inputId = `${groupId}-${type}`;
        return (
          <div key={type}>
            <input
              type="radio"
              id={inputId}
              name={groupId}
              value={type}
              checked={value === type}
              onChange={() => onChange(type)}
            />
            <label htmlFor={inputId}>{CHARGER_TYPE_LABELS[type]}</label>
          </div>
        );
      })}
      {error && (
        <p id={errorId} role="alert" aria-live="polite">
          {error}
        </p>
      )}
    </fieldset>
  );
}
