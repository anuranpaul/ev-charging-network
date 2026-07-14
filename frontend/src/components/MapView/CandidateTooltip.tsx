/**
 * CandidateTooltip — renders the detail popup for a clicked candidate marker.
 * Shows: rank, score, population within 1 km, nearest charger distance,
 * road type, parking availability, nearest mall distance (Req 6 AC-3).
 */

import type { CandidateFeature } from '../../types/geojson';
import { Tooltip } from '../shared/Tooltip';

interface CandidateTooltipProps {
  candidate: CandidateFeature;
  x: number;
  y: number;
  onClose: () => void;
}

function fmt(value: number | null, unit: string): string {
  if (value === null || value === undefined) return 'None';
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${unit}`;
}

export function CandidateTooltip({ candidate, x, y, onClose }: CandidateTooltipProps) {
  const p = candidate.properties;

  return (
    <Tooltip x={x} y={y} onClose={onClose}>
      <div style={{ fontWeight: 700, marginBottom: 6, fontSize: 14 }}>
        Rank #{p.rank} — Score {p.score}/100
      </div>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <tbody>
          <Row label="Population (1 km)" value={p.population_1km.toLocaleString()} />
          <Row label="Nearest charger" value={fmt(p.nearest_charger_distance_m, 'm')} />
          <Row label="Road type" value={p.road_type || '—'} />
          <Row label="Parking nearby" value={p.parking_available ? 'Yes' : 'No'} />
          <Row label="Nearest mall" value={fmt(p.nearest_mall_distance_m, 'm')} />
        </tbody>
      </table>
      {p.warnings.length > 0 && (
        <p style={{ marginTop: 6, fontSize: 11, color: '#ffb74d' }}>
          ⚠ {p.warnings.join(', ')}
        </p>
      )}
    </Tooltip>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <td style={{ paddingRight: 8, color: '#aaa', whiteSpace: 'nowrap', verticalAlign: 'top' }}>
        {label}
      </td>
      <td style={{ fontWeight: 500 }}>{value}</td>
    </tr>
  );
}
