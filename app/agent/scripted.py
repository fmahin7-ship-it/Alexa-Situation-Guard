"""ScriptedProvider — a rule-based stand-in for the LLM. Free, offline, deterministic.

It follows one guided flow for a demo scenario (data/demo/*.json), but every
decision after the first step reads the real tool results:

    user reports a change -> report_change
      still works?        -> say so
      broken              -> find_options -> try_option A, B, ... until the engine says yes
                          -> recommend it and ask before changing the plan
    user confirms         -> confirm_option -> report whether it was verified

Like ScriptedProposer before it, a real LLM replaces it without changing the loop.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

from .llm import LLMReply, Message, ToolCall, ToolResult, ToolSpec
from .loop import is_approval

DEMO_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "demo"

_STATUS = re.compile(r"\b(status|plan|how am i|what('?s| is) (my|the))\b", re.I)


class DemoChange(BaseModel):
    commitment_id: str
    kind: str
    observed_at: str
    minutes: Optional[int] = None


class DemoScenario(BaseModel):
    situation_id: str
    change: DemoChange
    search_after: Optional[str] = None
    search_before: Optional[str] = None


def load_scenario(name: str = "travel_delay") -> DemoScenario:
    return DemoScenario.model_validate_json((DEMO_DIR / f"{name}.json").read_text(encoding="utf-8"))


Policy = Callable[[List[Message], List[ToolSpec]], LLMReply]


class ScriptedProvider:
    name = "scripted"
    live = False

    def __init__(self, policy: Policy):
        self._policy = policy

    def reply(self, system: str, messages: List[Message], tools: List[ToolSpec]) -> LLMReply:
        return self._policy(messages, tools)


# --- the guided policy -------------------------------------------------------


def _call(name: str, step: int, **arguments: Any) -> LLMReply:
    return LLMReply(tool_calls=[ToolCall(id=f"scripted-{step}-{name}", name=name, arguments=arguments)])


def _say(text: str) -> LLMReply:
    return LLMReply(text=text)


def _results(messages: List[Message]) -> List[ToolResult]:
    return [r for m in messages if m.role == "user" for r in m.tool_results]


def _since_last_user_text(messages: List[Message]) -> List[ToolResult]:
    """Tool results in the current turn (after the most recent user text)."""
    out: List[ToolResult] = []
    for message in reversed(messages):
        if message.role == "user" and message.text is not None:
            break
        if message.role == "user":
            out = message.tool_results + out
    return out


def _recommended_label(messages: List[Message]) -> Optional[str]:
    """Label of the last option the engine said works, if it has not been confirmed since."""
    for result in reversed(_results(messages)):
        if result.name == "confirm_option" and result.content.get("confirmed"):
            return None
        if result.name == "try_option" and result.content.get("feasible") is True:
            return result.content.get("label")
    return None


def _explain_failure(result: ToolResult) -> str:
    reasons = result.content.get("reasons") or []
    return reasons[0] if reasons else "the plan no longer works"


def guided_policy(scenario: Optional[DemoScenario] = None) -> Policy:
    scenario = scenario or load_scenario()
    sid = scenario.situation_id

    def policy(messages: List[Message], tools: List[ToolSpec]) -> LLMReply:
        last = messages[-1]
        step = len(messages)

        # A new user utterance starts the flow.
        if last.role == "user" and last.text is not None:
            label = _recommended_label(messages)
            if label and is_approval(last.text):
                return _call("confirm_option", step, situation_id=sid, label=label)
            if _STATUS.search(last.text):
                return _call("get_situation", step, situation_id=sid)
            change = scenario.change
            args: Dict[str, Any] = dict(
                situation_id=sid, commitment_id=change.commitment_id, kind=change.kind, observed_at=change.observed_at
            )
            if change.minutes is not None:
                args["minutes"] = change.minutes
            return _call("report_change", step, **args)

        # Otherwise react to the tool result that just came back.
        result = last.tool_results[-1]
        if result.is_error:
            return _say(f"Sorry, I couldn't do that: {result.content.get('error', 'unknown error')}")

        data = result.content
        if result.name == "get_situation":
            at_risk = [g["description"] for g in data.get("goals", []) if g.get("at_risk")]
            if data.get("feasible"):
                return _say("Your plan works as it stands. Nothing needs to change.")
            return _say(f"Something needs attention: {'; '.join(at_risk) or _explain_failure(result)}.")

        if result.name == "report_change":
            if data.get("feasible_after"):
                return _say("Noted. Your plan still works, so nothing needs to change.")
            args = dict(situation_id=sid, commitment_id=scenario.change.commitment_id)
            if scenario.search_after:
                args["after"] = scenario.search_after
            if scenario.search_before:
                args["before"] = scenario.search_before
            return _call("find_options", step, **args)

        if result.name == "find_options":
            labels = [o["label"] for o in data.get("options", [])]
            if not labels:
                return _say("That breaks your plan, and I couldn't find any alternatives.")
            return _call("try_option", step, situation_id=sid, label=labels[0])

        if result.name == "try_option":
            if data.get("feasible"):
                return _say(
                    f"That change breaks your plan, but option {data['label']} works: {data['summary']}. "
                    "Shall I switch to it?"
                )
            offered = next(
                (r.content for r in reversed(_since_last_user_text(messages)) if r.name == "find_options"), {}
            )
            tried = {r.content.get("label") for r in _since_last_user_text(messages) if r.name == "try_option"}
            remaining = [o["label"] for o in offered.get("options", []) if o["label"] not in tried]
            if remaining:
                return _call("try_option", step, situation_id=sid, label=remaining[0])
            return _say(f"I tried every option and none of them works. The closest problem: {_explain_failure(result)}.")

        if result.name == "confirm_option":
            if data.get("confirmed") and data.get("verified"):
                return _say("Done. I've switched your plan and checked it again: everything works.")
            if data.get("confirmed"):
                return _say("I switched your plan, but the re-check found a problem: " + _explain_failure(result))
            return _say("I couldn't switch to that option: " + _explain_failure(result))

        return _say(json.dumps(data)[:300])

    return policy
