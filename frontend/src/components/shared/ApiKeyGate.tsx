/**
 * ApiKeyGate — guards child content behind an API-key prompt.
 *
 * The key is held exclusively in the module-level variable inside
 * `apiClient.ts` (via `setApiKey`/`getApiKey`). It is never written to
 * localStorage, sessionStorage, or any cookie. On page reload the variable
 * is empty and the gate re-appears, prompting the user to re-enter the key
 * before any protected request is issued (Requirement 8A AC-4).
 */

import { type FormEvent, type ReactNode, useCallback, useRef, useState } from 'react';
import { setApiKey, getApiKey } from '../../services/apiClient';

interface ApiKeyGateProps {
  children: ReactNode;
}

export function ApiKeyGate({ children }: ApiKeyGateProps) {
  // Track whether the in-memory key has been set; initialise from the module
  // variable so a key set earlier in the same page session still works.
  const [isKeySet, setIsKeySet] = useState<boolean>(() => getApiKey() !== '');

  // Use a ref for the draft value — no need to trigger a re-render on every
  // keystroke; only the submit action matters.
  const draftRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');

  const handleSubmit = useCallback((event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = draftRef.current?.value.trim() ?? '';
    if (!trimmed) {
      setError('Please enter an API key.');
      return;
    }
    setApiKey(trimmed);
    setError('');
    setIsKeySet(true);
  }, []);

  if (isKeySet) {
    return <>{children}</>;
  }

  return (
    <div role="main" style={{ padding: '2rem', maxWidth: 420 }}>
      <h1>ChargeWise</h1>
      <p>Enter your API key to continue.</p>
      <form onSubmit={handleSubmit} noValidate>
        <label htmlFor="api-key-input">
          API Key
          <input
            id="api-key-input"
            ref={draftRef}
            type="password"
            autoComplete="current-password"
            aria-describedby={error ? 'api-key-error' : undefined}
            aria-invalid={!!error || undefined}
            style={{ display: 'block', marginTop: '0.5rem', width: '100%' }}
          />
        </label>
        {error && (
          <p id="api-key-error" role="alert" style={{ color: 'red' }}>
            {error}
          </p>
        )}
        <button type="submit" style={{ marginTop: '1rem' }}>
          Continue
        </button>
      </form>
    </div>
  );
}
