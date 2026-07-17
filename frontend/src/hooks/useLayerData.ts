/**
 * useLayerData — lazy, cached GeoJSON fetcher for the seven base map layers.
 *
 * Fetch semantics:
 *  - A layer is only fetched when it is activated (toggled on) AND a city
 *    is known. Toggling before a city is selected stores the intent; the fetch
 *    fires automatically once the city is picked.
 *  - Fetched data is stored in a ref keyed by `${city}/${layerId}` so
 *    re-toggling a previously activated layer within the same session never
 *    triggers a second network request (cached).
 *  - When the city changes the fetch state and cache are wiped, but the
 *    active (toggled) set is preserved — fetches for active layers re-fire
 *    automatically for the new city.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient } from '../services/apiClient';
import type { BaseLayerId } from '../types/domain';
import { layerApiPath } from '../types/domain';
import type { GeoJsonFeatureCollection } from '../types/geojson';

/** The layer that is activated by default whenever a city is selected.
 * @deprecated No longer auto-activated; kept as a named constant for reference.
 */
// const DEFAULT_ACTIVE_LAYER: BaseLayerId = 'ev_chargers';

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

  // Snapshot of active layers at the time the city changes, so we can
  // re-trigger fetches for all currently-toggled layers when a city is picked.
  const activeLayersSnapshotRef = useRef<Set<BaseLayerId>>(new Set());

  // Keep the snapshot ref in sync on every render so it always reflects
  // the latest active set when the city-change effect fires.
  activeLayersSnapshotRef.current = activeLayers;

  // When the city changes: clear stale fetch state and cache.
  // The active layer set is preserved so toggles made before a city is
  // selected survive — their fetches fire in the effect below once city is set.
  useEffect(() => {
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

        // Confirm feature count in the browser console so the known dataset
        // sizes (e.g. 39 ev_charger nodes for Bengaluru) can be verified.
        console.log(
          `[useLayerData] ${city}/${id}: ${data.features.length} features received from ${layerApiPath(id, city)}`,
        );

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

  // When a city is selected (or changes), fetch data for all layers that are
  // currently toggled on. This means toggles made before a city is picked
  // (pre-city exploration) are honoured as soon as the city arrives.
  useEffect(() => {
    if (!city) return;
    for (const id of activeLayersSnapshotRef.current) {
      void fetchLayer(id);
    }
  // fetchLayer is stable (useCallback with [city] dep) — city change reruns this.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [city]);

  const toggleLayer = useCallback(
    (id: BaseLayerId) => {
      setActiveLayers((prev) => {
        const isEnabling = !prev.has(id);
        const next = new Set(prev);
        if (isEnabling) {
          next.add(id);
        } else {
          next.delete(id);
        }

        // Trigger the fetch for the newly enabled layer (city already known).
        if (isEnabling && city) {
          void fetchLayer(id);
        }

        return next;
      });
    },
    [city, fetchLayer],
  );

  return { layers, activeLayers, toggleLayer };
}
