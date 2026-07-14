/**
 * SidePanel — displays ranked recommendation candidates with sorting,
 * a display-count slider, CSV export, and click-to-select.
 *
 * Requirements:
 * - Req 6 AC-1: Candidate list, sortable by rank / score.
 * - Req 6 AC-2: Slider 10–200 step 10, default 50, to control displayed count.
 * - Req 6 AC-3: Click row → pan+zoom map to zoom-15, highlight marker.
 * - Req 6 AC-6: CSV export of the top-N displayed candidates.
 */

import { useCallback, useId, useMemo, useState } from 'react';
import type { CandidateFeature, RecommendationResponse } from '../../types/geojson';
import { CandidateRow } from './CandidateRow';

// ---------------------------------------------------------------------------
// Sort types
// ---------------------------------------------------------------------------

type SortColumn = 'rank' | 'score';
type SortDir = 'asc' | 'desc';

const DISPLAY_COUNT_MIN = 10;
const DISPLAY_COUNT_MAX = 200;
const DISPLAY_COUNT_DEFAULT = 50;
const DISPLAY_COUNT_STEP = 10;

// ---------------------------------------------------------------------------
// CSV helpers
// ---------------------------------------------------------------------------

function escapeCsvCell(value: string | number | boolean | null): string {
  const s = value === null ? '' : String(value);
  // Wrap in quotes if the cell contains a comma, quote, or newline.
  return s.includes(',') || s.includes('"') || s.includes('\n')
    ? `"${s.replace(/"/g, '""')}"`
    : s;
}

function buildCsv(candidates: CandidateFeature[]): string {
  const header = [
    'rank',
    'latitude',
    'longitude',
    'score',
    'population_1km',
    'nearest_charger_distance_m',
    'road_type',
    'parking_available',
    'nearest_mall_distance_m',
  ].join(',');

  const rows = candidates.map((f) => {
    const p = f.properties;
    const [lng, lat] = f.geometry.coordinates;
    return [
      p.rank,
      lat,
      lng,
      p.score,
      p.population_1km,
      p.nearest_charger_distance_m,
      p.road_type,
      p.parking_available,
      p.nearest_mall_distance_m,
    ]
      .map(escapeCsvCell)
      .join(',');
  });

  return [header, ...rows].join('\n');
}

function downloadCsv(csv: string, filename: string): void {
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
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
  /** Rank of the currently selected candidate (from marker click or row click). */
  selectedRank: number | null;
  /** Called when the user clicks a row. */
  onCandidateSelect: (candidate: CandidateFeature) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SidePanel({ response, selectedRank, onCandidateSelect }: SidePanelProps) {
  const [sortCol, setSortCol] = useState<SortColumn>('rank');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [displayCount, setDisplayCount] = useState(DISPLAY_COUNT_DEFAULT);

  const sliderId = useId();

  // Toggle sort: same column flips direction; different column resets to asc.
  const handleSort = useCallback(
    (col: SortColumn) => {
      if (col === sortCol) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortCol(col);
        setSortDir('asc');
      }
    },
    [sortCol],
  );

  // Compute the slice that is actually displayed.
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
    const city = response?.city ?? 'city';
    const csv = buildCsv(visibleCandidates);
    downloadCsv(csv, `chargewise_${city.toLowerCase()}_candidates.csv`);
  }, [visibleCandidates, response]);

  // ---------------------------------------------------------------------------
  // Empty state
  // ---------------------------------------------------------------------------
  if (!response) {
    return (
      <aside
        aria-label="Candidate list"
        style={{
          padding: 16,
          color: '#888',
          fontSize: 14,
          textAlign: 'center',
          height: '100%',
          boxSizing: 'border-box',
        }}
      >
        Run a recommendation to see candidates here.
      </aside>
    );
  }

  const ariaSort = (col: SortColumn) =>
    sortCol === col ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none';

  const sortLabel = (col: SortColumn) => {
    const arrow = sortCol === col ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';
    return col === 'rank' ? `Rank${arrow}` : `Score${arrow}`;
  };

  return (
    <aside
      aria-label="Candidate list"
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        fontSize: 13,
        color: '#e0e0e0',
      }}
    >
      {/* ── Header ── */}
      <div style={{ padding: '10px 12px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>
          {response.city} — {response.chargerType}
        </div>
        <div style={{ color: '#888', fontSize: 12 }}>
          {response.total_candidates} candidates scored
        </div>
      </div>

      {/* ── Sort controls ── */}
      <div
        role="toolbar"
        aria-label="Sort controls"
        style={{
          display: 'flex',
          gap: 6,
          padding: '8px 12px',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        <span style={{ color: '#aaa', alignSelf: 'center', fontSize: 12 }}>Sort:</span>
        {(['rank', 'score'] as const).map((col) => (
          <button
            key={col}
            type="button"
            aria-sort={ariaSort(col)}
            onClick={() => handleSort(col)}
            style={{
              padding: '3px 10px',
              border: '1px solid rgba(255,255,255,0.2)',
              borderRadius: 4,
              background: sortCol === col ? 'rgba(255,255,255,0.15)' : 'transparent',
              color: '#e0e0e0',
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: sortCol === col ? 700 : 400,
            }}
          >
            {sortLabel(col)}
          </button>
        ))}
      </div>

      {/* ── Display count slider ── */}
      <div
        style={{
          padding: '6px 12px 8px',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <label htmlFor={sliderId} style={{ color: '#aaa', fontSize: 12, whiteSpace: 'nowrap' }}>
          Show:
        </label>
        <input
          id={sliderId}
          type="range"
          min={DISPLAY_COUNT_MIN}
          max={DISPLAY_COUNT_MAX}
          step={DISPLAY_COUNT_STEP}
          value={displayCount}
          onChange={(e) => setDisplayCount(Number(e.target.value))}
          style={{ flex: 1 }}
          aria-valuetext={`${displayCount} candidates`}
        />
        <span style={{ fontSize: 12, minWidth: 28, textAlign: 'right' }}>{displayCount}</span>
      </div>

      {/* ── Candidate list ── */}
      <ul
        role="list"
        aria-label={`Top ${visibleCandidates.length} candidates`}
        style={{
          flex: 1,
          overflowY: 'auto',
          margin: 0,
          padding: '4px 8px',
        }}
      >
        {visibleCandidates.map((c) => (
          <CandidateRow
            key={c.properties.rank}
            candidate={c}
            isSelected={c.properties.rank === selectedRank}
            onClick={onCandidateSelect}
          />
        ))}
        {visibleCandidates.length === 0 && (
          <li style={{ listStyle: 'none', padding: 12, color: '#666', textAlign: 'center' }}>
            No candidates to display.
          </li>
        )}
      </ul>

      {/* ── CSV export ── */}
      <div
        style={{
          padding: '8px 12px',
          borderTop: '1px solid rgba(255,255,255,0.1)',
        }}
      >
        <button
          type="button"
          onClick={handleExport}
          disabled={visibleCandidates.length === 0}
          style={{
            width: '100%',
            padding: '6px 0',
            border: '1px solid rgba(255,255,255,0.25)',
            borderRadius: 5,
            background: 'rgba(255,255,255,0.08)',
            color: '#e0e0e0',
            cursor: visibleCandidates.length === 0 ? 'not-allowed' : 'pointer',
            fontSize: 13,
            fontWeight: 500,
          }}
          aria-label={`Export ${visibleCandidates.length} candidates as CSV`}
        >
          ⬇ Export CSV ({visibleCandidates.length})
        </button>
      </div>
    </aside>
  );
}
