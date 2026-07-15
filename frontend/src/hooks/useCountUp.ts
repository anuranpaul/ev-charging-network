/**
 * useCountUp — animates a numeric value from its previous target to the
 * next target using requestAnimationFrame.
 *
 * Contract
 * --------
 *   - Returns the current display value (integer during the run, final
 *     value when complete).
 *   - When `target` is null the hook immediately returns null — no
 *     animation, no stale number.
 *   - Respects `prefers-reduced-motion`: if the media query matches,
 *     the target value is returned immediately with no animation.
 *   - Duration is configurable; defaults to 600 ms which keeps the
 *     transition perceptible but brief.
 *   - Uses an ease-out curve (1 - (1-t)^3) so the numbers decelerate
 *     into the final value rather than stopping abruptly.
 *
 * Why integer only: the readout shows candidate counts, integer scores,
 * and percentages formatted to one decimal — the caller formats the
 * final value; the hook just drives the numeric interpolation.
 */

import { useEffect, useRef, useState } from 'react';

const DURATION_MS = 600;

/** Returns true if the user prefers reduced motion. Evaluated once. */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/** Ease-out cubic: fast start, decelerates to rest. */
function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

export function useCountUp(
  target: number | null,
  durationMs: number = DURATION_MS,
): number | null {
  // The value that drives the displayed number.
  const [displayed, setDisplayed] = useState<number | null>(target);

  // Track the previous target so we always animate from the last settled
  // value, not from 0 when a second query fires.
  const fromRef       = useRef<number>(0);
  const rafRef        = useRef<number | null>(null);
  const startTimeRef  = useRef<number | null>(null);

  useEffect(() => {
    // Null target → show dash immediately, cancel any running animation.
    if (target === null) {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      fromRef.current = 0;
      startTimeRef.current = null;
      setDisplayed(null);
      return;
    }

    // Reduced-motion → jump straight to the value, no animation.
    if (prefersReducedMotion()) {
      fromRef.current = target;
      setDisplayed(target);
      return;
    }

    const from = fromRef.current;

    // Already at target (e.g. same response returned from cache) — skip.
    if (from === target) return;

    // Cancel any in-flight animation before starting a new one.
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    startTimeRef.current = null;

    function tick(now: number) {
      if (startTimeRef.current === null) startTimeRef.current = now;

      const elapsed = now - startTimeRef.current;
      const progress = Math.min(elapsed / durationMs, 1);
      const eased = easeOutCubic(progress);
      const current = Math.round(from + (target! - from) * eased);

      setDisplayed(current);

      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        // Settle exactly on target to avoid floating-point drift.
        setDisplayed(target!);
        fromRef.current = target!;
        rafRef.current = null;
        startTimeRef.current = null;
      }
    }

    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
    // durationMs is expected to be stable; target drives re-runs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  return displayed;
}
