/**
 * LayerToggleBar — a horizontal bar of toggle buttons for the seven base
 * map layers. Each button shows the layer colour swatch, label, and current
 * status (loading spinner or error badge).
 */

import type { BaseLayerId } from '../../types/domain';
import { BASE_LAYERS } from '../../types/domain';
import type { LayerDataMap } from '../../hooks/useLayerData';

interface LayerToggleBarProps {
  activeLayers: Set<BaseLayerId>;
  layerStates: LayerDataMap;
  onToggle: (id: BaseLayerId) => void;
  /** Disable all toggles while the map is not yet ready. */
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
      aria-label="Map layer toggles"
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '6px',
        padding: '6px 8px',
        background: 'rgba(255,255,255,0.92)',
        backdropFilter: 'blur(4px)',
        borderRadius: '6px',
        boxShadow: '0 1px 4px rgba(0,0,0,0.18)',
      }}
    >
      {BASE_LAYERS.map((layer) => {
        const isActive = activeLayers.has(layer.id);
        const state = layerStates.get(layer.id);
        const isLoading = state?.status === 'loading';
        const hasError = state?.status === 'error';

        return (
          <button
            key={layer.id}
            type="button"
            role="switch"
            aria-checked={isActive}
            aria-label={`${isActive ? 'Hide' : 'Show'} ${layer.label}`}
            aria-busy={isLoading}
            onClick={() => onToggle(layer.id)}
            disabled={disabled}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '4px 10px',
              border: `2px solid ${layer.color}`,
              borderRadius: '4px',
              background: isActive ? layer.color : 'transparent',
              color: isActive ? '#fff' : '#333',
              cursor: disabled ? 'not-allowed' : 'pointer',
              fontSize: '12px',
              fontWeight: 500,
              opacity: disabled ? 0.6 : 1,
              transition: 'background 0.15s, color 0.15s',
              whiteSpace: 'nowrap',
            }}
          >
            {/* Colour swatch */}
            <span
              aria-hidden="true"
              style={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: layer.color,
                flexShrink: 0,
                outline: isActive ? '2px solid rgba(255,255,255,0.7)' : 'none',
              }}
            />

            {layer.label}

            {/* Loading indicator */}
            {isLoading && (
              <span
                aria-hidden="true"
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  border: '2px solid currentColor',
                  borderTopColor: 'transparent',
                  animation: 'spin 0.6s linear infinite',
                  flexShrink: 0,
                }}
              />
            )}

            {/* Error badge */}
            {hasError && !isLoading && (
              <span
                title={(state as { status: 'error'; message: string }).message}
                aria-label="Load error"
                style={{
                  fontSize: 11,
                  lineHeight: 1,
                  color: '#c00',
                  flexShrink: 0,
                }}
              >
                ⚠
              </span>
            )}
          </button>
        );
      })}

      {/* Keyframe animation injected once via a style tag */}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
