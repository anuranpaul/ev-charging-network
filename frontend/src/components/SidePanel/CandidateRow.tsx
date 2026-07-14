/**
 * CandidateRow — a single row in the candidate list.
 * Clicking the row triggers pan+zoom on the map and highlights the marker.
 */

import type { CandidateFeature } from '../../types/geojson';

interface CandidateRowProps {
  candidate: CandidateFeature;
  isSelected: boolean;
  onClick: (candidate: CandidateFeature) => void;
}

/** Score band colour — matches ScatterplotLayer fill colours. */
function scoreBandColor(score: number): string {
  if (score <= 33) return '#FF0000';
  if (score <= 66) return '#FFA500';
  return '#00AA00';
}

function fmtDist(v: number | null): string {
  return v === null ? 'None' : `${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} m`;
}

export function CandidateRow({ candidate, isSelected, onClick }: CandidateRowProps) {
  const p = candidate.properties;
  const [lng, lat] = candidate.geometry.coordinates;

  return (
    <li
      role="button"
      tabIndex={0}
      aria-pressed={isSelected}
      aria-label={`Rank ${p.rank}, score ${p.score}`}
      onClick={() => onClick(candidate)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick(candidate);
        }
      }}
      style={{
        listStyle: 'none',
        padding: '8px 10px',
        marginBottom: 4,
        borderRadius: 5,
        cursor: 'pointer',
        background: isSelected ? 'rgba(255,255,255,0.12)' : 'transparent',
        border: isSelected ? '1px solid rgba(255,255,255,0.35)' : '1px solid transparent',
        transition: 'background 0.12s',
      }}
    >
      {/* Top line: rank + score badge */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
        <span style={{ fontSize: 13, fontWeight: 700, minWidth: 28 }}>#{p.rank}</span>
        <span
          style={{
            fontSize: 12,
            fontWeight: 700,
            color: '#fff',
            background: scoreBandColor(p.score),
            borderRadius: 3,
            padding: '1px 6px',
          }}
          aria-label={`Score ${p.score}`}
        >
          {p.score}
        </span>
        <span style={{ fontSize: 11, color: '#aaa', marginLeft: 'auto' }}>
          {lat.toFixed(4)}, {lng.toFixed(4)}
        </span>
      </div>

      {/* Detail line */}
      <div style={{ fontSize: 11, color: '#bbb', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <span>Pop: {p.population_1km.toLocaleString()}</span>
        <span>Charger: {fmtDist(p.nearest_charger_distance_m)}</span>
        <span>{p.road_type || '—'}</span>
        <span>{p.parking_available ? '🅿' : '–'}</span>
        <span>Mall: {fmtDist(p.nearest_mall_distance_m)}</span>
      </div>
    </li>
  );
}
