"""Pure tests for the entitlement-driven `onboard status` computation (Chunk 2).

No DB, no filesystem — `compute_status` operates on an `Evidence` fixture. These pin the
core promise: an input is flagged MISSING only when an ACTIVE service requires it.
"""
from __future__ import annotations

from sable_platform.onboarding import requirements as R
from sable_platform.onboarding.status import Evidence, compute_status, render


def _ent(service_key, status="active"):
    return {"service_key": service_key, "status": status}


def test_no_entitlements_means_no_required_inputs():
    st = compute_status(Evidence(org_id="acme", display_name="Acme"))
    assert st.inputs == []
    assert st.blocking == []
    assert st.is_ready  # nothing bought -> nothing to chase


def test_reply_assist_flags_each_missing_input():
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("reply_assist")],
        accounts=[{"platform": "twitter", "handle": "@acme", "role": "official"}],
        present_files={"brief.md"},  # guardrails missing
        personas=[],  # no reply persona
    )
    st = compute_status(ev)
    by_key = {i.key: i for i in st.inputs}
    assert by_key[R.REQ_TWITTER].state == "ok" and by_key[R.REQ_TWITTER].detail == "@acme"
    assert by_key[R.REQ_BRIEF].state == "ok"
    assert by_key[R.REQ_GUARDRAILS].state == "missing"
    assert by_key[R.REQ_REPLY_PERSONA].state == "missing"
    # entitlement-driven: every reply_assist requirement is present in the report
    assert set(by_key) == set(R.SERVICES["reply_assist"].required)
    assert {i.key for i in st.blocking} == {R.REQ_GUARDRAILS, R.REQ_REPLY_PERSONA}
    assert not st.is_ready


def test_paused_entitlement_does_not_drive_requirements():
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("reply_assist", status="paused")],
    )
    st = compute_status(ev)
    assert st.inputs == []  # paused -> not active -> no required inputs
    assert st.is_ready
    # but the paused service still shows in the services list
    assert [(s.service_key, s.status) for s in st.services] == [("reply_assist", "paused")]


def test_intake_group_satisfied_by_discord_or_telegram():
    base = dict(org_id="acme", display_name="Acme", entitlements=[_ent("tracking")])
    discord = compute_status(Evidence(**base, accounts=[{"platform": "discord", "handle": "999"}]))
    assert discord.inputs[0].state == "ok" and "discord:999" in discord.inputs[0].detail
    telegram = compute_status(Evidence(**base, accounts=[{"platform": "telegram", "handle": "-100"}]))
    assert telegram.inputs[0].state == "ok"
    none = compute_status(Evidence(**base))
    assert none.inputs[0].state == "missing" and not none.is_ready


def test_checkin_warns_when_chat_set_but_disabled():
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("checkin")],
        org_config={"client_telegram_chat_id": "-500", "checkin_enabled": False},
    )
    st = compute_status(ev)
    assert st.inputs[0].state == "warn"
    assert st.is_ready  # warn does NOT block
    # enabling it flips to ok
    ev.org_config["checkin_enabled"] = "true"  # truthy-string accepted
    assert compute_status(ev).inputs[0].state == "ok"


def test_needed_for_aggregates_across_services():
    # twitter is required by BOTH reply_assist and kol -> needed_for lists both
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("kol"), _ent("reply_assist")],
        accounts=[],  # twitter missing
    )
    st = compute_status(ev)
    tw = next(i for i in st.inputs if i.key == R.REQ_TWITTER)
    assert tw.state == "missing"
    assert tw.needed_for == ["kol", "reply_assist"]  # sorted union


def test_file_warning_surfaces_as_warn_not_missing():
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("reply_assist")],
        accounts=[{"platform": "twitter", "handle": "@a", "role": "official"}],
        present_files={"brief.md", "guardrails.yaml"},
        file_warnings={"guardrails.yaml": "0 forbidden_claims — review"},
        personas=["@intern"],
    )
    st = compute_status(ev)
    g = next(i for i in st.inputs if i.key == R.REQ_GUARDRAILS)
    assert g.state == "warn" and "review" in g.detail
    assert st.is_ready  # warn is not blocking; nothing missing


def test_compose_requires_controlled_account_and_voice():
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("compose")],
        accounts=[{"platform": "twitter", "handle": "@founder", "controlled": 1}],
        docs=[{"kind": "voice", "label": "founder voice"}],
    )
    st = compute_status(ev)
    by_key = {i.key: i for i in st.inputs}
    assert by_key[R.REQ_CONTROLLED_ACCOUNT].state == "ok"
    assert by_key[R.REQ_VOICE_DOC].state == "ok"
    assert st.is_ready


def test_provisioning_listed_for_active_services_only():
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("tracking"), _ent("audit", status="ended")],
    )
    st = compute_status(ev)
    svc_keys = {p.service_key for p in st.provisioning}
    assert "tracking" in svc_keys  # active
    assert "audit" not in svc_keys  # ended -> not provisioned


def test_render_shows_glyphs_and_chase_line():
    ev = Evidence(
        org_id="solstitch", display_name="SolStitch",
        entitlements=[_ent("reply_assist"), _ent("checkin", status="paused")],
        accounts=[{"platform": "twitter", "handle": "@SolStitch", "role": "official"}],
        present_files=set(),  # brief + guardrails missing
        personas=["@sol_intern"],
    )
    out = render(compute_status(ev))
    assert "SolStitch (solstitch) — manifest: draft" in out
    assert "REQUIRED INPUTS" in out
    assert "❌" in out and "Reply brief" in out
    assert "✅" in out and "@SolStitch" in out
    assert "needed for: reply_assist" in out
    assert "blocking item" in out  # the chase line


def test_partial_entitlement_row_does_not_crash():
    # a malformed row (no service_key) is tolerated, not a KeyError (pure core robustness)
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[{"status": "active"}, _ent("kol")],  # first row has no service_key
        accounts=[{"platform": "twitter", "handle": "@a", "role": "official"}],
    )
    st = compute_status(ev)  # must not raise
    assert [s.service_key for s in st.services] == ["kol"]  # the junk row is dropped
    assert next(i for i in st.inputs if i.key == R.REQ_TWITTER).state == "ok"


def test_contact_role_does_not_satisfy_official_handle_or_intake():
    # a `contact` (a person to reach) must NOT masquerade as the client's official presence
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("kol"), _ent("tracking")],
        accounts=[
            {"platform": "twitter", "handle": "@journalist", "role": "contact"},
            {"platform": "telegram", "handle": "-personal", "role": "contact"},
        ],
    )
    st = compute_status(ev)
    by_key = {i.key: i for i in st.inputs}
    assert by_key[R.REQ_TWITTER].state == "missing"  # contact twitter doesn't count
    assert by_key[R.REQ_INTAKE_GROUP].state == "missing"  # contact telegram isn't the intake group


def test_official_preferred_over_other_roles():
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("kol")],
        accounts=[
            {"platform": "twitter", "handle": "@team_member", "role": "team"},
            {"platform": "twitter", "handle": "@AcmeHQ", "role": "official"},
        ],
    )
    st = compute_status(ev)
    assert st.inputs[0].detail == "@AcmeHQ"  # official wins over team


def test_checkin_enabled_numeric_one_is_truthy():
    # JSON-numeric checkin_enabled=1 reads as ON (parity with client_checkin_loop)
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("checkin")],
        org_config={"client_telegram_chat_id": "-7", "checkin_enabled": 1},
    )
    assert compute_status(ev).inputs[0].state == "ok"


def test_voice_doc_satisfied_by_a_voice_file():
    # the present_files branch (a voice/ file on disk), distinct from a registered doc
    ev = Evidence(
        org_id="acme", display_name="Acme",
        entitlements=[_ent("compose")],
        accounts=[{"platform": "twitter", "handle": "@f", "controlled": 1}],
        present_files={"voice/f.md"},
    )
    by_key = {i.key: i for i in compute_status(ev).inputs}
    assert by_key[R.REQ_VOICE_DOC].state == "ok"


def test_unknown_service_key_is_ignored():
    # forward-compatible: an entitlement for an unknown SKU drives no requirements + no crash
    st = compute_status(
        Evidence(org_id="acme", display_name="Acme", entitlements=[_ent("future_sku")])
    )
    assert st.inputs == []
    assert [s.service_key for s in st.services] == ["future_sku"]  # still shown
