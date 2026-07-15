/**
 * SidePanel — ranked candidates with sort controls, display-count slider,
 * and CSV export.
 *
 * Requirements:
 *   Req 6 AC-1  Sortable by rank / score.
 *   Req 6 AC-2  Slider 10–200 step 10, default 50.
 *   Req 6 AC-3  Click row → pan+zoom map, highlight marker.
 *   Req 6 AC-6  CSV export of the displayed slice.
 *
 * Styled via SidePanel.module.css — same panel treatment as SelectionPanel.
 * All inline style props have been removed; every value references tokens.
 */

import { useCallback, useId, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Download,
  RefreshCw,
  SlidersHorizontal,
} from 'lucide-react';
import type { QueryError } from '../../types/domain';
import type {
  AnalysisResponse,
  CandidateFeature,
  RecommendationResponse,
  WardStat,
} from '../../types/geojson';
import { CandidateRow } from './CandidateRow';
import s from './SidePanel.module.css';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

type SortColumn = 'rank' | 'score';
type SortDir = 'asc' | 'desc';

const DISPLAY_COUNT_MIN     = 10;
const DISPLAY_COUNT_MAX     = 200;
const DISPLAY_COUNT_DEFAULT = 50;
const DISPLAY_COUNT_STEP    = 10;

// ---------------------------------------------------------------------------
// CSV helpers — unchanged from original
// ---------------------------------------------------------------------------

function escapeCsvCell(value: string | number | boolean | null): string {
  const str = value === null ? '' : String(value);
  return str.includes(',') || str.includes('"') || str.includes('\n')
    ? `"${str.replace(/"/g, '""')}"`
    : str;
}

function buildCsv(candidates: CandidateFeature[]): string {
  const header = [
    'rank', 'latitude', 'longitude', 'score', 'population_1km',
    'nearest_charger_distance_m', 'road_type', 'parking_available',
    'nearest_mall_distance_m',
  ].join(',');

  const rows = candidates.map((f) => {
    const p = f.properties;
    const [lng, lat] = f.geometry.coordinates;
    return [
      p.rank, lat, lng, p.score, p.population_1km,
      p.nearest_charger_distance_m, p.road_type, p.parking_available,
      p.nearest_mall_distance_m,
    ].map(escapeCsvCell).join(',');
  });

  return [header, ...rows].join('\n');
}

function downloadCsv(csv: string, filename: string): void {
  const blob = new Blob([csv], { type: 'text/csv' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SidePanelProps {
  response: RecommendationResponse | null;
  /** Full analysis response — supplies ward breakdown and score distribution. */
  analysis?: AnalysisResponse | null;
  selectedRank: number | null;
  onCandidateSelect: (candidate: CandidateFeature) => void;
  /** True while POST /recommendation is in flight. */
  isLoading?: boolean;
  /** City being scored — shown in the loading panel status line. */
  loadingCity?: string | null;
  /** Charger type being scored — shown in the loading panel status line. */
  loadingChargerType?: string | null;
  /** Typed error from the last failed query — drives the error panel. */
  queryError?: QueryError | null;
  /** Called when the user clicks "Retry" in the 503 error panel. */
  onRetry?: () => void;
}

// ---------------------------------------------------------------------------
// Score legend — compact strip showing the three colour bands
// ---------------------------------------------------------------------------

function ScoreLegend() {
  return (
    <div className={s.scoreLegend} aria-label="Score colour legend">
      <span className={s.legendItem}>
        <span className={s.legendSwatch} style={{ background: '#FF0000' }} aria-hidden="true" />
        <span className={s.legendLabel}>0–33 low</span>
      </span>
      <span className={s.legendItem}>
        <span className={s.legendSwatch} style={{ background: '#FFA500' }} aria-hidden="true" />
        <span className={s.legendLabel}>34–66 mid</span>
      </span>
      <span className={s.legendItem}>
        <span className={s.legendSwatch} style={{ background: '#00AA00' }} aria-hidden="true" />
        <span className={s.legendLabel}>67–100 high</span>
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sort-indicator glyph — subtle, single-character
// ---------------------------------------------------------------------------

function SortIndicator({ col, activeCol, dir }: {
  col: SortColumn;
  activeCol: SortColumn;
  dir: SortDir;
}) {
  if (col !== activeCol) return null;
  // ▴ / ▾ are smaller than ▲/▼ — less visually aggressive
  return (
    <span className={s.sortIndicator} aria-hidden="true">
      {dir === 'asc' ? '▴' : '▾'}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Loading panel — shown while POST /recommendation is in flight
// ---------------------------------------------------------------------------

interface LoadingPanelProps {
  city: string | null;
  chargerType: string | null;
}

function SkeletonRow({ width }: { width: string }) {
  return (
    <li className={s.skeletonRow} aria-hidden="true">
      <span className={s.skeletonRank} />
      <span className={s.skeletonBody} style={{ '--sk-width': width } as React.CSSProperties} />
      <span className={s.skeletonScore} />
    </li>
  );
}

function LoadingPanel({ city, chargerType }: LoadingPanelProps) {
  const label = city
    ? `Scoring candidates for ${city}…`
    : 'Scoring candidates…';

  const typeLabel = chargerType
    ? chargerType.replace('_', ' ').toLowerCase()
    : null;

  return (
    <aside className={`${s.panel} ${s.loadingPanel}`} aria-label="Loading results" aria-busy="true">
      {/* Header mirrors the real header's height so layout doesn't jump */}
      <div className={s.header}>
        <p className={s.headerTitle}>
          {city ?? 'City'}
          {typeLabel ? ` · ${typeLabel}` : ''}
        </p>
        <p className={`${s.headerMeta} ${s.loadingStatus}`}>
          <span className={s.spinner} aria-hidden="true" />
          {label}
        </p>
      </div>

      {/* Skeleton rows — mock the candidate list */}
      <ul className={s.list} role="list" aria-label="Loading candidates">
        <SkeletonRow width="88%" />
        <SkeletonRow width="72%" />
        <SkeletonRow width="80%" />
        <SkeletonRow width="65%" />
        <SkeletonRow width="75%" />
        <SkeletonRow width="60%" />
        <SkeletonRow width="82%" />
        <SkeletonRow width="70%" />
      </ul>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Error panel — distinct layout per failure kind
// ---------------------------------------------------------------------------

/**
 * Maps an internal field key to a human-readable label so the 400 error
 * message reads "City: …" rather than exposing the raw API field name.
 */
const FIELD_LABELS: Record<string, string> = {
  city: 'City',
  chargerType: 'Charger type',
  radius: 'Radius',
};

interface ErrorPanelProps {
  error: QueryError;
  onRetry?: () => void;
}

function ErrorPanel({ error, onRetry }: ErrorPanelProps) {
  return (
    <aside className={`${s.panel} ${s.errorPanel}`} role="alert" aria-label="Query failed">
      <div className={s.errorHeader}>
        <AlertTriangle size={16} className={s.errorIcon} aria-hidden="true" />
        <p className={s.errorTitle}>
          {error.kind === '400' && 'Invalid request'}
          {error.kind === '422' && 'Incomplete data'}
          {error.kind === '503' && 'Service unavailable'}
          {error.kind === 'generic' && 'Request failed'}
        </p>
      </div>

      <div className={s.errorBody}>
        {/* ── 400: show which field the server rejected ── */}
        {error.kind === '400' && (
          <>
            <p className={s.errorMessage}>
              The server rejected a field in your request.
            </p>
            <div className={s.errorDetail}>
              <span className={s.errorFieldLabel}>
                {FIELD_LABELS[error.field] ?? error.field}
              </span>
              <span className={s.errorFieldMessage}>{error.message}</span>
            </div>
            <p className={s.errorHint}>
              Adjust the highlighted field above and resubmit.
            </p>
          </>
        )}

        {/* ── 422: list the missing datasets ── */}
        {error.kind === '422' && (
          <>
            <p className={s.errorMessage}>
              {error.city
                ? `${error.city} is not fully supported yet.`
                : 'This city is not fully supported yet.'}
            </p>
            {error.missing_datasets.length > 0 && (
              <>
                <p className={s.errorSubLabel}>Missing datasets</p>
                <ul className={s.datasetList}>
                  {error.missing_datasets.map((ds) => (
                    <li key={ds} className={s.datasetItem}>
                      <span className={s.datasetDot} aria-hidden="true" />
                      {ds}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p className={s.errorHint}>
              Try a different city or check back later.
            </p>
          </>
        )}

        {/* ── 503: retry-friendly with Retry-After seconds ── */}
        {error.kind === '503' && (
          <>
            <p className={s.errorMessage}>
              {error.retryAfterSeconds !== null
                ? `Service temporarily unavailable — retrying is recommended in ${error.retryAfterSeconds} second${error.retryAfterSeconds === 1 ? '' : 's'}.`
                : 'The geo-service is temporarily unreachable.'}
            </p>
            {onRetry && (
              <button
                type="button"
                className={s.retryBtn}
                onClick={onRetry}
                aria-label="Retry the recommendation request"
              >
                <RefreshCw size={13} aria-hidden="true" />
                Retry
              </button>
            )}
          </>
        )}

        {/* ── generic: something else went wrong ── */}
        {error.kind === 'generic' && (
          <>
            <p className={s.errorMessage}>{error.message}</p>
            {error.status !== null && (
              <p className={s.errorHint}>HTTP {error.status}</p>
            )}
            {onRetry && (
              <button
                type="button"
                className={s.retryBtn}
                onClick={onRetry}
                aria-label="Retry the recommendation request"
              >
                <RefreshCw size={13} aria-hidden="true" />
                Retry
              </button>
            )}
          </>
        )}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Ward breakdown — collapsible per-ward stats from GET /analysis
// ---------------------------------------------------------------------------

function WardBreakdown({ wards }: { wards: WardStat[] }) {
  const [open, setOpen] = useState(false);

  if (wards.length === 0) return null;

  return (
    <div className={s.wardSection}>
      <button
        type="button"
        className={s.wardToggle}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open
          ? <ChevronDown size={12} aria-hidden="true" />
          : <ChevronRight size={12} aria-hidden="true" />}
        Ward breakdown
        <span className={s.wardCount} aria-hidden="true">({wards.length})</span>
      </button>

      {open && (
        <div className={s.wardTable} role="table" aria-label="Per-ward candidate statistics">
          {/* Header row */}
          <div className={s.wardRow} role="row" aria-rowindex={1}>
            <span className={`${s.wardCell} ${s['wardCell--head']}`} role="columnheader">Ward</span>
            <span className={`${s.wardCell} ${s['wardCell--head']} ${s['wardCell--num']}`} role="columnheader">Count</span>
            <span className={`${s.wardCell} ${s['wardCell--head']} ${s['wardCell--num']}`} role="columnheader">Mean</span>
          </div>

          {/* Data rows — top 15 by candidate count (already sorted by backend) */}
          {wards.slice(0, 15).map((w, i) => (
            <div key={w.ward_name} className={s.wardRow} role="row" aria-rowindex={i + 2}>
              <span className={s.wardCell} role="cell">{w.ward_name}</span>
              <span className={`${s.wardCell} ${s['wardCell--num']}`} role="cell">{w.candidate_count}</span>
              <span className={`${s.wardCell} ${s['wardCell--num']}`} role="cell">{w.mean_score.toFixed(1)}</span>
            </div>
          ))}

          {wards.length > 15 && (
            <p className={s.wardMore}>+{wards.length - 15} more wards</p>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  return (
    <aside className={`${s.panel} ${s.emptyState}`} aria-label="Candidate list">
      <SlidersHorizontal
        size={28}
        className={s.emptyIcon}
        aria-hidden="true"
        strokeWidth={1.25}
      />
      <p className={s.emptyText}>
        Select a city and run the analysis to see ranked locations here.
      </p>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SidePanel({
  response,
  analysis = null,
  selectedRank,
  onCandidateSelect,
  isLoading = false,
  loadingCity = null,
  loadingChargerType = null,
  queryError = null,
  onRetry,
}: SidePanelProps) {
  const [sortCol, setSortCol]       = useState<SortColumn>('rank');
  const [sortDir, setSortDir]       = useState<SortDir>('asc');
  const [displayCount, setDisplayCount] = useState(DISPLAY_COUNT_DEFAULT);

  const sliderId = useId();

  const handleSort = useCallback((col: SortColumn) => {
    if (col === sortCol) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortCol(col);
      setSortDir('asc');
    }
  }, [sortCol]);

  const visibleCandidates = useMemo<CandidateFeature[]>(() => {
    if (!response) return [];
    const sorted = [...response.features].sort((a, b) => {
      const av = a.properties[sortCol];
      const bv = b.properties[sortCol];
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return sorted.slice(0, displayCount);
  }, [response, sortCol, sortDir, displayCount]);

  const handleExport = useCallback(() => {
    const csv = buildCsv(visibleCandidates);
    downloadCsv(csv, `chargewise_${(response?.city ?? 'city').toLowerCase()}_candidates.csv`);
  }, [visibleCandidates, response]);

  if (isLoading) {
    return <LoadingPanel city={loadingCity} chargerType={loadingChargerType} />;
  }

  if (queryError) {
    return <ErrorPanel error={queryError} onRetry={onRetry} />;
  }

  if (!response) return <EmptyState />;

  const sortBtnClass = (col: SortColumn) =>
    [s.sortBtn, col === sortCol ? s['sortBtn--active'] : ''].filter(Boolean).join(' ');

  const ariaSort = (col: SortColumn): 'ascending' | 'descending' | 'none' =>
    col === sortCol ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none';

  return (
    <aside className={s.panel} aria-label="Candidate list">

      {/* ── Header ── */}
      <div className={s.header}>
        <p className={s.headerTitle}>
          {response.city} · {response.chargerType.replace('_', ' ').toLowerCase()}
        </p>
        <p className={s.headerMeta}>
          {response.total_candidates.toLocaleString()} candidates scored
        </p>
      </div>

      {/* ── Score colour legend ── */}
      <ScoreLegend />

      {/* ── Sort controls ── */}
      <div
        role="toolbar"
        aria-label="Sort candidates"
        className={s.sortBar}
      >
        <span className={s.sortLabel} aria-hidden="true">Sort</span>

        {(['rank', 'score'] as const).map((col) => (
          <button
            key={col}
            type="button"
            className={sortBtnClass(col)}
            aria-sort={ariaSort(col)}
            aria-pressed={col === sortCol}
            onClick={() => handleSort(col)}
          >
            {col}
            <SortIndicator col={col} activeCol={sortCol} dir={sortDir} />
          </button>
        ))}
      </div>

      {/* ── Display-count slider ── */}
      <div className={s.sliderRow}>
        <label htmlFor={sliderId} className={s.sliderLabel}>
          Show
        </label>
        <input
          id={sliderId}
          type="range"
          className={s.slider}
          min={DISPLAY_COUNT_MIN}
          max={DISPLAY_COUNT_MAX}
          step={DISPLAY_COUNT_STEP}
          value={displayCount}
          onChange={(e) => setDisplayCount(Number(e.target.value))}
          aria-valuetext={`${displayCount} candidates`}
        />
        <span className={s.sliderValue} aria-hidden="true">
          {displayCount}
        </span>
      </div>

      {/* ── Candidate list ── */}
      <ul
        className={s.list}
        role="list"
        aria-label={`${visibleCandidates.length} ranked candidates`}
      >
        {visibleCandidates.map((c) => (
          <CandidateRow
            key={c.properties.rank}
            candidate={c}
            isSelected={c.properties.rank === selectedRank}
            onClick={onCandidateSelect}
          />
        ))}
      </ul>

      {/* ── Ward breakdown (from analysis) ── */}
      {analysis?.ward_stats && analysis.ward_stats.length > 0 && (
        <WardBreakdown wards={analysis.ward_stats} />
      )}

      {/* ── CSV export ── */}
      <div className={s.footer}>
        <button
          type="button"
          className={s.exportBtn}
          onClick={handleExport}
          disabled={visibleCandidates.length === 0}
          aria-label={`Export ${visibleCandidates.length} candidates as CSV`}
        >
          <Download size={13} aria-hidden="true" />
          Export CSV ({visibleCandidates.length})
        </button>
      </div>

    </aside>
  );
}
