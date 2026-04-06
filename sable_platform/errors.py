"""Sable platform error codes and base exception."""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Error message redaction (for DB persistence paths)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED]"),
    (
        re.compile(
            r"(ANTHROPIC_API_KEY|REPLICATE_API_TOKEN|SOCIALDATA_API_KEY)\s*=\s*\S+"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE), "Bearer [REDACTED]"),
]


def redact_error(message: str) -> str:
    """Redact key-like strings from error messages before DB persistence."""
    for pattern, replacement in _SECRET_PATTERNS:
        message = pattern.sub(replacement, message)
    return message


class SableError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Error [{code}]: {message}")


# ---------------------------------------------------------------------------
# Existing error codes (verbatim from sable.platform.errors)
# ---------------------------------------------------------------------------
ORG_EXISTS = "ORG_EXISTS"
ORG_NOT_FOUND = "ORG_NOT_FOUND"
ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
ENTITY_ARCHIVED = "ENTITY_ARCHIVED"
HANDLE_NOT_IN_ROSTER = "HANDLE_NOT_IN_ROSTER"
NO_ORG_FOR_HANDLE = "NO_ORG_FOR_HANDLE"
CROSS_ORG_MERGE_BLOCKED = "CROSS_ORG_MERGE_BLOCKED"
SLUG_ORG_CONFLICT = "SLUG_ORG_CONFLICT"
STALE_DIAGNOSTIC = "STALE_DIAGNOSTIC"
NO_DISCORD_DIAGNOSTIC = "NO_DISCORD_DIAGNOSTIC"
INVALID_CONFIG = "INVALID_CONFIG"
ORG_MAPPING_ERROR = "ORG_MAPPING_ERROR"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
BRIEF_CAP_EXCEEDED = "BRIEF_CAP_EXCEEDED"
MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"
AMBIGUOUS_INPUT = "AMBIGUOUS_INPUT"
AWAITING_OPERATOR_INPUT = "AWAITING_OPERATOR_INPUT"
INVALID_ORG_ID = "INVALID_ORG_ID"
INVALID_PATH = "INVALID_PATH"

# ---------------------------------------------------------------------------
# New error codes for workflow engine
# ---------------------------------------------------------------------------
WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
STEP_EXECUTION_ERROR = "STEP_EXECUTION_ERROR"
WORKFLOW_ALREADY_RUNNING = "WORKFLOW_ALREADY_RUNNING"
