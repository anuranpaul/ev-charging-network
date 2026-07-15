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
 *
 * The ApiKeyGate wraps everything so no component underneath can fire
 * a protected request before a key is in memory.
 */

import { useCallback, useState } from 'react';
import './App.css';
import { ApiKeyGate } from './components/shared/ApiKeyGate';
import { StatusReadout } from './components/shared/StatusReadout';
import { MapView } from './components/MapView';
import { SelectionPanel } from './components/SelectionPanel';
import { SidePanel } from './components/SidePanel';
import { useHealthCheck } from './hooks/useHealthCheck';
import { apiClient } from './services/apiClient';
import type { SelectionState } from './types/domain';
import type { CandidateFeature, RecommendationResponse, AnalysisResponse } from './types/geojson';

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

  // ── Query handler ──────────────────────────────────────────────────────
  const handleSelectionSubmit = useCallback(
    async (selection: Required<SelectionState>) => {
      setIsLoading(true);
      setSelectedRank(null);
      setActiveCity(selection.city);
      try {
        const [recommendationData, analysisData] = await Promise.all([
          apiClient.post<RecommendationResponse>(
            '/recommendation',
            {
              city: selection.city,
              chargerType: selection.chargerType,
              radius: selection.radius,
            },
          ),
          apiClient.get<AnalysisResponse>(
            `/analysis?city=${encodeURIComponent(selection.city)}&chargerType=${encodeURIComponent(selection.chargerType)}`
          )
        ]);
        setResponse(recommendationData);
        setAnalysis(analysisData);
      } catch {
        // Errors are surfaced via the toast system (future); silently reset
        // loading state for now so the form re-enables.
      } finally {
        setIsLoading(false);
      }
    },
    [],
  );

  // ── Candidate selection (shared between map marker click and list row) ─
  const handleCandidateSelect = useCallback((candidate: CandidateFeature) => {
    setSelectedRank(candidate.properties.rank);
  }, []);

  // ── Readout strip values ───────────────────────────────────────────────
  // Derived inside StatusReadout itself; nothing to compute here.
  // coverage_pct is not returned by POST /recommendation; pass undefined
  // until GET /analysis is wired up.

  const hasResults = response !== null && response.features.length > 0;

  return (
    <div className="cw-shell cw-root">

      {/* ── 1. Top bar ─────────────────────────────────────────────────── */}
      <header className="cw-topbar" role="banner">
        <p className="cw-topbar__name">EV network India</p>
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
        />

        {/* Floating selection card — always visible over the map */}
        <div className="cw-selection-card" aria-label="Query parameters">
          <SelectionPanel
            onSubmit={handleSelectionSubmit}
            isLoading={isLoading}
          />
        </div>

        {/* Side panel slides in from the right when results exist */}
        {hasResults && (
          <aside
            aria-label="Ranked candidates"
            style={{
              position: 'absolute',
              top: 0,
              right: 0,
              bottom: 0,
              width: 300,
              background: 'color-mix(in srgb, var(--surface-panel) 94%, transparent)',
              backdropFilter: 'blur(10px)',
              WebkitBackdropFilter: 'blur(10px)',
              borderLeft: '1px solid var(--line-grid)',
              zIndex: 20,
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <SidePanel
              response={response}
              selectedRank={selectedRank}
              onCandidateSelect={handleCandidateSelect}
            />
          </aside>
        )}
      </main>

      {/* ── 3. Bottom readout strip ────────────────────────────────────── */}
      <StatusReadout response={response} coveragePct={analysis?.coverage_pct} />

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
