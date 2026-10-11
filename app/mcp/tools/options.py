"""Finding alternatives, asking the engine about one, and making one the plan."""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from app.core.candidate import AddCommitment, apply_edits, simulate
from app.core.feasibility import situation_errors
from app.mcp.context import ToolContext, evaluate, offer_for
from app.mcp.views import ConfirmView, OptionsView, TryView, offer_views
from app.options.base import NoOptionSource
from app.options.base import find_options as find_options_for
from app.options.travel.search import TravelSearchError
from app.situations.store import LogEntry, Offer, TryRecord


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
        Replaces any options offered earlier. Does not change the plan.
        """
        state = ctx.load(situation_id)
        try:
            found = find_options_for(state.situation, commitment_id, ctx.sources, after=after, before=before)
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
        )
