/**
 * StatusReadout — full-width monospace bar at the bottom of the viewport.
 *
 * Shows five stats from the paired POST /recommendation + GET /analysis
 * responses:
 *
 *   candidates  — total_candidates (from recommendation response)
 *   coverage    — coverage_pct     (from analysis response, %)
 *   mean score  — score_mean       (server-computed over all candidates)
 *   median      — score_median
 *   p90         — score_p90
 *
 * Before any query has run all slots show an em-dash (—).
 * coverage shows "n/a" if the analysis response hasn't arrived yet.
 *
 * Animation
 * ---------
 * When a new response arrives each numeric value count-up animates from
 * its previous settled value to the new target (600 ms, ease-out cubic).
 * A single-frame colour flash (accent → primary, 500 ms) marks arrival.
 * Both animations respect prefers-reduced-motion.
 */

import { useEffect, useRef, useState } from 'react';
import { useCountUp } from '../../hooks/useCountUp';
import type { AnalysisResponse, RecommendationResponse } from '../../types/geojson';
import s from './StatusReadout.module.css';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface StatusReadoutProps {
  response: RecommendationResponse | null;
  /** Full analysis response — supplies score distribution and coverage. */
  analysis?: AnalysisResponse | null;
}

// ---------------------------------------------------------------------------
// StatCell — one label+value pair
// ---------------------------------------------------------------------------

interface StatCellProps {
  label: string;
  animatedValue: number | null;
  format: (n: number) => string;
  flash: boolean;
  /**
   * Render "n/a" instead of "—" when true and animatedValue is null —
   * signals the field exists but isn't populated yet.
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

  return (
    <div className={s.item}>
      <span className={s.label}>{label}</span>
      <span className={valueClass} aria-live="polite" aria-atomic="true">
        {isEmpty ? (notAvailable ? 'n/a' : '—') : format(animatedValue)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hook: fire once when a value newly becomes non-null or changes
// ---------------------------------------------------------------------------

function useFlash(value: number | null): boolean {
  const [flashing, setFlashing] = useState(false);
  const prevRef = useRef<number | null>(null);

  useEffect(() => {
    if (value !== null && value !== prevRef.current) {
      setFlashing(true);
      const t = setTimeout(() => setFlashing(false), 520);
      prevRef.current = value;
      return () => clearTimeout(t);
    }
    if (value === null) prevRef.current = null;
  }, [value]);

  return flashing;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StatusReadout({ response, analysis = null }: StatusReadoutProps) {
  const hasResponse = response !== null && response.features.length > 0;

  // ── Targets ──────────────────────────────────────────────────────────────
  // candidates — from recommendation envelope (total scored, not just displayed)
  const candidatesTarget = hasResponse ? response.total_candidates : null;

  // All score stats and coverage from analysis. Multiply floats × 10 so
  // useCountUp (integer-only) can animate one decimal place.
  const coverageTenths  = analysis?.coverage_pct  != null ? Math.round(analysis.coverage_pct  * 10) : null;
  const meanTenths      = analysis?.score_mean     != null ? Math.round(analysis.score_mean     * 10) : null;
  const medianTenths    = analysis?.score_median   != null ? Math.round(analysis.score_median   * 10) : null;
  const p90Tenths       = analysis?.score_p90      != null ? Math.round(analysis.score_p90      * 10) : null;

  // ── Animated values ───────────────────────────────────────────────────────
  const displayedCandidates  = useCountUp(candidatesTarget);
  const displayedCovTenths   = useCountUp(coverageTenths);
  const displayedMeanTenths  = useCountUp(meanTenths);
  const displayedMedTenths   = useCountUp(medianTenths);
  const displayedP90Tenths   = useCountUp(p90Tenths);

  // ── Flash states ──────────────────────────────────────────────────────────
  const flashCandidates = useFlash(displayedCandidates);
  const flashCoverage   = useFlash(displayedCovTenths);
  const flashMean       = useFlash(displayedMeanTenths);
  const flashMedian     = useFlash(displayedMedTenths);
  const flashP90        = useFlash(displayedP90Tenths);

  // Format helpers — divide back to restore the decimal
  const fmtPct    = (tenths: number) => `${(tenths / 10).toFixed(1)}%`;
  const fmtScore  = (tenths: number) => (tenths / 10).toFixed(1);

  return (
    <footer className={s.bar} role="contentinfo" aria-label="Query summary">

      {/* Pre-query prompt */}
      {!hasResponse && (
        <p className={s.prompt} aria-live="polite">
          Run a recommendation to see{' '}
          <span className={s.promptField}>candidate count</span>
          {', '}
          <span className={s.promptField}>coverage %</span>
          {', '}
          <span className={s.promptField}>mean</span>
          {', '}
          <span className={s.promptField}>median</span>
          {' and '}
          <span className={s.promptField}>p90 score</span>
          {' here.'}
        </p>
      )}

      {hasResponse && (
        <>
          <StatCell
            label="candidates"
            animatedValue={displayedCandidates}
            format={(n) => n.toLocaleString()}
            flash={flashCandidates}
          />

          <span className={s.sep} aria-hidden="true" />

          <StatCell
            label="coverage"
            animatedValue={displayedCovTenths}
            format={fmtPct}
            flash={flashCoverage}
            notAvailable={analysis == null}
          />

          <span className={s.sep} aria-hidden="true" />

          <StatCell
            label="mean score"
            animatedValue={displayedMeanTenths}
            format={fmtScore}
            flash={flashMean}
            notAvailable={analysis == null}
          />

          <span className={s.sep} aria-hidden="true" />

          <StatCell
            label="median"
            animatedValue={displayedMedTenths}
            format={fmtScore}
            flash={flashMedian}
            notAvailable={analysis == null}
          />

          <span className={s.sep} aria-hidden="true" />

          <StatCell
            label="p90"
            animatedValue={displayedP90Tenths}
            format={fmtScore}
            flash={flashP90}
            notAvailable={analysis == null}
          />
        </>
      )}
    </footer>
  );
}
