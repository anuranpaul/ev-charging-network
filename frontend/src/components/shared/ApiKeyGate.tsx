/**
 * ApiKeyGate — guards child content behind an API-key prompt.
 *
 * The key is held exclusively in the module-level variable inside
 * `apiClient.ts` (via `setApiKey`/`getApiKey`). It is never written to
 * localStorage, sessionStorage, or any cookie. On page reload the variable
 * is empty and the gate re-appears (Requirement 8A AC-4).
 */

import { AlertCircle, KeyRound, LogIn } from 'lucide-react';
import { type FormEvent, type ReactNode, useCallback, useRef, useState } from 'react';
import { getApiKey, setApiKey } from '../../services/apiClient';
import s from './ApiKeyGate.module.css';

interface ApiKeyGateProps {
  children: ReactNode;
}

export function ApiKeyGate({ children }: ApiKeyGateProps) {
  const [isKeySet, setIsKeySet] = useState<boolean>(() => getApiKey() !== '');
  const draftRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');

  const handleSubmit = useCallback((event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = draftRef.current?.value.trim() ?? '';
    if (!trimmed) {
      setError('Enter an API key to continue.');
      return;
    }
    setApiKey(trimmed);
    setError('');
    setIsKeySet(true);
  }, []);

  if (isKeySet) return <>{children}</>;

  return (
    <div className={s.backdrop}>
      <div className={s.card}>

        {/* Header */}
        <div className={s.header}>
          <p className={s.headerTitle}>EV network India</p>
          <p className={s.headerSub}>Enter your API key to access the planning tool.</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} noValidate className={s.form}>
          <label htmlFor="api-key-input" className={s.label}>
            <KeyRound size={11} aria-hidden="true" style={{ verticalAlign: 'middle', marginRight: 4 }} />
            API key
          </label>

          <input
            id="api-key-input"
            ref={draftRef}
            type="password"
            autoComplete="current-password"
            aria-describedby={error ? 'api-key-error' : undefined}
            aria-invalid={error ? true : undefined}
            className={`${s.input}${error ? ` ${s['input--error']}` : ''}`}
          />

          {error && (
            <p id="api-key-error" role="alert" className={s.error}>
              <AlertCircle
                size={12}
                aria-hidden="true"
                className={s.errorIcon}
              />
              {error}
            </p>
          )}

          <button type="submit" className={s.submitBtn}>
            <LogIn size={15} aria-hidden="true" style={{ verticalAlign: 'middle', marginRight: 6 }} />
            Continue
          </button>
        </form>

      </div>
    </div>
  );
}
