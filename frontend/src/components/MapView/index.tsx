/**
 * MapView — MapLibre GL JS base map with Deck.gl interleaved overlay.
 *
 * Responsibilities:
 *  - Mount a MapLibre map filling its container, styled via VITE_MAP_STYLE_URL.
 *  - Fly to the selected city (zoom 12) whenever the city prop changes.
 *  - Mount a MapboxOverlay (interleaved=true) on top of MapLibre.
 *  - Render the seven base layers as GeoJsonLayers (toggled via LayerToggleBar).
 *  - Render recommendation candidates as a ScatterplotLayer (red/amber/green).
 *  - Show a tooltip when a candidate marker is clicked.
 *  - Pan + zoom to zoom-15 when the parent selects a candidate (side-panel click).
 *  - Highlight the selected candidate with a white 3 px border.
 */

import { GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { Map as MapLibreMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useCallback, useEffect, useRef, useState } from 'react';
import { config } from '../../config';
import { useLayerData } from '../../hooks/useLayerData';
import { BASE_LAYERS, CITY_CENTRES } from '../../types/domain';
import type { BaseLayerId } from '../../types/domain';
import type { ViewMode } from '../../App';
import type { CandidateFeature, GeoJsonFeatureCollection } from '../../types/geojson';
import { CandidateTooltip } from './CandidateTooltip';
import { LayerToggleBar } from './LayerToggleBar';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hexToRgb(hex: string): [number, number, number] {
  const c = hex.replace('#', '');
  return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
}

/** Map score 0-100 to RGBA per design doc colour bands. */
function scoreToColor(score: number): [number, number, number, number] {
  if (score <= 33) return [255, 0, 0, 200];     // red
  if (score <= 66) return [255, 165, 0, 200];   // amber
  return [0, 170, 0, 200];                       // green (#00AA00)
}

const DEFAULT_CENTRE: [number, number] = [77.5946, 12.9716];
const DEFAULT_ZOOM = 11;
const CITY_ZOOM = 12;
const CANDIDATE_ZOOM = 15;

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface MapViewProps {
  city: string | null;
  /** Ranked candidates from the last POST /recommendation response. */
  candidates: CandidateFeature[];
  /** Rank of the candidate currently highlighted (from side-panel click). */
  selectedRank: number | null;
  /** Called when the user clicks a marker — parent updates selectedRank. */
  onCandidateSelect: (candidate: CandidateFeature) => void;
  /**
   * When true, base layers are initially rendered at reduced opacity so
   * candidate markers are the clear visual focus. The user can restore
   * full opacity via the layer toggle bar.
   */
  hasResults?: boolean;
  /**
   * Current application view mode.
   * "explore" — base layers render per their toggle states.
   * "recommend" — candidate ScatterplotLayer is the primary visual;
   *   ev_chargers remains visible if the user has it toggled on.
   */
  viewMode?: ViewMode;
  /**
   * Total candidates scored (from response.total_candidates) — shown in the
   * mode label as "X of Y" where X = features.length, Y = total_candidates.
   */
  totalCandidates?: number;
}

// ---------------------------------------------------------------------------
// Tooltip state
// ---------------------------------------------------------------------------

interface TooltipState {
  candidate: CandidateFeature;
  x: number;
  y: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/** Full and dimmed opacity values for base GeoJSON layers. */
const BASE_LAYER_OPACITY_FULL   = 1.0;
const BASE_LAYER_OPACITY_DIMMED = 0.25;

export function MapView({ city, candidates, selectedRank, onCandidateSelect, hasResults = false, viewMode = 'explore', totalCandidates }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);

  const { layers, activeLayers, toggleLayer } = useLayerData(city);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  /**
   * Tracks whether the user has manually restored full opacity after a result
   * arrived. Set to false whenever hasResults flips true (new query), so each
   * fresh result set starts dimmed. Set to true when the user clicks "Restore".
   */
  const [opacityRestored, setOpacityRestored] = useState(false);

  // When a new result set arrives, reset the restored flag so base layers
  // dim again to foreground the fresh candidate markers.
  const prevHasResultsRef = useRef(false);
  useEffect(() => {
    if (hasResults && !prevHasResultsRef.current) {
      setOpacityRestored(false);
    }
    prevHasResultsRef.current = hasResults;
  }, [hasResults]);

  const isDimmed = hasResults && !opacityRestored;
  const layerOpacity = isDimmed ? BASE_LAYER_OPACITY_DIMMED : BASE_LAYER_OPACITY_FULL;

  const handleRestoreOpacity = useCallback(() => {
    setOpacityRestored(true);
  }, []);

  // ---------------------------------------------------------------------------
  // 1. Mount MapLibre + Deck.gl overlay once.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new MapLibreMap({
      container: containerRef.current,
      style: config.mapStyleUrl,
      center: DEFAULT_CENTRE,
      zoom: DEFAULT_ZOOM,
    });

    map.once('load', () => {
      const overlay = new MapboxOverlay({ interleaved: true, layers: [] });
      map.addControl(overlay);
      overlayRef.current = overlay;
    });

    mapRef.current = map;

    return () => {
      overlayRef.current?.finalize();
      overlayRef.current = null;
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // 2. Fly to city when city changes.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const centre = city ? (CITY_CENTRES[city] ?? DEFAULT_CENTRE) : DEFAULT_CENTRE;
    const zoom = city ? CITY_ZOOM : DEFAULT_ZOOM;
    map.flyTo({ center: centre, zoom, duration: 800, essential: true });
    setTooltip(null);
  }, [city]);

  // ---------------------------------------------------------------------------
  // 3. Pan to selected candidate when selectedRank changes (side-panel click).
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (selectedRank === null) return;
    const feature = candidates.find((c) => c.properties.rank === selectedRank);
    if (!feature) return;
    const [lng, lat] = feature.geometry.coordinates;
    mapRef.current?.flyTo({ center: [lng, lat], zoom: CANDIDATE_ZOOM, duration: 600, essential: true });
  }, [selectedRank, candidates]);

  // ---------------------------------------------------------------------------
  // 4. Rebuild all Deck.gl layers whenever relevant state changes.
  // ---------------------------------------------------------------------------
  const handleMarkerClick = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ({ object, x, y }: any) => {
      if (!object) return;
      onCandidateSelect(object as CandidateFeature);
      setTooltip({ candidate: object as CandidateFeature, x, y });
    },
    [onCandidateSelect],
  );

  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;

    // Base GeoJSON layers
    const baseLayers = BASE_LAYERS.flatMap((cfg) => {
      if (!activeLayers.has(cfg.id as BaseLayerId)) return [];
      const state = layers.get(cfg.id as BaseLayerId);
      if (state?.status !== 'ready') return [];
      const [r, g, b] = hexToRgb(cfg.color);

      return [
        new GeoJsonLayer({
          id: `base-${cfg.id}`,
          data: state.data as GeoJsonFeatureCollection,
          pickable: false,
          filled: true,
          getFillColor: [r, g, b, 180],
          stroked: true,
          getLineColor: [0, 0, 0, 100],
          lineWidthMinPixels: 1,
          pointRadiusMinPixels: 4,
          pointRadiusMaxPixels: 12,
          // Render point features as circles (required for GeoJSON Point geometry).
          pointType: 'circle',
          // Deck.gl opacity (0–1) multiplies all colour alphas uniformly.
          // Dimmed when candidates are present so markers read as primary.
          opacity: layerOpacity,
        }),
      ];
    });

    // Candidate ScatterplotLayer
    const candidateLayer =
      candidates.length > 0
        ? new ScatterplotLayer<CandidateFeature>({
            id: 'candidates',
            data: candidates,
            getPosition: (f) => f.geometry.coordinates,
            getRadius: 60,
            radiusUnits: 'meters',
            getFillColor: (f) => scoreToColor(f.properties.score),
            // White 3 px border for the selected marker; transparent otherwise
            getLineColor: (f) =>
              f.properties.rank === selectedRank ? [255, 255, 255, 255] : [0, 0, 0, 0],
            lineWidthMinPixels: 0,
            lineWidthUnits: 'pixels',
            getLineWidth: (f) => (f.properties.rank === selectedRank ? 3 : 0),
            stroked: true,
            pickable: true,
            onClick: handleMarkerClick,
            // Re-evaluate per-feature accessors when selectedRank changes
            updateTriggers: {
              getLineColor: selectedRank,
              getLineWidth: selectedRank,
            },
          })
        : null;

    overlay.setProps({
      layers: candidateLayer ? [...baseLayers, candidateLayer] : baseLayers,
    });
  }, [activeLayers, layers, candidates, selectedRank, handleMarkerClick, layerOpacity]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  // Build the human-readable mode label shown near the map.
  const modeLabel = viewMode === 'recommend'
    ? `Showing: ${candidates.length.toLocaleString()} recommended sites${totalCandidates !== undefined && totalCandidates !== candidates.length ? ` (of ${totalCandidates.toLocaleString()} scored)` : ''} + existing chargers`
    : 'Explore: toggle infrastructure layers below →';

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }} aria-label="Map">
      <div
        ref={containerRef}
        style={{ width: '100%', height: '100%' }}
        role="application"
        aria-label={city ? `Map centred on ${city}` : 'Map'}
      />

      {/* Mode indicator label — always visible so the switch is never silent */}
      <div
        style={{
          position: 'absolute',
          bottom: 16,
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 15,
          pointerEvents: 'none',
        }}
        aria-live="polite"
        aria-atomic="true"
      >
        <span className="cw-mode-label">
          {modeLabel}
        </span>
      </div>

      {/* Layer disclosure — collapsed by default so first-time users focus on
           the primary "Recommend locations" action, not layer exploration.
           Positioned bottom-right to stay out of the critical path visually. */}
      <div
        style={{
          position: 'absolute',
          bottom: 16,
          right: 10,
          zIndex: 10,
        }}
      >
        <details className="cw-layer-disclosure">
          <summary className="cw-layer-disclosure__summary">
            Show existing infrastructure
          </summary>
          <div className="cw-layer-disclosure__panel">
          <LayerToggleBar
              activeLayers={activeLayers}
              layerStates={layers}
              onToggle={toggleLayer}
              disabled={!city}
              isDimmed={isDimmed}
              onRestoreOpacity={handleRestoreOpacity}
            />
          </div>
        </details>
      </div>

      {/* Candidate tooltip */}
      {tooltip && (
        <CandidateTooltip
          candidate={tooltip.candidate}
          x={tooltip.x}
          y={tooltip.y}
          onClose={() => setTooltip(null)}
        />
      )}
    </div>
  );
}
