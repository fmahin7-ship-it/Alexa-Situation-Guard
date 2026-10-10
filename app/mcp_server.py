"""Situation Guard MCP server — the engine as tools an agent (Alexa+) can call.

The agent proposes; these tools decide. It can only pick labelled options that
code has already turned into edits, and only the feasibility engine says
whether a plan works. Streamable HTTP, mounted by app.main at /mcp.

    list_situations  get_situation  report_change
    find_options     try_option     confirm_option

Situations start from predefined files (data/); this server persists and
updates them (state/). It does not create new situations from conversation.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Sequence

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from .candidate import apply_edits, simulate
from .feasibility import evaluate_situation, find_affected_commitment_ids, situation_errors, time_form_errors
from .impact import CANCELLED_EVENT, apply_event
from .models import Event, Situation
from .options import NoOptionSource, OptionSource
from .options import find_options as find_options_for
from .store import LogEntry, Offer, SessionState, SituationStore, TryRecord
from .travel.search import TravelSearchError
from .travel.source import TravelSource

INSTRUCTIONS = (
    "Situation Guard keeps a person's plans working when reality changes. "
    "Use report_change when something changes (a delay or a cancellation), then find_options for the "
    "broken commitment, then try_option on the labels you think best. try_option is the engine's verdict: "
    "never tell the user an option works unless try_option said feasible. Only confirm_option changes the plan; "
    "ask the user before confirming."
)


# --- what the tools return ---------------------------------------------------


class GoalView(BaseModel):
    id: str
    description: str
    at_risk: bool


class CommitmentView(BaseModel):
    id: str
    owner_id: str
    action: str
    start: Optional[str] = None
    end: Optional[str] = None
    status: str
    origin_id: Optional[str] = None
    destination_id: Optional[str] = None
    location_id: Optional[str] = None


class OfferView(BaseModel):
    label: str
    summary: str
    assumptions: List[str] = Field(default_factory=list)


class SituationBrief(BaseModel):
    id: str
    name: str
    now: Optional[str] = None
    feasible: bool


class SituationView(BaseModel):
    id: str
    name: str
    timezone: Optional[str] = None
    now: Optional[str] = None
    feasible: bool
    reasons: List[str]
    broken_commitment_ids: List[str]
    goals: List[GoalView]
    commitments: List[CommitmentView]
    offered_for: Optional[str] = None
    offers: List[OfferView] = Field(default_factory=list)


class ChangeView(BaseModel):
    applied: bool
    changed_commitment_ids: List[str]
    affected_commitment_ids: List[str]
    feasible_before: bool
    feasible_after: bool
    goals_at_risk: List[str]
    reasons: List[str]


class OptionsView(BaseModel):
    commitment_id: str
    source: str
    live: bool = Field(..., description="False when options come from replayed recordings, not a live search")
    options: List[OfferView]
    notes: List[str] = Field(default_factory=list)


class TryView(BaseModel):
    label: str
    summary: str
    applied: bool = Field(..., description="False when the option could not even be applied (malformed)")
    feasible: Optional[bool] = None
    broken_commitment_ids: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)


class ConfirmView(BaseModel):
    label: str
    confirmed: bool
    verified: bool = Field(..., description="Saved plan was reloaded and re-checked by the engine")
    feasible: Optional[bool] = None
    reasons: List[str] = Field(default_factory=list)


# --- helpers -----------------------------------------------------------------


def _load(store: SituationStore, situation_id: str) -> SessionState:
    try:
        return store.load(situation_id)
    except KeyError as exc:
        raise ToolError(f"Unknown situation '{situation_id}'. Known: {', '.join(store.list_ids())}") from exc


def _evaluate(situation: Situation):
    try:
        return evaluate_situation(situation)
    except ValueError as exc:
        raise ToolError(f"Situation cannot be evaluated: {exc}") from exc


def _offer(state: SessionState, label: str) -> Offer:
    offer = state.offer(label)
    if offer is None:
        known = ", ".join(o.label for o in state.offers) or "none — call find_options first"
        raise ToolError(f"No option '{label}'. Current options: {known}")
    return offer


def _situation_view(state: SessionState) -> SituationView:
    situation = state.situation
    result = _evaluate(situation)
    return SituationView(
        id=situation.id,
        name=situation.name,
        timezone=situation.timezone,
        now=situation.now,
        feasible=result.feasible,
        reasons=result.reasons,
        broken_commitment_ids=result.broken_commitment_ids,
        goals=[
            GoalView(id=g.id, description=g.description, at_risk=g.id in result.affected_goals)
            for g in situation.goals
        ],
        commitments=[CommitmentView(**c.model_dump(include=set(CommitmentView.model_fields))) for c in situation.commitments],
        offered_for=state.offered_for,
        offers=[OfferView(label=o.label, summary=o.summary, assumptions=o.candidate.assumptions) for o in state.offers],
    )


# --- the server --------------------------------------------------------------


def build_server(
    store: Optional[SituationStore] = None,
    sources: Optional[Sequence[OptionSource]] = None,
) -> MCPServer:
    store = store or SituationStore()
    sources = list(sources) if sources is not None else [TravelSource()]
    server = MCPServer(name="situation-guard", version="0.1.0", instructions=INSTRUCTIONS)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def list_situations() -> List[SituationBrief]:
        """List the situations being guarded, with their current time and whether they still work."""
        briefs = []
        for situation_id in store.list_ids():
            situation = store.load(situation_id).situation
            briefs.append(
                SituationBrief(id=situation.id, name=situation.name, now=situation.now, feasible=_evaluate(situation).feasible)
            )
        return briefs

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def get_situation(situation_id: str) -> SituationView:
        """Current plan: commitments, goals at risk, the engine's reasons, and any options on offer."""
        return _situation_view(_load(store, situation_id))

    @server.tool()
    def report_change(
        situation_id: str,
        commitment_id: str,
        kind: Literal["delay", "cancelled"],
        observed_at: str,
        minutes: Optional[int] = None,
        description: Optional[str] = None,
    ) -> ChangeView:
        """
        Record that reality changed: a commitment is delayed by `minutes`, or cancelled.
        `observed_at` becomes the situation's current time and must use the situation's time format
        (e.g. 2026-10-02T15:05:00+10:00). Saves the change and returns what broke.
        """
        state = _load(store, situation_id)
        if kind == "delay" and minutes is None:
            raise ToolError("A delay needs minutes")

        situation = state.situation.model_copy(deep=True)
        situation.now = observed_at
        errors = time_form_errors(situation)
        if errors:
            raise ToolError(f"observed_at is not usable here: {'; '.join(errors)}")

        event = Event(
            id=f"ev_{len(situation.events) + 1}_{kind}_{commitment_id}",
            type=CANCELLED_EVENT if kind == "cancelled" else "delay",
            description=description or f"{commitment_id} {kind}" + (f" {minutes} min" if minutes else ""),
            at=observed_at,
            related_ids=[commitment_id],
            meta={} if kind == "cancelled" else {"delay_minutes": minutes},
        )
        before = _evaluate(state.situation)  # as things stood, at the previous time
        updated, changed, applied, notes = apply_event(situation, event)
        if not applied:
            raise ToolError("; ".join(notes))
        after = _evaluate(updated)

        state.situation = updated
        state.offers, state.offered_for = [], None  # earlier options were for the old reality
        state.log.append(LogEntry(kind="change_reported", detail=event.description, data={"event_id": event.id}))
        store.save(state)

        return ChangeView(
            applied=True,
            changed_commitment_ids=changed,
            affected_commitment_ids=find_affected_commitment_ids(updated, set(changed)),
            feasible_before=before.feasible,
            feasible_after=after.feasible,
            goals_at_risk=after.affected_goals,
            reasons=after.reasons,
        )

    @server.tool()
    def find_options(
        situation_id: str,
        commitment_id: str,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> OptionsView:
        """
        Find alternatives to a commitment, labelled A, B, C, ... Optionally only ones starting at/after
        `after`, or finishing by `before` (default: from the situation's current time).
        Replaces any options offered earlier. Does not change the plan.
        """
        state = _load(store, situation_id)
        try:
            found = find_options_for(state.situation, commitment_id, sources, after=after, before=before)
        except (KeyError, NoOptionSource, TravelSearchError, ValueError) as exc:
            raise ToolError(str(exc).strip("'\"")) from exc

        state.offered_for = commitment_id
        state.offers = [Offer(**c.model_dump(), source=found.source, live=found.live) for c in found.choices]
        state.log.append(
            LogEntry(
                kind="options_found",
                detail=f"{len(state.offers)} options for {commitment_id} from {found.source}",
                data={"labels": [o.label for o in state.offers], "live": found.live},
            )
        )
        store.save(state)
        return OptionsView(
            commitment_id=commitment_id,
            source=found.source,
            live=found.live,
            options=[OfferView(label=o.label, summary=o.summary, assumptions=o.candidate.assumptions) for o in state.offers],
            notes=found.notes,
        )

    @server.tool()
    def try_option(situation_id: str, label: str) -> TryView:
        """Ask the engine whether an offered option would work. Tests it on a copy; changes nothing."""
        state = _load(store, situation_id)
        offer = _offer(state, label)
        result = simulate(state.situation, offer.candidate)

        state.tries.append(
            TryRecord(
                label=label,
                candidate_id=offer.candidate.id,
                applied=result.applied,
                feasible=result.feasible,
                reasons=result.reasons,
                at=state.situation.now,
            )
        )
        verdict = "works" if result.feasible else ("does not work" if result.applied else "could not be applied")
        state.log.append(LogEntry(kind="option_tried", detail=f"{label} {verdict}", data={"reasons": result.reasons}))
        store.save(state)
        return TryView(
            label=label,
            summary=offer.summary,
            applied=result.applied,
            feasible=result.feasible,
            broken_commitment_ids=result.broken_commitment_ids,
            reasons=result.reasons,
        )

    @server.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=False))
    def confirm_option(situation_id: str, label: str) -> ConfirmView:
        """
        Make an option the plan. The engine checks it again first and refuses if it does not work.
        After saving, the plan is reloaded and re-checked to verify the change really took effect.
        """
        state = _load(store, situation_id)
        offer = _offer(state, label)
        check = simulate(state.situation, offer.candidate)
        if not (check.applied and check.feasible):
            state.log.append(LogEntry(kind="confirm_refused", detail=f"{label} refused", data={"reasons": check.reasons}))
            store.save(state)
            return ConfirmView(label=label, confirmed=False, verified=False, feasible=check.feasible, reasons=check.reasons)

        updated, errors = apply_edits(state.situation, offer.candidate.edits)
        errors = errors or situation_errors(updated)
        if errors:
            return ConfirmView(label=label, confirmed=False, verified=False, reasons=errors)

        state.situation = updated
        state.offers, state.offered_for = [], None
        state.log.append(LogEntry(kind="option_confirmed", detail=f"{label}: {offer.summary}"))
        store.save(state)

        # Verify: read back what was saved and let the engine judge it again.
        saved = store.load(situation_id).situation
        added = {e.commitment.id for e in offer.candidate.edits if getattr(e, "op", "") == "add_commitment"}
        missing = sorted(added - {c.id for c in saved.commitments})
        recheck = _evaluate(saved)
        reasons = recheck.reasons + [f"Saved plan is missing '{m}'" for m in missing]
        return ConfirmView(
            label=label,
            confirmed=True,
            verified=recheck.feasible and not missing,
            feasible=recheck.feasible,
            reasons=reasons,
        )

    return server


mcp_server = build_server()
