/**
 * useHealthCheck — polls GET /health every `intervalMs` milliseconds.
 *
 * GET /health is a public endpoint (no API key required). The hook uses
 * a plain fetch rather than apiClient so it never blocks on the key gate.
 *
 * Returns:
 *  'ok'       — last response was 200 with status "ok"
 *  'degraded' — last response was 200 with status "degraded"
 *  'unknown'  — no response received yet, or a network/non-200 error
 */

import { useEffect, useState } from 'react';
import { config } from '../config';

export type HealthStatus = 'ok' | 'degraded' | 'unknown';

interface HealthResponse {
  status: 'ok' | 'degraded';
}

const DEFAULT_INTERVAL_MS = 30_000; // poll every 30 s

export function useHealthCheck(intervalMs = DEFAULT_INTERVAL_MS): HealthStatus {
  const [status, setStatus] = useState<HealthStatus>('unknown');

  useEffect(() => {
    let cancelled = false;

    async function check() {
      try {
        const res = await fetch(`${config.apiUrl}/health`, {
          method: 'GET',
          // Short timeout — the indicator should update quickly or fall back.
          signal: AbortSignal.timeout(4_000),
        });
        if (cancelled) return;
        if (!res.ok) {
          setStatus('degraded');
          return;
        }
        const body = (await res.json()) as HealthResponse;
        if (!cancelled) {
          setStatus(body.status === 'ok' ? 'ok' : 'degraded');
        }
      } catch {
        if (!cancelled) setStatus('degraded');
      }
    }

    void check();
    const timer = setInterval(() => void check(), intervalMs);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [intervalMs]);

  return status;
}
