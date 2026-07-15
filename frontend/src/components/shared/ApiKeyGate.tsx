/**
 * ApiKeyGate — guards child content behind an API-key prompt.
 *
 * The key is held exclusively in the module-level variable inside
 * `apiClient.ts` (via `setApiKey`/`getApiKey`). It is never written to
 * localStorage, sessionStorage, or any cookie. On page reload the variable
 * is empty and the gate re-appears (Requirement 8A AC-4).
 *
 * Styled with the same dark panel treatment as SelectionPanel so the gate
 * reads as part of the same product rather than a browser-default form.
 */

import { type FormEvent, type ReactNode, useCallback, useRef, useState } from 'react';
import { getApiKey, setApiKey } from '../../services/apiClient';

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
    /* Full-viewport centred wrapper — matches --bg-base so there is no
       white flash before the app shell loads. */
    <div
      style={{
        minHeight: '100svh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-base)',
        fontFamily: 'var(--font-body)',
        padding: '24px 16px',
        boxSizing: 'border-box',
      }}
    >
      {/* Panel card — same surface/border/shadow as SelectionPanel */}
      <div
        style={{
          width: '100%',
          maxWidth: 360,
          background: 'var(--surface-panel)',
          border: '1px solid var(--line-grid)',
          borderRadius: 10,
          boxShadow: '0 4px 24px rgba(0,0,0,0.45), 0 1px 4px rgba(0,0,0,0.25)',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '16px 18px 14px',
            borderBottom: '1px solid var(--line-grid)',
          }}
        >
          <p
            style={{
              fontFamily: 'var(--font-display)',
              fontSize: 'var(--text-body)',
              fontWeight: 600,
              letterSpacing: '-0.01em',
              color: 'var(--text-primary)',
              margin: 0,
              lineHeight: 1.2,
            }}
          >
            EV network India
          </p>
          <p
            style={{
              fontFamily: 'var(--font-body)',
              fontSize: 'var(--text-caption)',
              color: 'var(--text-secondary)',
              margin: '4px 0 0',
              lineHeight: 1.4,
            }}
          >
            Enter your API key to access the planning tool.
          </p>
        </div>

        {/* Form body */}
        <form onSubmit={handleSubmit} noValidate style={{ padding: '14px 18px 16px' }}>
          <label
            htmlFor="api-key-input"
            style={{
              display: 'block',
              fontFamily: 'var(--font-body)',
              fontSize: 'var(--text-caption)',
              fontWeight: 500,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
              color: 'var(--text-secondary)',
              marginBottom: 6,
            }}
          >
            API key
          </label>

          <input
            id="api-key-input"
            ref={draftRef}
            type="password"
            autoComplete="current-password"
            aria-describedby={error ? 'api-key-error' : undefined}
            aria-invalid={error ? true : undefined}
            style={{
              width: '100%',
              boxSizing: 'border-box',
              padding: '7px 10px',
              background: 'var(--bg-base)',
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-body)',
              fontSize: 'var(--text-body)',
              border: `1px solid ${error ? '#e05252' : 'var(--line-grid)'}`,
              borderRadius: 5,
              outline: 'none',
              /* Focus ring must be set inline to avoid the .cw-root dependency */
              transition: 'border-color 0.15s, box-shadow 0.15s',
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = 'var(--accent-signal)';
              e.currentTarget.style.boxShadow = 'var(--focus-ring)';
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = error ? '#e05252' : 'var(--line-grid)';
              e.currentTarget.style.boxShadow = 'none';
            }}
          />

          {error && (
            <p
              id="api-key-error"
              role="alert"
              style={{
                fontFamily: 'var(--font-body)',
                fontSize: 'var(--text-caption)',
                color: '#e05252',
                margin: '5px 0 0',
                display: 'flex',
                alignItems: 'flex-start',
                gap: 5,
              }}
            >
              <span aria-hidden="true" style={{ fontSize: 10, lineHeight: 1.6, opacity: 0.8 }}>
                ↑
              </span>
              {error}
            </p>
          )}

          <button
            type="submit"
            style={{
              marginTop: 14,
              width: '100%',
              padding: '9px 0',
              background: 'var(--accent-signal)',
              color: '#14181F',
              fontFamily: 'var(--font-body)',
              fontSize: 'var(--text-body)',
              fontWeight: 600,
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer',
              letterSpacing: '0.01em',
              outline: 'none',
              transition: 'background 0.15s, box-shadow 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background =
                'color-mix(in srgb, var(--accent-signal) 85%, #fff 15%)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'var(--accent-signal)';
              e.currentTarget.style.boxShadow = 'none';
            }}
            onFocus={(e) => {
              e.currentTarget.style.boxShadow = 'var(--focus-ring)';
            }}
            onBlur={(e) => {
              e.currentTarget.style.boxShadow = 'none';
            }}
          >
            Continue
          </button>
        </form>
      </div>
    </div>
  );
}
