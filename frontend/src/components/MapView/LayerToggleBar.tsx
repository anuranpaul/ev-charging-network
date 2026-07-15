/**
 * LayerToggleBar — a slim vertical list of layer-visibility toggles.
 *
 * Each row is a native <button role="switch"> so keyboard users can:
 *   - Tab into the panel
 *   - Arrow-key or Tab between rows
 *   - Press Enter or Space to toggle
 *
 * The per-layer colour is threaded through a CSS custom property
 * (--layer-color) so the CSS module can apply it to both the swatch
 * and the active-state left-accent bar without importing JS values.
 */

import type { LayerDataMap } from '../../hooks/useLayerData';
import type { BaseLayerId } from '../../types/domain';
import { BASE_LAYERS } from '../../types/domain';
import s from './LayerToggleBar.module.css';

interface LayerToggleBarProps {
  activeLayers: Set<BaseLayerId>;
  layerStates: LayerDataMap;
  onToggle: (id: BaseLayerId) => void;
  disabled?: boolean;
}

export function LayerToggleBar({
  activeLayers,
  layerStates,
  onToggle,
  disabled,
}: LayerToggleBarProps) {
  return (
    <div
      role="toolbar"
      aria-label="Map layer visibility"
      aria-orientation="vertical"
      className={s.panel}
    >
      <p className={s.heading} aria-hidden="true">Layers</p>

      {BASE_LAYERS.map((layer) => {
        const isActive  = activeLayers.has(layer.id);
        const state     = layerStates.get(layer.id);
        const isLoading = state?.status === 'loading';
        const hasError  = state?.status === 'error';
        const errMsg    = hasError
          ? (state as { status: 'error'; message: string }).message
          : undefined;

        return (
          <button
            key={layer.id}
            type="button"
            role="switch"
            aria-checked={isActive}
            aria-label={`${layer.label} layer — ${isActive ? 'visible' : 'hidden'}`}
            aria-busy={isLoading || undefined}
            className={s.row}
            disabled={disabled}
            onClick={() => onToggle(layer.id)}
            // Thread the layer colour into the CSS module via a custom property
            style={{ '--layer-color': layer.color } as React.CSSProperties}
          >
            {/* Colour swatch */}
            <span className={s.swatch} aria-hidden="true" />

            {/* Label */}
            <span className={s.rowLabel}>{layer.label}</span>

            {/* Loading spinner — shown while GeoJSON is being fetched */}
            {isLoading && (
              <span className={s.spinner} aria-hidden="true" />
            )}

            {/* Error badge — shown if the fetch failed */}
            {hasError && !isLoading && (
              <span
                className={s.errorBadge}
                title={errMsg}
                aria-label="Failed to load"
                role="img"
              >
                ⚠
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
