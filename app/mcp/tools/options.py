"""Finding alternatives, asking the engine about one, and making one the plan."""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from app.calendars.sync import push_moves
from app.core.candidate import AddCommitment, apply_edits, simulate
from app.core.feasibility import situation_errors
from app.core.models import Situation
from app.mcp.context import ToolContext, evaluate, offer_for
from app.mcp.views import ConfirmView, OptionsView, TryView, offer_views
from app.options.base import NoOptionSource
from app.options.base import find_options as find_options_for
from app.options.travel.search import TravelSearchError
from app.situations.store import LogEntry, Offer, TryRecord


def _unmapped_ends(situation: Situation, commitment_id: str) -> Optional[str]:
    """Why a journey got no routes: an end of it has no map coordinates. None if that is not the reason."""
    commitment = next((c for c in situation.commitments if c.id == commitment_id), None)
    if commitment is None:
        return None
    ends = [loc for loc in situation.locations if loc.id in (commitment.origin_id, commitment.destination_id)]
    unmapped = [loc.name for loc in ends if loc.latitude is None or loc.longitude is None]
    if not unmapped:
        return None
    return (
        f"No routes searched: {' and '.join(unmapped)} has no map coordinates. Ask the user for a fuller "
        "address or landmark, then add_place it again with look_up true."
    )


def _current_version(situation: Situation, commitment_id: str) -> str:
    """
    A replaced commitment (e.g. a journey already swapped for a real route) is not the one to change:
    follow what replaced it to the planned one, so moving a journey replaces it instead of adding another.
    """
    current = next((c for c in situation.commitments if c.id == commitment_id), None)
    seen = set()
    while current is not None and current.status == "cancelled" and current.id not in seen:
        seen.add(current.id)
        successors = [c for c in situation.commitments if c.meta.get("replaces") == current.id]
        if not successors:  # plans saved before journeys recorded what they replaced
            successors = [
                c for c in situation.commitments
                if c.status != "cancelled" and "option_id" in c.meta and c.owner_id == current.owner_id
                and (c.origin_id, c.destination_id) == (current.origin_id, current.destination_id)
            ]
        if len(successors) > 1:
            listed = ", ".join(f"{c.id} ({c.start})" for c in successors)
            raise ToolError(
                f"'{commitment_id}' was replaced by more than one journey: {listed}. Ask which one to change, "
                "then find_options for that id."
            )
        if not successors:
            break
        current = successors[0]
    return current.id if current is not None else commitment_id


def register(server: MCPServer, ctx: ToolContext) -> None:
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
        Replaces any options offered earlier. Does not change the plan. For a commitment that was already
        replaced (e.g. a journey swapped for a real route), the options are for what replaced it.
        """
        state = ctx.load(situation_id)
        commitment_id = _current_version(state.situation, commitment_id)
        try:
            found = find_options_for(state.situation, commitment_id, ctx.sources, after=after, before=before)
        except NoOptionSource as exc:
            raise ToolError(_unmapped_ends(state.situation, commitment_id) or str(exc).strip("'\"")) from exc
        except (KeyError, TravelSearchError, ValueError) as exc:
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
        ctx.store.save(state)
        return OptionsView(
            commitment_id=commitment_id,
            source=found.source,
            live=found.live,
            options=offer_views(state),
            notes=found.notes,
        )

    @server.tool()
    def try_option(situation_id: str, label: str) -> TryView:
        """Ask the engine whether an offered option would work. Tests it on a copy; changes nothing."""
        state = ctx.load(situation_id)
        offer = offer_for(state, label)
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
        ctx.store.save(state)
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
        state = ctx.load(situation_id)
        offer = offer_for(state, label)
        check = simulate(state.situation, offer.candidate)
        if not (check.applied and check.feasible):
            state.log.append(LogEntry(kind="confirm_refused", detail=f"{label} refused", data={"reasons": check.reasons}))
            ctx.store.save(state)
            return ConfirmView(label=label, confirmed=False, verified=False, feasible=check.feasible, reasons=check.reasons)

        updated, errors = apply_edits(state.situation, offer.candidate.edits)
        errors = errors or situation_errors(updated)
        if errors:
            return ConfirmView(label=label, confirmed=False, verified=False, reasons=errors)

        previous = state.situation
        state.situation = updated
        state.offers, state.offered_for = [], None
        state.log.append(LogEntry(kind="option_confirmed", detail=f"{label}: {offer.summary}"))
        ctx.store.save(state)

        # Verify: read back what was saved and let the engine judge it again.
        saved = ctx.store.load(situation_id).situation
        added = {e.commitment.id for e in offer.candidate.edits if isinstance(e, AddCommitment)}
        missing = sorted(added - {c.id for c in saved.commitments})
        recheck = evaluate(saved)
        reasons = recheck.reasons + [f"Saved plan is missing '{m}'" for m in missing]
        return ConfirmView(
            label=label,
            confirmed=True,
            verified=recheck.feasible and not missing,
            feasible=recheck.feasible,
            reasons=reasons,
            calendar_updates=push_moves(ctx.calendar, previous, saved),
        )
