"""Client-facing weekly check-in pipeline.

V1 lives here on the platform side until post-trial migration to
Sable_Client_Comms. Module split mirrors the workflow steps:

- collector: pull cult_grader metrics, this-week's actions, last metric_snapshot, latest strategy brief
- deltas: week-over-week diff for Tier 1 / Tier 2 metrics
- render: deterministic markdown tables (Jinja, no LLM)
- synthesize: single Anthropic call with prompt-cached system prompt (Task 7)
- assemble: combine rendered tables + synthesized prose into summary.md + deep_dive.md (Task 8 step)

The architectural deviation (LLM dep on the platform) is documented in CLAUDE.md
under "TIG trial build". Real check-in logic migrates to Sable_Client_Comms post-trial.
"""

from sable_platform.checkin.collector import CheckinInputs, collect_inputs
from sable_platform.checkin.deltas import MetricDelta, DeltaReport, compute_deltas
from sable_platform.checkin.render import render_data_sections
from sable_platform.checkin.synthesize import (
    DEFAULT_MODEL,
    SynthesisResult,
    synthesize,
)

__all__ = [
    "CheckinInputs",
    "collect_inputs",
    "MetricDelta",
    "DeltaReport",
    "compute_deltas",
    "render_data_sections",
    "DEFAULT_MODEL",
    "SynthesisResult",
    "synthesize",
]
