"""Tests run offline and free, whatever the developer's .env says.

Real environment variables win over .env, so pinning them here (before the app is imported)
keeps live AWS, OpenAI and the user's Google Calendar out of the test run.
"""

import os

OFFLINE = {
    "SITUATION_GUARD_LLM": "scripted",
    "SITUATION_GUARD_TRAVEL": "recorded",
    "SITUATION_GUARD_WEATHER": "recorded",
    "SITUATION_GUARD_HOLIDAYS": "recorded",
    "SITUATION_GUARD_PLACES": "known",
    "SITUATION_GUARD_CALENDAR": "off",
    "SITUATION_GUARD_HOME_ADDRESS": "",
}
os.environ.update(OFFLINE)
