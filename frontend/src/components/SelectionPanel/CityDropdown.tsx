/**
 * CityDropdown — fetches available cities from GET /cities (public, no auth)
 * and renders a <select> element. Notifies the parent when the selection
 * changes.
 */

import { useEffect, useId, useRef, useState } from 'react';
import { apiClient } from '../../services/apiClient';
import type { CityInfo } from '../../types/domain';

interface CityDropdownProps {
  /** Currently selected city name, or null when nothing is chosen. */
  value: string | null;
  /** Called with the new city name whenever the user picks a different one. */
  onChange: (city: string) => void;
  /** Forwarded error message from the parent (shown below the control). */
  error?: string;
  disabled?: boolean;
}

type LoadState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; cities: CityInfo[] };

export function CityDropdown({
  value,
  onChange,
  error,
  disabled,
}: CityDropdownProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const [load, setLoad] = useState<LoadState>({ status: 'idle' });

  // Abort controller ref so we can cancel the fetch on unmount.
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    setLoad({ status: 'loading' });

    apiClient
      .get<CityInfo[]>('/cities', { signal: controller.signal })
      .then((cities) => {
        if (!controller.signal.aborted) {
          setLoad({ status: 'ready', cities });
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          err instanceof Error ? err.message : 'Failed to load cities.';
        setLoad({ status: 'error', message });
      });

    return () => {
      controller.abort();
    };
  }, []);

  const isLoading = load.status === 'loading' || load.status === 'idle';
  const fetchError = load.status === 'error' ? load.message : undefined;
  const cities = load.status === 'ready' ? load.cities : [];

  const displayError = fetchError ?? error;

  return (
    <div>
      <label htmlFor={id}>City</label>
      <select
        id={id}
        value={value ?? ''}
        onChange={(e) => {
          if (e.target.value) onChange(e.target.value);
        }}
        disabled={disabled || isLoading}
        aria-describedby={displayError ? errorId : undefined}
        aria-invalid={displayError ? true : undefined}
        aria-busy={isLoading}
      >
        <option value="" disabled>
          {isLoading ? 'Loading cities…' : 'Select a city'}
        </option>
        {cities.map((city) => (
          <option key={city.name} value={city.name}>
            {city.name}
          </option>
        ))}
      </select>
      {displayError && (
        <p id={errorId} role="alert" aria-live="polite">
          {displayError}
        </p>
      )}
    </div>
  );
}
