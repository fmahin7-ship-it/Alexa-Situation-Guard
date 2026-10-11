"""Where the project's files live. One place, so no module counts `..` levels."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = REPO_ROOT / "data"  # predefined situations (situation_*.json)
RECORDED_DIR = DATA_DIR / "recorded"  # recorded Amazon Location responses
DEMO_DIR = DATA_DIR / "demo"  # scripted demo scenarios
STATE_DIR = REPO_ROOT / "state"  # saved situations only (gitignored)
SECRETS_DIR = REPO_ROOT / "secrets"  # credentials: OAuth client and token (gitignored)
STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"  # the web page
