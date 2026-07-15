/**
 * CandidateRow — a single interactive list row.
 *
 * Typography contract:
 *   rank, score, coordinates, distances → --font-mono (tabular data)
 *   road type, parking label            → --font-body (Inter, text values)
 *
 * Score-band background colours (#FF0000 / #FFA500 / #00AA00) are fixed
 * spec values applied inline — they are not design-system tokens.
 */

import type { CandidateFeature } from '../../types/geojson';
import s from './CandidateRow.module.css';

interface CandidateRowProps {
  candidate: CandidateFeature;
  isSelected: boolean;
  onClick: (candidate: CandidateFeature) => void;
}

/** Fixed spec colours — not tokens. */
function scoreBandBg(score: number): string {
  if (score <= 33) return '#FF0000';
  if (score <= 66) return '#FFA500';
  return '#00AA00';
}

function fmtDist(v: number | null): string {
  if (v === null) return '—';
  return v >= 1000
    ? `${(v / 1000).toFixed(1)} km`
    : `${Math.round(v)} m`;
}

export function CandidateRow({ candidate, isSelected, onClick }: CandidateRowProps) {
  const p = candidate.properties;
  const [lng, lat] = candidate.geometry.coordinates;
  const hasWarnings = p.warnings.length > 0;

  return (
    <li
      className={s.row}
      role="button"
      tabIndex={0}
      aria-pressed={isSelected}
      aria-label={`Rank ${p.rank}, score ${p.score}. Click to pan map to this location.`}
      onClick={() => onClick(candidate)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick(candidate);
        }
      }}
    >
      {/* ── Rank ── */}
      <span className={s.rank} aria-label={`Rank ${p.rank}`}>
        #{p.rank}
      </span>

      {/* ── Score badge ── */}
      <span
        className={s.scoreBadge}
        style={{ background: scoreBandBg(p.score) }}
        aria-label={`Score ${p.score}`}
      >
        {p.score}
        {hasWarnings && (
          <span
            className={s.warningDot}
            title={`Warnings: ${p.warnings.join('; ')}`}
            aria-label={`${p.warnings.length} warning${p.warnings.length > 1 ? 's' : ''}`}
            role="img"
          />
        )}
      </span>

      {/* ── Coordinates ── */}
      <span className={s.coords} aria-label={`${lat.toFixed(4)}°N ${lng.toFixed(4)}°E`}>
        {lat.toFixed(4)}, {lng.toFixed(4)}
      </span>

      {/* ── Detail row ── */}
      <div className={s.detail} aria-label="Location details">
        <span className={s.detailChip}>
          {p.road_type || 'no road'}
        </span>
        <span className={`${s.detailChip} ${s['detailChip--mono']}`}
          aria-label={`Population within 1 km: ${p.population_1km.toLocaleString()}`}>
          {p.population_1km.toLocaleString()} pop
        </span>
        <span className={`${s.detailChip} ${s['detailChip--mono']}`}
          aria-label={`Nearest charger: ${fmtDist(p.nearest_charger_distance_m)}`}>
          ⚡ {fmtDist(p.nearest_charger_distance_m)}
        </span>
        {p.parking_available && (
          <span className={s.detailChip} aria-label="Parking available">🅿</span>
        )}
      </div>
    </li>
  );
}
