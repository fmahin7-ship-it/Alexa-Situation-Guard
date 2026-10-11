"""Consent, enforced in code: a tool that changes the plan runs only right after a clear yes."""

from __future__ import annotations

import re
from typing import Optional

# Tools that change the user's plan: allowed only right after the user approves.
NEEDS_APPROVAL = {"confirm_option"}

_APPROVAL = re.compile(
    r"\b(yes|yeah|yep|sure|confirm|confirmed|do it|go ahead|switch|book it|sounds good|please do|ok(ay)?)\b", re.I
)
_REFUSAL = re.compile(r"\b(no|don'?t|do not|stop|wait|cancel|not yet)\b", re.I)


def is_approval(text: Optional[str]) -> bool:
    """A clear yes, and no 'no' / 'don't' / 'wait' in the same message."""
    return bool(text) and bool(_APPROVAL.search(text)) and not _REFUSAL.search(text)
