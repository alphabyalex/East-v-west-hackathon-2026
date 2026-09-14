"""Bounded, single-process session portfolios over existing precomputed readers.

Opaque IDs are bearer capabilities, NOT authentication. No sensitive customer data.
Idle sessions expire after 24 hours; restart loses all entries. Use one API worker.
No training, external fetching, background polling or stored estimate snapshots.
"""

from datetime import datetime, timezone
from threading import RLock
from time import monotonic
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import Field, ValidationError

from .alerts import Thresholds, ThresholdStatus, evaluate_thresholds, supported_source
from .economics import ArithmeticRangeError, AssumptionsError
from .estimate import build_estimate
from .locations import get_locations
from .mock_provider import LocationNotFoundError
from .pipeline_provider import PipelineDataError, get_pipeline_location
from .schemas import ContractModel, EstimateRequest, EstimateResponse, Source
from .zone_ranking import WindRankingEvidence, ZoneRankingsError, load_zone_rankings


class NewSite(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=80, pattern=r"\S")]
    inputs: EstimateRequest


class SavedSite(NewSite):
    id: str
    thresholds: Thresholds = Field(default_factory=Thresholds)


class RankingComparison(ContractModel):
    status: str
    # Global zone rank, never a scenario-specific or newly normalized score.
    zone_rank: int | None = None
    composite_score: float | None = None
    reasons: list[str]
    source: Source


class SiteComparison(SavedSite):
    input_source: Source
    ranking: RankingComparison
    wind_evidence: WindRankingEvidence | None
    estimate: EstimateResponse | None
    estimate_unavailable_reason: str | None
    threshold_status: ThresholdStatus


class PortfolioResponse(ContractModel):
    id: str
    checked_at_utc: str
    storage: str = "In memory on one API worker; lost on restart or after a day of inactivity. Session ID grants access; not an authenticated account."
    check_mode: str = "Static checks of current precomputed data, refreshed on request; no live monitoring or notifications."
    economics_basis: str = "Current server economics assumptions; local workspace economics overrides are not saved."
    ranking_basis: str = "Published global zone scores, where supported. Saved load, term, flexibility, site exposure and VPP inputs do not alter the zone score."
    sites: list[SiteComparison]


class PortfolioStore:
    """Copy on read/write and a lock keep concurrent requests from losing entries."""
    def __init__(self, *, clock=monotonic, max_sessions=128, max_sites=20, ttl=86400):
        self.clock, self.max_sessions, self.max_sites, self.ttl = clock, max_sessions, max_sites, ttl
        self.sessions: dict[str, tuple[float, dict[str, SavedSite]]] = {}
        self.lock = RLock()

    def _prune(self):
        now = self.clock()
        for key, (touched, _) in list(self.sessions.items()):
            if now - touched >= self.ttl:
                del self.sessions[key]

    def create(self) -> str:
        with self.lock:
            self._prune()
            if len(self.sessions) >= self.max_sessions:
                raise HTTPException(503, "Session capacity reached; try again after an idle session expires.")
            key = str(uuid4())
            self.sessions[key] = (self.clock(), {})
            return key

    def _sites(self, key):
        self._prune()
        if key not in self.sessions:
            raise HTTPException(404, "Portfolio session not found or expired. Start a new session.")
        sites = self.sessions[key][1]
        self.sessions[key] = (self.clock(), sites)
        return sites

    def read(self, key) -> list[SavedSite]:
        with self.lock:
            return [site.model_copy(deep=True) for site in self._sites(key).values()]

    def add(self, key, item: NewSite):
        with self.lock:
            sites = self._sites(key)
            if len(sites) >= self.max_sites:
                raise HTTPException(409, "Portfolio is full; remove a saved site first.")
            site = SavedSite(id=str(uuid4()), **item.model_dump())
            sites[site.id] = site

    def change(self, key, site_id, thresholds: Thresholds | None):
        with self.lock:
            sites = self._sites(key)
            if site_id not in sites:
                raise HTTPException(404, "Saved site not found in this portfolio.")
            if thresholds is None:
                del sites[site_id]
            else:
                sites[site_id] = sites[site_id].model_copy(update={"thresholds": thresholds.model_copy(deep=True)})


store = PortfolioStore()
router = APIRouter(prefix="/api/portfolios", tags=["session portfolios"])


class PortfolioCORSMiddleware(CORSMiddleware):
    """Permit portfolio mutations without broadening other routes' preflights."""
    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if path == "/api/portfolios" or path.startswith("/api/portfolios/"):
            await super().__call__(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def compare(key: str) -> PortfolioResponse:
    sites = store.read(key)
    try:
        manifest = load_zone_rankings()
        rankings = {row.location_id: row for row in manifest.rankings}
        exclusions = {row.location_id: row for row in manifest.excluded_locations}
        wind = {row.location_id: row for row in manifest.available_wind_evidence}
        manifest_error = None
    except ZoneRankingsError as error:
        rankings, exclusions, wind = {}, {}, {}
        manifest_error = str(error)
    output = []
    for site in sites:
        location_id = site.inputs.location_id
        rank, exclusion = rankings.get(location_id), exclusions.get(location_id)
        source = Source(source_type="assumption", ref="data/processed/national_stack/zone_rankings.json; published zone composite, not a site-specific score")
        ranking = RankingComparison(
            status="available" if rank and not exclusion else "unavailable",
            zone_rank=rank.rank if rank and not exclusion else None,
            composite_score=rank.composite_score if rank and not exclusion else None,
            reasons=[] if rank and not exclusion else exclusion.reasons if exclusion else [manifest_error or "No supported ranking input for this location."],
            source=exclusion.source if exclusion else source,
        )
        estimate, reason = None, None
        try:
            candidate = build_estimate(site.inputs, get_pipeline_location)
            if supported_source(candidate.modeled_exposure.source):
                estimate = candidate
            else:
                reason = "Annual modeled exposure is placeholder or assumption-only; no evidence-based exposure, cost or threshold result is available."
        except (PipelineDataError, LocationNotFoundError, AssumptionsError, ArithmeticRangeError, ValidationError) as error:
            reason = str(error)
        output.append(SiteComparison(
            **site.model_dump(), input_source=Source(source_type="assumption", ref=f"user://portfolio/sites/{site.id}/inputs"),
            ranking=ranking, wind_evidence=wind.get(location_id), estimate=estimate,
            estimate_unavailable_reason=reason,
            threshold_status=evaluate_thresholds(site.thresholds, estimate, reason or ""),
        ))
    # Stable descending published score, excluded sites retain save order. No fake rank for ties.
    output.sort(key=lambda site: (site.ranking.composite_score is None, -(site.ranking.composite_score or 0)))
    return PortfolioResponse(id=key, checked_at_utc=datetime.now(timezone.utc).isoformat(), sites=output)


@router.post("", response_model=PortfolioResponse, status_code=201)
def create_portfolio(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return compare(store.create())


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
def get_portfolio(portfolio_id: UUID, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return compare(str(portfolio_id))


@router.post("/{portfolio_id}/sites", response_model=PortfolioResponse, status_code=201)
def add_site(portfolio_id: UUID, item: NewSite, response: Response):
    response.headers["Cache-Control"] = "no-store"
    store.read(str(portfolio_id))
    try:
        catalog = get_locations()
    except PipelineDataError as error:
        raise HTTPException(503, str(error)) from error
    if item.inputs.location_id not in {row.id for row in catalog.locations}:
        raise HTTPException(422, "Location is not in the supplied SPP catalog.")
    store.add(str(portfolio_id), item)
    return compare(str(portfolio_id))


@router.delete("/{portfolio_id}/sites/{site_id}", response_model=PortfolioResponse)
def remove_site(portfolio_id: UUID, site_id: UUID, response: Response):
    response.headers["Cache-Control"] = "no-store"
    store.change(str(portfolio_id), str(site_id), None)
    return compare(str(portfolio_id))


@router.put("/{portfolio_id}/sites/{site_id}/thresholds", response_model=PortfolioResponse)
def set_thresholds(portfolio_id: UUID, site_id: UUID, item: Thresholds, response: Response):
    response.headers["Cache-Control"] = "no-store"
    store.change(str(portfolio_id), str(site_id), item)
    return compare(str(portfolio_id))
