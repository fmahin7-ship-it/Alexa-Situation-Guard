"""Step 1 — Load / list / summarize Situation state (no AI, no what-if yet)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .models import Situation

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def data_dir() -> Path:
    return DATA_DIR


def list_situation_files() -> List[Path]:
    return sorted(DATA_DIR.glob("situation_*.json"))


def list_situation_ids() -> List[str]:
    ids: List[str] = []
    for path in list_situation_files():
        raw = json.loads(path.read_text(encoding="utf-8"))
        ids.append(raw["id"])
    return ids


def load_situation(situation_id: str) -> Situation:
    for path in list_situation_files():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("id") == situation_id:
            return Situation.model_validate(raw)
    raise KeyError(f"Unknown situation_id: {situation_id}")


def load_all_situations() -> Dict[str, Situation]:
    out: Dict[str, Situation] = {}
    for path in list_situation_files():
        situation = Situation.model_validate(json.loads(path.read_text(encoding="utf-8")))
        out[situation.id] = situation
    return out


def describe_situation(situation: Situation) -> str:
    """Human-readable snapshot of current state (representation only)."""
    lines = [f"Situation: {situation.name} ({situation.id})"]
    if situation.timezone:
        lines.append(f"Timezone: {situation.timezone}")
    lines.append(f"People: {', '.join(p.name for p in situation.people) or '-'}")
    if situation.locations:
        lines.append("Locations:")
        for loc in situation.locations:
            lines.append(f"  - [{loc.id}] {loc.name}")
    lines.append("Goals:")
    for g in situation.goals:
        lines.append(f"  - {g.description}")
    lines.append("Commitments:")
    for c in situation.commitments:
        window = f"{c.start or '?'}-{c.end or '?'}"
        resources = ",".join(c.resource_ids) or "-"
        lines.append(f"  - [{c.id}] {c.owner_id}: {c.action} @ {window} resources=[{resources}] status={c.status}")
    lines.append("Resources:")
    for r in situation.resources:
        lines.append(f"  - [{r.id}] {r.type}: {r.name}")
    lines.append("Dependencies:")
    for d in situation.dependencies:
        lines.append(f"  - [{d.id}] {d.from_id} -{d.kind}-> {d.to_id}")
    lines.append("Constraints:")
    for c in situation.constraints:
        lines.append(f"  - [{c.id}] {c.description}")
    lines.append("Events:")
    for e in situation.events:
        lines.append(f"  - [{e.id}] {e.type}: {e.description}")
    return "\n".join(lines)
