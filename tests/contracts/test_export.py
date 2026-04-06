"""Tests for sable_platform.contracts.export."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sable_platform.contracts.export import export_schemas, _MODELS


def test_export_schemas_returns_all_models():
    """export_schemas() returns a dict with one entry per _MODELS key."""
    schemas = export_schemas()
    assert set(schemas.keys()) == set(_MODELS.keys())
    for name, schema in schemas.items():
        assert isinstance(schema, dict)
        assert "properties" in schema or "$defs" in schema or "title" in schema


def test_export_schemas_writes_files():
    """When output_dir is provided, writes individual JSON files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "schemas"
        schemas = export_schemas(out)
        assert out.exists()
        for name in _MODELS:
            fpath = out / f"{name}.json"
            assert fpath.exists(), f"Missing {fpath}"
            data = json.loads(fpath.read_text())
            assert data == schemas[name]


def test_export_includes_tracking_metadata():
    """TrackingMetadata is included in the export set."""
    assert "TrackingMetadata" in _MODELS
    schemas = export_schemas()
    assert "TrackingMetadata" in schemas
