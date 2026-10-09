"""Saved situation state — what the agent's tools remember between calls.

One JSON file per situation under state/ (gitignored). A situation starts as a
copy of its data/situation_*.json file; after that, reported changes and
confirmed options update the saved copy, so it survives restarts and new
conversations. `python -m app.store reset <situation_id>` restores the start.

Situations are not created from conversation yet — only predefined ones exist.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .choice import Choice
from .engine import list_situation_ids, load_situation
from .models import Situation

STATE_DIR_ENV = "SITUATION_GUARD_STATE_DIR"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent.parent / "state"


class Offer(Choice):
    """A Choice the agent was shown, with where it came from."""

    source: str
    live: bool


class TryRecord(BaseModel):
    label: str
    candidate_id: str
    applied: bool
    feasible: Optional[bool] = None
    reasons: List[str] = Field(default_factory=list)
    at: Optional[str] = Field(None, description="Situation 'now' when tried")


class LogEntry(BaseModel):
    kind: str
    detail: str
    data: Dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    situation: Situation
    offered_for: Optional[str] = Field(None, description="Commitment the current offers would replace")
    offers: List[Offer] = Field(default_factory=list)
    tries: List[TryRecord] = Field(default_factory=list)
    log: List[LogEntry] = Field(default_factory=list)

    def offer(self, label: str) -> Optional[Offer]:
        return next((o for o in self.offers if o.label == label), None)


class SituationStore:
    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir or os.environ.get(STATE_DIR_ENV) or DEFAULT_STATE_DIR)

    def _path(self, situation_id: str) -> Path:
        if not situation_id or any(ch in situation_id for ch in "/\\.:"):
            raise KeyError(f"Invalid situation_id: {situation_id!r}")
        return self.state_dir / f"{situation_id}.json"

    def list_ids(self) -> List[str]:
        return list_situation_ids()

    def load(self, situation_id: str) -> SessionState:
        """Saved state if any, otherwise a fresh copy of the starting file. KeyError if unknown."""
        path = self._path(situation_id)
        if path.exists():
            return SessionState.model_validate_json(path.read_text(encoding="utf-8"))
        return SessionState(situation=load_situation(situation_id))

    def save(self, state: SessionState) -> None:
        """Write atomically: a crash mid-write never leaves a half-written file."""
        path = self._path(state.situation.id)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def reset(self, situation_id: str) -> SessionState:
        """Forget saved changes and start again from the situation's starting file."""
        state = SessionState(situation=load_situation(situation_id))
        self.save(state)
        return state


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "reset":
        sys.exit("usage: python -m app.store reset <situation_id>")
    SituationStore().reset(sys.argv[2])
    print(f"Reset {sys.argv[2]} to its starting file")
