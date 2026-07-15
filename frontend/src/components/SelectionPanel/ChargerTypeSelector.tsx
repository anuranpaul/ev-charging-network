/**
 * ChargerTypeSelector — three custom radio buttons for SLOW / FAST / DC_FAST.
 */

import { useId } from 'react';
import { CHARGER_TYPES, CHARGER_TYPE_LABELS, type ChargerType } from '../../types/domain';
import s from './SelectionPanel.module.css';

interface ChargerTypeSelectorProps {
  value: ChargerType | null;
  onChange: (type: ChargerType) => void;
  error?: string;
  disabled?: boolean;
}

export function ChargerTypeSelector({ value, onChange, error, disabled }: ChargerTypeSelectorProps) {
  const groupId = useId();
  const errorId = `${groupId}-error`;

  return (
    <div className={s.section}>
      {/* Visible section label — uppercase caption above the radio group */}
      <p className={s.label} id={`${groupId}-heading`} aria-hidden="true">
        Charger type
      </p>

      <fieldset
        className={s.fieldset}
        aria-labelledby={`${groupId}-heading`}
        aria-describedby={error ? errorId : undefined}
        aria-invalid={error ? true : undefined}
        disabled={disabled}
      >
        {/* Visually hidden legend for assistive tech */}
        <legend className={s.legend}>Charger type</legend>

        <div className={s.radioGroup}>
          {CHARGER_TYPES.map((type) => {
            const inputId = `${groupId}-${type}`;
            return (
              <div key={type} className={s.radioRow}>
                <input
                  type="radio"
                  id={inputId}
                  className={s.radioInput}
                  name={groupId}
                  value={type}
                  checked={value === type}
                  onChange={() => onChange(type)}
                />
                <label htmlFor={inputId} className={s.radioLabel}>
                  {CHARGER_TYPE_LABELS[type]}
                </label>
              </div>
            );
          })}
        </div>
      </fieldset>

      {error && (
        <p id={errorId} className={s.error} role="alert" aria-live="polite">
          {error}
        </p>
      )}
    </div>
  );
}
