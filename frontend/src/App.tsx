/**
 * App — root layout shell for ChargeWise India.
 *
 * Layout (three CSS grid rows):
 *   1. Top bar     — app name + live health indicator
 *   2. Map stage   — full-bleed MapView with a floating SelectionPanel card
 *                    and a slide-in SidePanel when results are available
 *   3. Bottom strip — aggregate readout (candidates · coverage · avg score)
 *
 * State owned here:
 *   - recommendation response (null until first successful query)
 *   - selected candidate rank (shared between MapView and SidePanel)
 *   - query loading flag
 *   - typed query error (null on success, QueryError on any API failure)
 *
 * The ApiKeyGate wraps everything so no component underneath can fire
 * a protected request before a key is in memory.
 */

import { useCallback, useRef, useState } from 'react';
import './App.css';
import { ApiKeyGate } from './components/shared/ApiKeyGate';
import { StatusReadout } from './components/shared/StatusReadout';
import { MapView } from './components/MapView';
import { SelectionPanel } from './components/SelectionPanel';
import { SidePanel } from './components/SidePanel';
import { useHealthCheck } from './hooks/useHealthCheck';
import { apiClient } from './services/apiClient';
import type { SelectionState, QueryError } from './types/domain';
import { parseQueryError } from './types/domain';
import type { CandidateFeature, RecommendationResponse, AnalysisResponse } from './types/geojson';

// ---------------------------------------------------------------------------
// View mode type
// ---------------------------------------------------------------------------

/**
 * "explore" — base layers render normally per their toggle states.
 * "recommend" — ev_chargers is auto-hidden; candidate ScatterplotLayer shown.
 */
export type ViewMode = 'explore' | 'recommend';

// ---------------------------------------------------------------------------
// Status dot
// ---------------------------------------------------------------------------

function StatusDot({ status }: { status: ReturnType<typeof useHealthCheck> }) {
  const label =
    status === 'ok' ? 'service reachable' :
    status === 'degraded' ? 'service degraded' :
    'checking…';

  return (
    <span className="cw-topbar__status" aria-label={`API status: ${label}`}>
      <span
        className={`cw-status-dot cw-status-dot--${status}`}
        aria-hidden="true"
      />
      <span>{label}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

function ChargeWiseApp() {
  const health = useHealthCheck();

  const [response, setResponse] = useState<RecommendationResponse | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [selectedRank, setSelectedRank] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [activeCity, setActiveCity] = useState<string | null>(null);

  /**
   * "explore" — default mode, all toggled base layers visible normally.
   * "recommend" — ev_chargers is auto-suppressed; candidate layer is the focus.
   * Transitions to "recommend" when a successful response arrives.
   * Reverts to "explore" when the results panel is closed.
   */
  const [viewMode, setViewMode] = useState<ViewMode>('explore');

  /**
   * Remembers whether ev_chargers was in the active set just before we
   * entered "recommend" mode, so we can faithfully restore its toggle state
   * when returning to "explore" — not force it back on if it was off.
   */
  const evChargersWasActiveRef = useRef<boolean>(false);
  /** The city/charger being scored — held separately so the loading panel
   *  can show "Scoring candidates for Bengaluru…" while the state
   *  for the previous completed query is still visible briefly. */
  const [loadingCity, setLoadingCity] = useState<string | null>(null);
  const [loadingChargerType, setLoadingChargerType] = useState<string | null>(null);
  /** Typed error from the last failed POST /recommendation. Cleared on
   *  every new submit so a retry always starts clean. */
  const [queryError, setQueryError] = useState<QueryError | null>(null);
  /** Last selection used to submit — stored so the retry button in the
   *  503 error panel can re-issue the exact same request. */
  const [lastSelection, setLastSelection] = useState<Required<SelectionState> | null>(null);

  /**
   * AbortController for the in-flight query pair.
   * When a new query starts we abort any previous in-flight fetch so stale
   * responses from old parameters can never overwrite fresh ones.
   */
  const abortRef = useRef<AbortController | null>(null);

  // ── Query handler ──────────────────────────────────────────────────────
  const runQuery = useCallback(async (selection: Required<SelectionState>) => {
    // Abort any in-flight request from a previous parameter set.
    // This prevents a slow response from older params overwriting a faster
    // response that just arrived for the current params.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;

    setIsLoading(true);
    setQueryError(null);
    setSelectedRank(null);
    setActiveCity(selection.city);
    setLoadingCity(selection.city);
    setLoadingChargerType(selection.chargerType);
    try {
      const [recommendationData, analysisData] = await Promise.all([
        apiClient.post<RecommendationResponse>(
          '/recommendation',
          {
            city: selection.city,
            chargerType: selection.chargerType,
            radius: selection.radius,
          },
          { signal },
        ),
        apiClient.get<AnalysisResponse>(
          `/analysis?city=${encodeURIComponent(selection.city)}&chargerType=${encodeURIComponent(selection.chargerType)}`,
          { signal },
        ),
      ]);

      // Guard: if this controller was aborted while awaiting, a newer query
      // is already in flight — discard these results silently.
      if (signal.aborted) return;

      setResponse(recommendationData);
      setAnalysis(analysisData);
      // Switch to recommend mode once a successful response arrives.
      // evChargersWasActiveRef is populated by MapView's onEvChargersToggle
      // callback whenever the user changes the ev_chargers toggle state;
      // it reflects the toggle state at the moment results land.
      setViewMode('recommend');
    } catch (err) {
      // AbortError means a newer query superseded this one — not an error.
      if (err instanceof Error && err.name === 'AbortError') return;
      setQueryError(parseQueryError(err));
    } finally {
      // Only clear loading if this controller is still the active one.
      if (!signal.aborted) {
        setIsLoading(false);
      }
    }
  }, []);

  const handleSelectionSubmit = useCallback(
    (selection: Required<SelectionState>) => {
      setLastSelection(selection);
      void runQuery(selection);
    },
    [runQuery],
  );

  /** Called by the 503 retry button — re-runs the last selection unchanged. */
  const handleRetry = useCallback(() => {
    if (lastSelection) void runQuery(lastSelection);
  }, [lastSelection, runQuery]);

  // ── Candidate selection (shared between map marker click and list row) ─
  const handleCandidateSelect = useCallback((candidate: CandidateFeature) => {
    setSelectedRank(candidate.properties.rank);
  }, []);

  /**
   * Called by MapView whenever the ev_chargers toggle is flipped.
   * We record the current active state so we can restore it correctly
   * when returning to "explore" mode via the panel close button.
   */
  const handleEvChargersToggle = useCallback((isActive: boolean) => {
    evChargersWasActiveRef.current = isActive;
  }, []);

  /**
   * Called by SidePanel's ✕ close button.
   * Reverts to "explore" mode so ev_chargers restores to its prior state,
   * and clears the results/error so the side panel collapses.
   */
  const handleResultsClose = useCallback(() => {
    setViewMode('explore');
    setResponse(null);
    setAnalysis(null);
    setQueryError(null);
    setSelectedRank(null);
  }, []);

  // ── Readout strip values ───────────────────────────────────────────────
  // analysis fields (score_mean, score_median, score_p90, coverage_pct,
  // ward_stats) are passed directly to StatusReadout and SidePanel.

  const hasResults = response !== null && response.features.length > 0;
  // Show the side panel whenever we're loading, there's an error, or results exist.
  const showSidePanel = isLoading || queryError !== null || hasResults;

  // Map a 400 field error back to inline SelectionPanel validation — only
  // city / chargerType / radius are valid field names from the API.
  const serverFieldErrors: { city?: string; chargerType?: string; radius?: string } =
    queryError?.kind === '400'
      ? { [queryError.field]: queryError.message }
      : {};

  return (
    <div className="cw-shell cw-root">

      {/* ── 1. Top bar ─────────────────────────────────────────────────── */}
      <header className="cw-topbar" role="banner">
        <div className="cw-topbar__identity">
          <p className="cw-topbar__name">EV network India</p>
          <p className="cw-topbar__tagline">
            <span className="cw-topbar__tagline-primary">
              Get ranked recommendations for new EV charging station sites in your city
            </span>
            {' '}— or explore existing infrastructure layers first.
          </p>
        </div>
        <StatusDot status={health} />
      </header>

      {/* ── 2. Map stage ───────────────────────────────────────────────── */}
      <main className="cw-stage" role="main">

        {/* Full-bleed map fills the entire stage */}
        <MapView
          city={activeCity}
          candidates={response?.features ?? []}
          selectedRank={selectedRank}
          onCandidateSelect={handleCandidateSelect}
          hasResults={hasResults}
          viewMode={viewMode}
          evChargersWasActive={evChargersWasActiveRef.current}
          onEvChargersToggle={handleEvChargersToggle}
          totalCandidates={response?.total_candidates}
        />

        {/* Floating selection card — always visible over the map */}
        <div className="cw-selection-card" aria-label="Query parameters">
          <SelectionPanel
            onSubmit={handleSelectionSubmit}
            isLoading={isLoading}
            loadingCity={loadingCity}
            serverFieldErrors={serverFieldErrors}
          />
        </div>

        {/* Side panel slides in from the right when loading, errored, or
            results exist. The panel renders skeleton / error panel / list. */}
        {showSidePanel && (
          <aside
            aria-label={isLoading ? 'Loading results' : queryError ? 'Query error' : 'Ranked candidates'}
            className="cw-results-panel"
          >
            <SidePanel
              response={response}
              analysis={analysis}
              selectedRank={selectedRank}
              onCandidateSelect={handleCandidateSelect}
              isLoading={isLoading}
              loadingCity={loadingCity}
              loadingChargerType={loadingChargerType}
              queryError={queryError}
              onRetry={handleRetry}
              onClose={handleResultsClose}
            />
          </aside>
        )}
      </main>

      {/* ── 3. Bottom readout strip ────────────────────────────────────── */}
      <StatusReadout response={response} analysis={analysis} />

    </div>
  );
}

// ApiKeyGate wraps the entire app so the map and all protected calls
// are blocked until a key is held in memory.
export default function App() {
  return (
    <ApiKeyGate>
      <ChargeWiseApp />
    </ApiKeyGate>
  );
}
