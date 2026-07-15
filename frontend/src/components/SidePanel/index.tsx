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
import type { CandidateFeature, RecommendationResponse } from '../../types/geojson';
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
  selectedRank: number | null;
  onCandidateSelect: (candidate: CandidateFeature) => void;
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
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  return (
    <aside className={`${s.panel} ${s.emptyState}`} aria-label="Candidate list">
      {/*
        A simple grid/location marker rendered as text — domain-relevant,
        no emoji variance risk, no external asset.
        U+25A6 = SQUARE WITH ORTHOGONAL CROSSHATCH FILL
      */}
      <span className={s.emptyIcon} aria-hidden="true">⊕</span>
      <p className={s.emptyText}>
        Select a city and run the analysis to see ranked locations here.
      </p>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SidePanel({ response, selectedRank, onCandidateSelect }: SidePanelProps) {
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

      {/* ── CSV export ── */}
      <div className={s.footer}>
        <button
          type="button"
          className={s.exportBtn}
          onClick={handleExport}
          disabled={visibleCandidates.length === 0}
          aria-label={`Export ${visibleCandidates.length} candidates as CSV`}
        >
          <span aria-hidden="true">↓</span>
          Export CSV ({visibleCandidates.length})
        </button>
      </div>

    </aside>
  );
}
