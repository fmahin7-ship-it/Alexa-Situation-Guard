"""Option sources — where alternatives for a commitment come from.

Generic. Each source says which commitments it can help with and returns
labelled Choices (ready-made Candidates). The tools are given a list of
sources and never know which domain each belongs to: travel today;
restaurants, rooms, deliveries, ... plug in the same way.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, Sequence

from pydantic import BaseModel, Field

from .choice import Choice
from .models import Commitment, Situation


class OptionsFound(BaseModel):
    source: str
    live: bool = Field(..., description="False when answers are replayed recordings")
    choices: List[Choice] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list, description="Searches that failed or options skipped, and why")


class OptionSource(Protocol):
    name: str

    def applies_to(self, situation: Situation, commitment: Commitment) -> bool:
        ...

    def find(
        self,
        situation: Situation,
        commitment: Commitment,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> OptionsFound:
        """Alternatives to `commitment` starting at/after `after` or finishing by `before`."""
        ...


class NoOptionSource(LookupError):
    """No given source can suggest alternatives for this commitment."""


def find_options(
    situation: Situation,
    commitment_id: str,
    sources: Sequence[OptionSource],
    after: Optional[str] = None,
    before: Optional[str] = None,
) -> OptionsFound:
    """Ask the first source that applies to this commitment."""
    commitment = next((c for c in situation.commitments if c.id == commitment_id), None)
    if commitment is None:
        raise KeyError(f"Unknown commitment '{commitment_id}'")
    for source in sources:
        if source.applies_to(situation, commitment):
            return source.find(situation, commitment, after=after, before=before)
    raise NoOptionSource(
        f"No option source can suggest alternatives for '{commitment_id}' ({commitment.action})"
    )
