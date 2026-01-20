# execution/adapter.py

from datetime import datetime
from typing import Literal

ExecutionMode = Literal["PAPER", "LIVE", "LOG_ONLY"]

ALLOWED_SESSIONS = {"LONDON", "ASIA"}
BLOCKED_SESSIONS = {"NY"}

DEFAULT_MODE: ExecutionMode = "PAPER"


def resolve_execution_mode(payload: dict) -> ExecutionMode:
    """
    Determines whether this decision can execute.
    """
    return DEFAULT_MODE


def session_allowed(session: str) -> bool:
    return session not in BLOCKED_SESSIONS
