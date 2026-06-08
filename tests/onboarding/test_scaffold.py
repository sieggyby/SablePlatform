"""Pure tests for the ~/.sable/orgs/<org>/ scaffold (Chunk 2). Writes under tmp_path —
no real ~/.sable. Pins the never-clobber rule + the Slopper-loadable guardrails shape.
"""
from __future__ import annotations

import yaml

from sable_platform.onboarding.scaffold import present_files, scaffold


def test_scaffold_creates_skeletons_and_voice_docs(tmp_path):
    created = scaffold(tmp_path, display_name="Acme", controlled_handles=["@founder", "@brand"])
    assert set(created) == {
        "brief.md", "guardrails.yaml", "bios.md", "voice/founder.md", "voice/brand.md"
    }
    assert (tmp_path / "brief.md").read_text().startswith("# Acme — reply brief")
    assert (tmp_path / "voice" / "founder.md").is_file()


def test_scaffold_never_clobbers(tmp_path):
    scaffold(tmp_path, display_name="Acme")
    (tmp_path / "brief.md").write_text("OPERATOR EDITED — do not overwrite")
    created = scaffold(tmp_path, display_name="Acme")  # re-run
    assert created == []  # nothing recreated
    assert (tmp_path / "brief.md").read_text() == "OPERATOR EDITED — do not overwrite"


def test_guardrails_template_loads_in_slopper_shape(tmp_path):
    scaffold(tmp_path, display_name="Acme")
    data = yaml.safe_load((tmp_path / "guardrails.yaml").read_text())
    # the shapes org_context.py requires (nested tickers, list do_not_mention/forbidden_claims)
    assert isinstance(data["do_not_mention"], list)
    assert isinstance(data["forbidden_claims"], list)
    assert isinstance(data["style_allow"], list)
    assert isinstance(data["tickers"], dict)
    assert data["tickers"]["appropriate"] == []  # NESTED, not a flat list


def test_present_files_reports_what_exists(tmp_path):
    assert present_files(tmp_path) == set()  # nothing yet (dir may not exist)
    scaffold(tmp_path, display_name="Acme", controlled_handles=["@x"])
    present = present_files(tmp_path)
    assert "brief.md" in present and "guardrails.yaml" in present and "bios.md" in present
    assert "voice/x.md" in present


def test_safe_handle_sanitizes_filenames(tmp_path):
    created = scaffold(tmp_path, display_name="Acme", controlled_handles=["@we!rd handle"])
    voice = [c for c in created if c.startswith("voice/")]
    assert voice == ["voice/we_rd_handle.md"]
    assert (tmp_path / voice[0]).is_file()


def test_sanitized_collisions_get_distinct_files(tmp_path):
    # @a.b and @a_b both sanitize to a_b — neither voice doc must be silently dropped
    created = scaffold(tmp_path, display_name="Acme", controlled_handles=["@a.b", "@a_b"])
    voice = sorted(c for c in created if c.startswith("voice/"))
    assert voice == ["voice/a_b-2.md", "voice/a_b.md"]
    assert all((tmp_path / v).is_file() for v in voice)
