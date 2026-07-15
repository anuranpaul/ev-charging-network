/**
 * CandidateTooltip — renders the detail popup for a clicked candidate marker.
 * Shows: rank, score, population within 1 km, nearest charger distance,
 * road type, parking availability, nearest mall distance (Req 6 AC-3).
 */

import {
  Car,
  MapPin,
  ShoppingBag,
  TriangleAlert,
  Users,
  Zap,
} from 'lucide-react';
import type { CandidateFeature } from '../../types/geojson';
import { Tooltip } from '../shared/Tooltip';

interface CandidateTooltipProps {
  candidate: CandidateFeature;
  x: number;
  y: number;
  onClose: () => void;
}

function fmtDist(v: number | null): string {
  if (v === null || v === undefined) return 'None';
  return v >= 1000
    ? `${(v / 1000).toFixed(1)} km`
    : `${Math.round(v)} m`;
}

/** Band colour matching the Deck.gl ScatterplotLayer palette. */
function scoreBandColor(score: number): string {
  if (score <= 33) return '#FF0000';
  if (score <= 66) return '#FFA500';
  return '#00AA00';
}

export function CandidateTooltip({ candidate, x, y, onClose }: CandidateTooltipProps) {
  const p = candidate.properties;

  return (
    <Tooltip x={x} y={y} onClose={onClose}>
      {/* Title row */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        marginBottom: 10,
        paddingBottom: 8,
        borderBottom: '1px solid var(--line-grid)',
      }}>
        <MapPin size={14} aria-hidden="true" style={{ color: 'var(--accent-signal)', flexShrink: 0 }} />
        <span style={{
          fontFamily: 'var(--font-display)',
          fontWeight: 700,
          fontSize: 'var(--text-body)',
          color: 'var(--text-primary)',
        }}>
          Rank #{p.rank}
        </span>
        <span style={{
          marginLeft: 'auto',
          fontFamily: 'var(--font-mono)',
          fontWeight: 700,
          fontSize: 'var(--text-body)',
          color: scoreBandColor(p.score),
          letterSpacing: 'var(--tracking-mono)',
        }}>
          {p.score}/100
        </span>
      </div>

      {/* Detail rows */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        <TooltipRow icon={<Users size={12} />} label="Population (1 km)" value={p.population_1km.toLocaleString()} />
        <TooltipRow icon={<Zap size={12} />}   label="Nearest charger"   value={fmtDist(p.nearest_charger_distance_m)} />
        <TooltipRow icon={<Car size={12} />}   label="Road type"         value={p.road_type || '—'} />
        <TooltipRow icon={<ShoppingBag size={12} />} label="Nearest mall" value={fmtDist(p.nearest_mall_distance_m)} />
      </div>

      {/* Warnings */}
      {p.warnings.length > 0 && (
        <div style={{
          marginTop: 8,
          paddingTop: 6,
          borderTop: '1px solid var(--line-grid)',
          display: 'flex',
          alignItems: 'flex-start',
          gap: 5,
          color: '#F0B429',
          fontSize: 'var(--text-caption)',
          fontFamily: 'var(--font-body)',
        }}>
          <TriangleAlert size={12} style={{ flexShrink: 0, marginTop: 1 }} aria-hidden="true" />
          {p.warnings.join(', ')}
        </div>
      )}
    </Tooltip>
  );
}

function TooltipRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      <span style={{ color: 'var(--text-secondary)', flexShrink: 0, display: 'flex' }} aria-hidden="true">
        {icon}
      </span>
      <span style={{
        color: 'var(--text-secondary)',
        fontFamily: 'var(--font-body)',
        fontSize: 'var(--text-caption)',
        whiteSpace: 'nowrap',
        flexShrink: 0,
      }}>
        {label}
      </span>
      <span style={{
        marginLeft: 'auto',
        color: 'var(--text-primary)',
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--text-caption)',
        letterSpacing: 'var(--tracking-mono)',
        fontVariantNumeric: 'tabular-nums',
        textAlign: 'right',
      }}>
        {value}
      </span>
    </div>
  );
}
