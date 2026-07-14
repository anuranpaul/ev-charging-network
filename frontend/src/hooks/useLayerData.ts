/**
 * useLayerData — lazy, cached GeoJSON fetcher for the seven base map layers.
 *
 * Fetch semantics:
 *  - A layer is only fetched when it is activated for the first time for a
 *    given city (lazy).
 *  - Fetched data is stored in a ref keyed by `${city}/${layerId}` so
 *    re-toggling a previously activated layer within the same session never
 *    triggers a second network request (cached).
 *  - When the city changes the active set is cleared and the cache is wiped,
 *    so stale data from a previous city can't bleed through.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient } from '../services/apiClient';
import type { BaseLayerId } from '../types/domain';
import { layerApiPath } from '../types/domain';
import type { GeoJsonFeatureCollection } from '../types/geojson';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Per-layer fetch state: idle before first activation, then loading/error/ready. */
type LayerState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: GeoJsonFeatureCollection };

export type LayerDataMap = Map<BaseLayerId, LayerState>;

export interface UseLayerDataResult {
  /** All layer states, keyed by layer id. */
  layers: LayerDataMap;
  /** Set of layer ids currently enabled (visible). */
  activeLayers: Set<BaseLayerId>;
  /**
   * Toggle a layer on or off.
   * Activating a layer whose data hasn't been fetched yet starts the fetch.
   */
  toggleLayer: (id: BaseLayerId) => void;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useLayerData(city: string | null): UseLayerDataResult {
  const [activeLayers, setActiveLayers] = useState<Set<BaseLayerId>>(new Set());
  const [layers, setLayers] = useState<LayerDataMap>(new Map());

  // Cache keyed on `${city}/${layerId}` — survives re-renders, cleared on city change.
  const cacheRef = useRef<Map<string, GeoJsonFeatureCollection>>(new Map());

  // Clear all state when the city changes.
  useEffect(() => {
    setActiveLayers(new Set());
    setLayers(new Map());
    cacheRef.current = new Map();
  }, [city]);

  const fetchLayer = useCallback(
    async (id: BaseLayerId) => {
      if (!city) return;

      const cacheKey = `${city}/${id}`;

      // Serve from cache if available.
      const cached = cacheRef.current.get(cacheKey);
      if (cached) {
        setLayers((prev) => {
          const next = new Map(prev);
          next.set(id, { status: 'ready', data: cached });
          return next;
        });
        return;
      }

      // Mark as loading.
      setLayers((prev) => {
        const next = new Map(prev);
        next.set(id, { status: 'loading' });
        return next;
      });

      try {
        const data = await apiClient.get<GeoJsonFeatureCollection>(
          layerApiPath(id, city),
        );
        cacheRef.current.set(cacheKey, data);
        setLayers((prev) => {
          const next = new Map(prev);
          next.set(id, { status: 'ready', data });
          return next;
        });
      } catch (err) {
        const message =
          err instanceof Error ? err.message : `Failed to load ${id}.`;
        setLayers((prev) => {
          const next = new Map(prev);
          next.set(id, { status: 'error', message });
          return next;
        });
      }
    },
    [city],
  );

  const toggleLayer = useCallback(
    (id: BaseLayerId) => {
      setActiveLayers((prev) => {
        const next = new Set(prev);
        if (next.has(id)) {
          next.delete(id);
        } else {
          next.add(id);
          // Trigger fetch only when activating — not when hiding.
          void fetchLayer(id);
        }
        return next;
      });
    },
    [fetchLayer],
  );

  return { layers, activeLayers, toggleLayer };
}
