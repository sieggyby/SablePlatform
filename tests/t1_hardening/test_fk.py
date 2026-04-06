"""T1-WEBHOOK: dispatch stays attached to the caller DB/process lifecycle."""
from __future__ import annotations

import inspect

from sable_platform.webhooks import dispatch as dispatch_module


def test_dispatch_event_does_not_spawn_background_threads():
    """dispatch_event should complete delivery inline before the caller exits."""
    source = inspect.getsource(dispatch_module.dispatch_event)
    assert "threading.Thread" not in source
    assert "daemon=True" not in source


def test_deliver_webhook_uses_caller_connection():
    """_deliver_webhook should not hop to a separate sqlite connection."""
    source = inspect.getsource(dispatch_module._deliver_webhook)
    assert "sqlite3.connect(" not in source
