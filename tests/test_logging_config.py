"""Tests for structured logging configuration."""
from __future__ import annotations

import json
import logging

from sable_platform.logging_config import StructuredFormatter, configure_logging


def test_structured_formatter_json_output():
    """StructuredFormatter produces valid JSON with required fields."""
    formatter = StructuredFormatter()
    record = logging.LogRecord(
        name="sable_platform.test",
        level=logging.WARNING,
        pathname="test.py",
        lineno=1,
        msg="Test message %s",
        args=("arg1",),
        exc_info=None,
    )
    output = formatter.format(record)
    data = json.loads(output)
    assert data["level"] == "WARNING"
    assert data["logger"] == "sable_platform.test"
    assert "Test message arg1" in data["msg"]
    assert "ts" in data


def test_structured_formatter_includes_extras():
    """Extra fields (org_id, run_id) are included in JSON output."""
    formatter = StructuredFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="step done", args=(), exc_info=None,
    )
    record.org_id = "tig"
    record.run_id = "abc123"
    output = formatter.format(record)
    data = json.loads(output)
    assert data["org_id"] == "tig"
    assert data["run_id"] == "abc123"


def test_configure_logging_json_mode():
    """configure_logging(json_mode=True) installs StructuredFormatter."""
    configure_logging(json_mode=True)
    root = logging.getLogger()
    assert any(isinstance(h.formatter, StructuredFormatter) for h in root.handlers)
    # Restore default
    configure_logging(json_mode=False)


def test_configure_logging_default_mode():
    """configure_logging() uses standard format."""
    configure_logging(json_mode=False)
    root = logging.getLogger()
    assert not any(isinstance(h.formatter, StructuredFormatter) for h in root.handlers)
