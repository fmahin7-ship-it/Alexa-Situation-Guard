"""Settings come from the environment; .env (gitignored) fills in what the environment does not set.

Read once, before anything picks its providers: the MCP server chooses weather, places,
holidays and calendar when it is built, so .env must already be loaded by then.
"""

from __future__ import annotations

from app.paths import REPO_ROOT

ENV_FILE = REPO_ROOT / ".env"


def load_settings() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=False)  # a real environment variable always wins over .env
