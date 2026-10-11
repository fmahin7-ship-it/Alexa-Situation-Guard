"""Fetch JSON over HTTPS with the standard library. Used by the free, keyless integrations."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

USER_AGENT = "situation-guard (hackathon project)"


class FetchError(Exception):
    """The service could not be reached or did not answer with JSON."""


def get_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: float = 20) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise FetchError(f"{url.split('?')[0]}: {exc}") from exc
