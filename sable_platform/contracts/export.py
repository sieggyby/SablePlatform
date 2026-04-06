"""Export JSON Schema from canonical Pydantic models.

Usage:
    sable-platform schema export          # writes to docs/schemas/
    sable-platform schema export --stdout  # prints to stdout
"""
from __future__ import annotations

import json
from pathlib import Path

from sable_platform.contracts.leads import Lead, DimensionScores, ProspectHandoff
from sable_platform.contracts.entities import Entity
from sable_platform.contracts.alerts import Alert
from sable_platform.contracts.diagnostics import DiagnosticRun
from sable_platform.contracts.artifacts import Artifact
from sable_platform.contracts.tracking import TrackingMetadata


_MODELS = {
    "Lead": Lead,
    "DimensionScores": DimensionScores,
    "ProspectHandoff": ProspectHandoff,
    "Entity": Entity,
    "Alert": Alert,
    "DiagnosticRun": DiagnosticRun,
    "Artifact": Artifact,
    "TrackingMetadata": TrackingMetadata,
}


def export_schemas(output_dir: Path | None = None) -> dict[str, dict]:
    """Export JSON Schema for all canonical models.

    If output_dir is provided, writes individual .json files.
    Returns dict of model_name → schema.
    """
    schemas: dict[str, dict] = {}
    for name, model_cls in _MODELS.items():
        schema = model_cls.model_json_schema()
        schemas[name] = schema
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{name}.json").write_text(
                json.dumps(schema, indent=2) + "\n", encoding="utf-8"
            )
    return schemas
