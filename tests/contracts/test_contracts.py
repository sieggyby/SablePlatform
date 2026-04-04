"""Contract round-trip and validation tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sable_platform.contracts.entities import Entity, EntityHandle, EntityTag
from sable_platform.contracts.leads import Lead, ProspectHandoff
from sable_platform.contracts.diagnostics import DiagnosticRun
from sable_platform.contracts.content import ContentItem
from sable_platform.contracts.artifacts import Artifact
from sable_platform.contracts.sync import SyncRun
from sable_platform.contracts.workflows import WorkflowRun, WorkflowStep
from sable_platform.contracts.alerts import Alert, AlertConfig
from sable_platform.contracts.tasks import Task, RunOutcome, Recommendation


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

def test_entity_roundtrip():
    e = Entity(entity_id="abc", org_id="org1", display_name="Alice", status="candidate")
    assert Entity.model_validate(e.model_dump()) == e


def test_entity_handle_roundtrip():
    h = EntityHandle(entity_id="abc", platform="twitter", handle="alice", is_primary=True)
    assert EntityHandle.model_validate(h.model_dump()) == h


def test_entity_tag_roundtrip():
    t = EntityTag(entity_id="abc", tag="cultist_candidate", confidence=0.85)
    assert EntityTag.model_validate(t.model_dump()) == t


def test_lead_roundtrip():
    lead = Lead(
        project_id="p1",
        name="TestProj",
        twitter_handle="testproj",
        composite_score=0.72,
        recommended_action="pursue",
    )
    assert Lead.model_validate(lead.model_dump()) == lead


def test_prospect_handoff_roundtrip():
    h = ProspectHandoff(
        org_id="org1",
        prospect_yaml_path="/path/to/config.yaml",
        project_name="TestProj",
    )
    assert ProspectHandoff.model_validate(h.model_dump()) == h


def test_diagnostic_run_roundtrip():
    dr = DiagnosticRun(org_id="org1", run_type="cult_doctor", status="completed", overall_grade="A")
    assert DiagnosticRun.model_validate(dr.model_dump()) == dr


def test_content_item_roundtrip():
    ci = ContentItem(item_id="item1", org_id="org1", platform="twitter", body="hello")
    assert ContentItem.model_validate(ci.model_dump()) == ci


def test_artifact_roundtrip():
    a = Artifact(org_id="org1", artifact_type="cult_doctor_report", path="/tmp/report.md")
    assert Artifact.model_validate(a.model_dump()) == a


def test_sync_run_roundtrip():
    sr = SyncRun(org_id="org1", sync_type="sable_tracking", status="completed", entities_created=5)
    assert SyncRun.model_validate(sr.model_dump()) == sr


def test_workflow_run_roundtrip():
    wr = WorkflowRun(run_id="run1", org_id="org1", workflow_name="test", status="running")
    assert WorkflowRun.model_validate(wr.model_dump()) == wr


def test_workflow_step_roundtrip():
    ws = WorkflowStep(step_id="s1", run_id="run1", step_name="step1", step_index=0, status="completed")
    assert WorkflowStep.model_validate(ws.model_dump()) == ws


def test_alert_roundtrip():
    alert = Alert(alert_id="a1", alert_type="test", severity="warning", title="Title")
    assert Alert.model_validate(alert.model_dump()) == alert


def test_alert_config_roundtrip():
    cfg = AlertConfig(config_id="cfg1", org_id="org1", cooldown_hours=8)
    assert AlertConfig.model_validate(cfg.model_dump()) == cfg


def test_task_roundtrip():
    t = Task(org_id="org1", task_type="review", title="Check brief", priority="high")
    assert Task.model_validate(t.model_dump()) == t


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

def test_entity_requires_org_id():
    with pytest.raises(ValidationError):
        Entity(entity_id="x")  # missing org_id


def test_lead_invalid_recommended_action():
    with pytest.raises(ValidationError):
        Lead(project_id="p", name="p", recommended_action="maybe")


def test_workflow_run_timed_out_status():
    wr = WorkflowRun.model_validate({
        "run_id": "x", "org_id": "o", "workflow_name": "w", "status": "timed_out"
    })
    assert wr.status == "timed_out"


def test_workflow_run_accepts_step_fingerprint():
    wr = WorkflowRun.model_validate({
        "run_id": "x",
        "org_id": "o",
        "workflow_name": "w",
        "step_fingerprint": "v2:deadbeef",
    })
    assert wr.step_fingerprint == "v2:deadbeef"


def test_alert_contract_accepts_delivery_fields():
    alert = Alert.model_validate({
        "alert_id": "a1",
        "alert_type": "test",
        "severity": "info",
        "title": "Alert",
        "last_delivered_at": "2026-01-01 00:00:00",
        "last_delivery_error": "timeout",
    })
    assert alert.last_delivered_at == "2026-01-01 00:00:00"
    assert alert.last_delivery_error == "timeout"


def test_diagnostic_run_accepts_run_summary_json():
    run = DiagnosticRun.model_validate({
        "org_id": "org1",
        "run_type": "cult_doctor",
        "run_summary_json": '{"summary": true}',
    })
    assert run.run_summary_json == '{"summary": true}'


def test_workflow_run_invalid_status():
    with pytest.raises(ValidationError):
        WorkflowRun(run_id="r", org_id="o", workflow_name="w", status="unknown_status")


def test_entity_invalid_status():
    with pytest.raises(ValidationError):
        Entity(entity_id="x", org_id="o", status="ghost")
