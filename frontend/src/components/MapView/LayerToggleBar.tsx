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

import { TriangleAlert } from 'lucide-react';
import type { LayerDataMap } from '../../hooks/useLayerData';
import type { BaseLayerId } from '../../types/domain';
import { BASE_LAYERS } from '../../types/domain';
import s from './LayerToggleBar.module.css';

interface LayerToggleBarProps {
  activeLayers: Set<BaseLayerId>;
  layerStates: LayerDataMap;
  onToggle: (id: BaseLayerId) => void;
  disabled?: boolean;
  /**
   * True when base layers are currently rendered at reduced opacity because
   * candidate results are the active visual focus.
   */
  isDimmed?: boolean;
  /** Called when the user wants to restore base layers to full opacity. */
  onRestoreOpacity?: () => void;
  /**
   * True when the app is in "recommend" mode.
   * The ev_chargers row is grayed out and non-interactive during this mode
   * to prevent manual toggles conflicting with the auto-hide behaviour.
   */
  isRecommendMode?: boolean;
}

export function LayerToggleBar({
  activeLayers,
  layerStates,
  onToggle,
  disabled,
  isDimmed = false,
  onRestoreOpacity,
  isRecommendMode = false,
}: LayerToggleBarProps) {
  // Only show the restore affordance when there are active layers to restore.
  const hasActiveLayers = activeLayers.size > 0;
  const showRestoreBanner = isDimmed && hasActiveLayers;
  return (
    <div
      role="toolbar"
      aria-label="Map layer visibility"
      aria-orientation="vertical"
      className={s.panel}
    >
      <p className={s.heading} aria-hidden="true">Layers</p>

      {/* Dimmed-opacity banner — shown when candidate results have caused
          base layers to be rendered at reduced opacity. Lets the user
          restore full opacity with one click without toggling each layer. */}
      {showRestoreBanner && (
        <div className={s.dimBanner}>
          <span className={s.dimLabel}>Dimmed for clarity</span>
          <button
            type="button"
            className={s.restoreBtn}
            onClick={onRestoreOpacity}
            aria-label="Restore base layers to full opacity"
          >
            Restore
          </button>
        </div>
      )}

      {BASE_LAYERS.map((layer) => {
        const isActive  = activeLayers.has(layer.id);
        const state     = layerStates.get(layer.id);
        const isLoading = state?.status === 'loading';
        const hasError  = state?.status === 'error';
        const errMsg    = hasError
          ? (state as { status: 'error'; message: string }).message
          : undefined;

        // In "recommend" mode, the ev_chargers row is locked — manually
        // toggling it would conflict with the auto-hide behaviour.
        const isEvChargers = layer.id === 'ev_chargers';
        const isRowDisabled = disabled || (isRecommendMode && isEvChargers);
        const rowTitle = isRecommendMode && isEvChargers
          ? 'Hidden while showing recommendations — close the panel to restore'
          : undefined;

        return (
          <button
            key={layer.id}
            type="button"
            role="switch"
            aria-checked={isActive && !(isRecommendMode && isEvChargers)}
            aria-label={`${layer.label} layer — ${isActive ? 'visible' : 'hidden'}`}
            aria-busy={isLoading || undefined}
            aria-disabled={isRowDisabled || undefined}
            title={rowTitle}
            className={[s.row, isRecommendMode && isEvChargers ? s['row--locked'] : ''].filter(Boolean).join(' ')}
            disabled={isRowDisabled}
            onClick={() => onToggle(layer.id)}
            // Thread the layer colour into the CSS module via a custom property
            style={{ '--layer-color': layer.color } as React.CSSProperties}
          >
            {/* Colour swatch */}
            <span className={s.swatch} aria-hidden="true" />

            {/* Label */}
            <span className={s.rowLabel}>{layer.label}</span>

            {/* Locked indicator — shown when ev_chargers is suppressed in recommend mode */}
            {isRecommendMode && isEvChargers && (
              <span className={s.lockedBadge} aria-hidden="true" title={rowTitle}>⊘</span>
            )}

            {/* Loading spinner — shown while GeoJSON is being fetched */}
            {isLoading && !isRecommendMode && (
              <span className={s.spinner} aria-hidden="true" />
            )}

            {/* Error badge — shown if the fetch failed */}
            {hasError && !isLoading && !isRecommendMode && (
              <span title={errMsg} aria-label="Failed to load">
                <TriangleAlert
                  size={10}
                  className={s.errorBadge}
                  aria-hidden="true"
                />
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
