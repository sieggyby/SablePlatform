"""AutoCM operator slash-command surface (MEGAPLAN C3.5c).

The mod-gated operator slash-command surface (HITL_UX §6 / §5): the TG operator
commands — ``/demote`` / ``/promote`` / ``/silence`` / ``/clear-flag`` /
``/kb-add`` / ``/kb-stale`` / ``/kb-remove`` / ``/kb-refresh-source`` /
``/category-state`` / ``/voice-drift`` / ``/punt`` / ``/pause-client`` /
``/resume-client`` / ``/incident-mode`` / ``/approve-all-tier1-<category>`` —
wired over the C2.7 command-registry path and each hitting a LIVE target
(C3.5a autonomy, C3.8a escalation/freeze, C3.2c KB, C3.8b incident-mode,
``autocm_flagged_users``). See :mod:`sable_platform.autocm.operator.commands`.
"""
from __future__ import annotations

from sable_platform.autocm.operator.commands import (
    KILL_SWITCH_REASON,
    CommandResult,
    CommandRouter,
    OperatorReplySender,
    clear_flag,
    pause_client,
    resume_client,
    silence_user,
    voice_drift_drafts,
)

__all__ = [
    "CommandRouter",
    "CommandResult",
    "OperatorReplySender",
    "KILL_SWITCH_REASON",
    "silence_user",
    "clear_flag",
    "pause_client",
    "resume_client",
    "voice_drift_drafts",
]
