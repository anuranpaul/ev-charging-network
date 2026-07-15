/**
 * StatusReadout — full-width monospace bar at the bottom of the viewport.
 *
 * Shows three stats derived from the last POST /recommendation response:
 *   candidates  — total_candidates from the response envelope
 *   coverage    — reserved slot (populated when GET /analysis is wired up)
 *   avg score   — mean of all returned candidate scores, rounded
 *
 * Before any query has run all three show an em-dash (—), not zero.
 *
 * Animation
 * ---------
 * When a new response arrives each numeric value count-up animates from
 * its previous settled value to the new target (600 ms, ease-out cubic).
 * Simultaneously a single-frame colour flash (accent → primary, 500 ms)
 * marks the moment of arrival without lingering.
 *
 * Both animations are skipped entirely when
 *   @media (prefers-reduced-motion: reduce)
 * matches — the hook jumps to the target and the CSS class is never added.
 */

import { useEffect, useRef, useState } from 'react';
import { useCountUp } from '../../hooks/useCountUp';
import type { RecommendationResponse } from '../../types/geojson';
import s from './StatusReadout.module.css';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface StatusReadoutProps {
  response: RecommendationResponse | null;
  /**
   * Optional coverage percentage (0–100).
   * Not returned by POST /recommendation; pass when GET /analysis is wired.
   */
  coveragePct?: number | null;
}

// ---------------------------------------------------------------------------
// StatCell — one label+value pair
// ---------------------------------------------------------------------------

interface StatCellProps {
  label: string;
  /** Animated integer to display, or null for the em-dash state. */
  animatedValue: number | null;
  /** Format the final settled integer into a display string. */
  format: (n: number) => string;
  /** Whether this cell just received a new non-null value. */
  flash: boolean;
  /**
   * When true and animatedValue is null, render the value slot as
   * "n/a" instead of "—", signalling the field exists but is not yet
   * populated rather than "no result yet".
   */
  notAvailable?: boolean;
}

function StatCell({ label, animatedValue, format, flash, notAvailable }: StatCellProps) {
  const isEmpty = animatedValue === null;
  const valueClass = [
    s.value,
    isEmpty ? s['value--empty'] : '',
    flash && !isEmpty ? s.flash : '',
  ]
    .filter(Boolean)
    .join(' ');

  const displayText = isEmpty
    ? (notAvailable ? 'n/a' : '—')
    : format(animatedValue);

  return (
    <div className={s.item}>
      <span className={s.label}>{label}</span>
      <span
        className={valueClass}
        aria-live="polite"
        aria-atomic="true"
      >
        {displayText}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Derive target integers from the response
// ---------------------------------------------------------------------------

function deriveTargets(response: RecommendationResponse | null) {
  if (!response || response.features.length === 0) {
    return { candidates: null, avgScore: null };
  }
  const candidates = response.total_candidates;
  const avgScore = Math.round(
    response.features.reduce((sum, f) => sum + f.properties.score, 0) /
      response.features.length,
  );
  return { candidates, avgScore };
}

// ---------------------------------------------------------------------------
// Hook: track whether a value just newly became non-null
// ---------------------------------------------------------------------------

function useFlash(value: number | null): boolean {
  const [flashing, setFlashing] = useState(false);
  const prevRef = useRef<number | null>(null);

  useEffect(() => {
    // Only flash when transitioning from null → number, or number → different number.
    if (value !== null && value !== prevRef.current) {
      setFlashing(true);
      // Remove the class after the animation duration so it can re-trigger
      // on the next response.
      const t = setTimeout(() => setFlashing(false), 520);
      prevRef.current = value;
      return () => clearTimeout(t);
    }
    if (value === null) {
      prevRef.current = null;
    }
  }, [value]);

  return flashing;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StatusReadout({
  response,
  coveragePct = null,
}: StatusReadoutProps) {
  const { candidates, avgScore } = deriveTargets(response);

  // Animated display values — each counts up independently.
  const displayedCandidates = useCountUp(candidates);
  const displayedAvgScore   = useCountUp(avgScore);
  // Coverage is a float; multiply by 10, animate, divide back for one decimal.
  const coverageTenths      = coveragePct !== null ? Math.round(coveragePct * 10) : null;
  const displayedCovTenths  = useCountUp(coverageTenths);
  const displayedCoverage   = displayedCovTenths !== null ? displayedCovTenths / 10 : null;

  // Flash states — independent per cell so each lights up as its data arrives.
  const flashCandidates = useFlash(displayedCandidates);
  const flashCoverage   = useFlash(displayedCoverage !== null ? Math.round(displayedCoverage * 10) : null);
  const flashAvgScore   = useFlash(displayedAvgScore);

  return (
    <footer
      className={s.bar}
      role="contentinfo"
      aria-label="Query summary"
    >
      <StatCell
        label="candidates"
        animatedValue={displayedCandidates}
        format={(n) => n.toLocaleString()}
        flash={flashCandidates}
      />

      <span className={s.sep} aria-hidden="true" />

      <StatCell
        label="coverage"
        animatedValue={displayedCoverage !== null ? Math.round(displayedCoverage * 10) : null}
        format={(tenths) => `${(tenths / 10).toFixed(1)}%`}
        flash={flashCoverage}
        notAvailable={coveragePct === null}
      />

      <span className={s.sep} aria-hidden="true" />

      <StatCell
        label="avg score"
        animatedValue={displayedAvgScore}
        format={(n) => String(n)}
        flash={flashAvgScore}
      />
    </footer>
  );
}
