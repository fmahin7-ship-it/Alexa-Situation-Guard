"""Saved situation state — what the agent's tools remember between calls.

One JSON file per situation under state/ (gitignored). A situation starts as a
copy of its data/situation_*.json file; after that, reported changes and
confirmed options update the saved copy, so it survives restarts and new
conversations. `python -m app.situations.store reset <situation_id>` restores the start.

Situations either start from a predefined file, or are created empty in
conversation (create) and built up with the same edits the engine already checks.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from app.options.choice import Choice
from app.situations.fixtures import list_situation_ids, load_situation
from app.core.models import Situation
from app.paths import STATE_DIR as DEFAULT_STATE_DIR

STATE_DIR_ENV = "SITUATION_GUARD_STATE_DIR"

log = logging.getLogger(__name__)


class UnreadableSituation(ValueError):
    """A file in the state folder that is not a saved situation (corrupt, or something else entirely)."""


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
    live_clock: bool = Field(False, description="`now` follows the real clock (a plan about real life)")

    def offer(self, label: str) -> Optional[Offer]:
        return next((o for o in self.offers if o.label == label), None)


class SituationStore:
    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir or os.environ.get(STATE_DIR_ENV) or DEFAULT_STATE_DIR)

    def _path(self, situation_id: str) -> Path:
        if not situation_id or any(ch in situation_id for ch in "/\\.:"):
            raise KeyError(f"Invalid situation_id: {situation_id!r}")
        return self.state_dir / f"{situation_id}.json"

    @staticmethod
    def _read(path: Path) -> SessionState:
        try:
            return SessionState.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, UnicodeDecodeError):
            # `from None`: the validation error quotes the file's contents, which may be a credential.
            raise UnreadableSituation(f"{path.name} in the state folder is not a saved situation") from None

    def _is_saved_situation(self, path: Path) -> bool:
        try:
            self._read(path)
        except UnreadableSituation as exc:
            log.warning("Skipped: %s", exc)
            return False
        return True

    def list_ids(self) -> List[str]:
        """Predefined situations first, then ones created in conversation. Other files are skipped."""
        seeded = list_situation_ids()
        files = sorted(self.state_dir.glob("*.json")) if self.state_dir.exists() else []
        created = [p.stem for p in files if p.stem not in seeded and self._is_saved_situation(p)]
        return seeded + created

    def is_predefined(self, situation_id: str) -> bool:
        return situation_id in list_situation_ids()

    def create(self, name: str, timezone: Optional[str] = None, now: Optional[str] = None) -> SessionState:
        """A new, empty situation saved under a fresh id derived from its name."""
        base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "plan"
        base = f"plan_{base}"
        taken = set(self.list_ids())
        situation_id, n = base, 2
        while situation_id in taken:
            situation_id, n = f"{base}_{n}", n + 1
        state = SessionState(situation=Situation(id=situation_id, name=name, timezone=timezone, now=now))
        self.save(state)
        return state

    def forget(self, situation_id: str) -> None:
        """Delete a situation created in conversation. Predefined ones are reset instead."""
        if self.is_predefined(situation_id):
            raise ValueError(f"'{situation_id}' is predefined; reset it instead")
        path = self._path(situation_id)
        if not path.exists():
            raise KeyError(f"Unknown situation_id: {situation_id}")
        self._read(path)  # never delete a file that is not a saved situation
        path.unlink()

    def load(self, situation_id: str) -> SessionState:
        """
        Saved state if any, otherwise a fresh copy of the starting file. KeyError if unknown;
        UnreadableSituation if the saved file is not a situation.
        """
        path = self._path(situation_id)
        if path.exists():
            return self._read(path)
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
        sys.exit("usage: python -m app.situations.store reset <situation_id>")
    SituationStore().reset(sys.argv[2])
    print(f"Reset {sys.argv[2]} to its starting file")
