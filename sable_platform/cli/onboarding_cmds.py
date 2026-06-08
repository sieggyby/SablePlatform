"""`sable-platform onboard …` — CLI-driven client onboarding (CLIENT_ONBOARDING_PLAN.md §3-5).

Thin I/O shell over `db/onboarding.py` (the manifest/registry/entitlement CRUD) + the pure
`sable_platform.onboarding` core (requirements/status/scaffold). Every mutation is stamped to
the audit log; `SABLE_OPERATOR_ID` is required (the `onboard` group is NOT in main.py's
fail-closed exemption list — unlike top-level `init`).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from sable_platform.db import onboarding as ob
from sable_platform.db import orgs as orgs_db
from sable_platform.db.audit import log_audit
from sable_platform.db.connection import get_db
from sable_platform.onboarding import requirements as R
from sable_platform.onboarding import scaffold as sc
from sable_platform.onboarding.status import Evidence, compute_status, render

# Fields `onboard set` accepts. Intake-header fields land in client_intake; the rest are
# org config_json keys (validated via orgs.set_org_config).
_INTAKE_SET_FIELDS = (
    "primary_contact_name",
    "primary_contact_email",
    "primary_contact_telegram",
    "website_url",
    "notes",
)
_CONFIG_SET_FIELDS = (
    "sector",
    "stage",
    "max_ai_usd_per_org_per_week",
    "client_telegram_chat_id",
    "checkin_enabled",
)
_DOC_KIND_BY_FILE = {"brief.md": "brief", "guardrails.yaml": "guardrails", "bios.md": "bio"}


def _operator() -> str:
    return os.environ.get("SABLE_OPERATOR_ID", "unknown")


def _sable_home() -> Path:
    return Path(os.environ.get("SABLE_HOME") or (Path.home() / ".sable"))


def _org_dir(org_id: str) -> Path:
    return _sable_home() / "orgs" / org_id


def _fail(msg: str) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(1)


def _get_org(conn, org_id: str):
    """Return (display_name, status, config_dict) or None if the org doesn't exist."""
    row = conn.execute(
        "SELECT display_name, status, config_json FROM orgs WHERE org_id=?", (org_id,)
    ).fetchone()
    if row is None:
        return None
    cfg = json.loads(row["config_json"]) if row["config_json"] else {}
    return row["display_name"], row["status"], cfg


def _guardrails_warning(org_dir: Path) -> str | None:
    """If guardrails.yaml exists but carries no do_not_mention AND no forbidden_claims, it's
    the un-filled skeleton — surface a ⚠️ (present but needs real content). Best-effort."""
    path = org_dir / "guardrails.yaml"
    if not path.is_file():
        return None
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not (data.get("do_not_mention") or data.get("forbidden_claims")):
        return "present but empty — add do_not_mention / forbidden_claims"
    return None


def _roster_personas(org_id: str) -> list[str]:
    """Operator reply personas assigned to this org in ~/.sable/roster.yaml (best-effort)."""
    path = _sable_home() / "roster.yaml"
    if not path.is_file():
        return []
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    accounts = data.get("accounts", data) if isinstance(data, dict) else data
    out: list[str] = []
    if isinstance(accounts, list):
        for a in accounts:
            if isinstance(a, dict) and str(a.get("org", "")).lower() == org_id.lower():
                h = a.get("handle")
                if h:
                    out.append(str(h))
    return out


def _build_evidence(conn, org_id: str, org_row) -> Evidence:
    display_name, _status, cfg = org_row
    org_dir = _org_dir(org_id)
    warnings = {}
    w = _guardrails_warning(org_dir)
    if w:
        warnings["guardrails.yaml"] = w
    return Evidence(
        org_id=org_id,
        display_name=display_name or org_id,
        manifest=ob.get_intake(conn, org_id) or {},
        accounts=ob.list_accounts(conn, org_id),
        entitlements=ob.list_entitlements(conn, org_id),
        docs=ob.list_docs(conn, org_id),
        org_config=cfg,
        present_files=sc.present_files(org_dir),
        file_warnings=warnings,
        personas=_roster_personas(org_id),
    )


def _register_docs(conn, org_id: str, org_dir: Path, created: list[str]) -> None:
    for rel in created:
        kind = "voice" if rel.startswith("voice/") else _DOC_KIND_BY_FILE.get(rel, "other")
        ob.add_doc(conn, org_id, kind, rel, str(org_dir / rel))


@click.group("onboard")
def onboard() -> None:
    """Client onboarding — intake manifest, accounts, docs, entitlements, status, apply."""


# --- init -------------------------------------------------------------------
@onboard.command("init")
@click.argument("org_id")
@click.option("--name", "-n", required=True, help="Client display name")
@click.option("--from-prospect", is_flag=True, help="Org already exists (e.g. a sable-audit prospect)")
def onboard_init(org_id: str, name: str, from_prospect: bool) -> None:
    """Start a client manifest: create a DRAFT org + intake row + scaffold ~/.sable/orgs/<org>/."""
    conn = get_db()
    try:
        # Read the pre-existing row BEFORE the upsert so --from-prospect can carry over the
        # handles a sable-audit prospect already has (kills the re-entry the project fights).
        pre = conn.execute(
            "SELECT twitter_handle, discord_server_id, config_json FROM orgs WHERE org_id=?",
            (org_id,),
        ).fetchone()
        orgs_db.upsert_client_org(conn, org_id=org_id, display_name=name)  # draft (status inactive)
        ob.upsert_intake(conn, org_id)  # creates the client_intake header (manifest_status=draft)

        seeded: list[str] = []
        if from_prospect and pre is not None:
            cfg = json.loads(pre["config_json"]) if pre["config_json"] else {}
            tw = pre["twitter_handle"]
            # sable-audit stores the guild in config_json.discord_guild_id; clients on the column
            dg = pre["discord_server_id"] or cfg.get("discord_guild_id")
            if tw:
                ob.add_account(conn, org_id, "twitter", tw, "official")
                seeded.append(f"twitter:{tw}")
            if dg:
                ob.add_account(conn, org_id, "discord", str(dg), "community")
                seeded.append(f"discord:{dg}")

        org_dir = _org_dir(org_id)
        created = sc.scaffold(org_dir, display_name=name)
        _register_docs(conn, org_id, org_dir, created)
        log_audit(conn, _operator(), "onboard_init", org_id=org_id,
                  detail={"name": name, "from_prospect": from_prospect, "seeded": seeded})
        click.echo(f"✅ Initialized draft manifest for '{org_id}' ({name}).")
        if seeded:
            click.echo(f"   Carried over from prospect: {', '.join(seeded)}")
        click.echo(f"   Scaffolded {len(created)} file(s) under {org_dir}")
        click.echo(f"   Next: add accounts/services, then `sable-platform onboard status {org_id}`.")
    finally:
        conn.close()


# --- set (intake header OR org config) --------------------------------------
@onboard.command("set")
@click.argument("org_id")
@click.argument("field")
@click.argument("value")
def onboard_set(org_id: str, field: str, value: str) -> None:
    """Set a manifest field. Intake fields go to client_intake; sector/stage/cost-cap/
    checkin go to config_json (validated). For a value starting with '-' use a `--`:
    `onboard set tig client_telegram_chat_id -- -5050566880`."""
    conn = get_db()
    try:
        if _get_org(conn, org_id) is None:
            _fail(f"Org '{org_id}' not found — run `onboard init {org_id} --name ...` first.")
        if field in _INTAKE_SET_FIELDS:
            ob.upsert_intake(conn, org_id, **{field: value})
        elif field in _CONFIG_SET_FIELDS:
            try:
                orgs_db.set_org_config(conn, org_id, field, value)
            except ValueError as e:
                _fail(str(e))
        else:
            valid = ", ".join((*_INTAKE_SET_FIELDS, *_CONFIG_SET_FIELDS))
            _fail(f"Unknown field '{field}'. Valid: {valid}")
        log_audit(conn, _operator(), "onboard_set", org_id=org_id, detail={"field": field})
        click.echo(f"Set {org_id}.{field}")
    finally:
        conn.close()


# --- account ----------------------------------------------------------------
@onboard.group("account")
def onboard_account() -> None:
    """Manage the client's handle registry (twitter/discord/telegram)."""


@onboard_account.command("add")
@click.argument("org_id")
@click.option("--platform", required=True, type=click.Choice(["twitter", "discord", "telegram"]))
@click.option("--handle", required=True, help="@handle, guild id, or chat id")
@click.option("--role", required=True,
              type=click.Choice(["official", "founder", "team", "intern", "contact", "community"]))
@click.option("--controlled", is_flag=True, help="Sable posts AS this account (managed/compose)")
@click.option("--display-name", default=None)
@click.option("--bio", default=None)
def onboard_account_add(org_id, platform, handle, role, controlled, display_name, bio) -> None:
    """Add/update a client account (natural-key upsert on org+platform+handle)."""
    conn = get_db()
    try:
        if _get_org(conn, org_id) is None:
            _fail(f"Org '{org_id}' not found.")
        ob.add_account(conn, org_id, platform, handle, role,
                       controlled=controlled, display_name=display_name, bio=bio)
        log_audit(conn, _operator(), "onboard_account_add", org_id=org_id,
                  detail={"platform": platform, "handle": handle, "role": role, "controlled": controlled})
        click.echo(f"{'★ controlled ' if controlled else ''}{platform}:{handle} ({role}) added to {org_id}.")
    finally:
        conn.close()


@onboard_account.command("list")
@click.argument("org_id")
@click.option("--json", "as_json", is_flag=True, default=False)
def onboard_account_list(org_id, as_json) -> None:
    """List the client's accounts."""
    conn = get_db()
    try:
        rows = ob.list_accounts(conn, org_id)
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(rows, default=str))
        return
    if not rows:
        click.echo("No accounts.")
        return
    for a in rows:
        ctrl = " ★" if a.get("controlled") else ""
        click.echo(f"  {a['platform']:<9} {a['handle']:<24} {a['role']}{ctrl}")


@onboard_account.command("rm")
@click.argument("org_id")
@click.option("--platform", required=True)
@click.option("--handle", required=True)
def onboard_account_rm(org_id, platform, handle) -> None:
    """Remove an account from the registry."""
    conn = get_db()
    try:
        if _get_org(conn, org_id) is None:
            _fail(f"Org '{org_id}' not found.")
        ob.remove_account(conn, org_id, platform, handle)
        log_audit(conn, _operator(), "onboard_account_rm", org_id=org_id, detail={"platform": platform, "handle": handle})
        click.echo(f"Removed {platform}:{handle} from {org_id}.")
    finally:
        conn.close()


# --- doc --------------------------------------------------------------------
@onboard.group("doc")
def onboard_doc() -> None:
    """Manage explainer/bio/voice doc pointers."""


@onboard_doc.command("add")
@click.argument("org_id")
@click.option("--kind", required=True,
              type=click.Choice(["explainer", "bio", "voice", "brand", "brief", "guardrails", "other"]))
@click.option("--label", required=True)
@click.option("--location", required=True, help="URL or local path")
def onboard_doc_add(org_id, kind, label, location) -> None:
    """Register a doc pointer (whitepaper link, bio file, voice doc, …)."""
    conn = get_db()
    try:
        if _get_org(conn, org_id) is None:
            _fail(f"Org '{org_id}' not found.")
        doc_id = ob.add_doc(conn, org_id, kind, label, location)
        log_audit(conn, _operator(), "onboard_doc_add", org_id=org_id, detail={"kind": kind, "label": label})
        click.echo(f"Added doc #{doc_id} [{kind}] {label}.")
    finally:
        conn.close()


@onboard_doc.command("list")
@click.argument("org_id")
@click.option("--json", "as_json", is_flag=True, default=False)
def onboard_doc_list(org_id, as_json) -> None:
    """List the client's docs."""
    conn = get_db()
    try:
        rows = ob.list_docs(conn, org_id)
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(rows, default=str))
        return
    for d in rows or []:
        click.echo(f"  #{d['id']} [{d['kind']}] {d['label']} → {d['location']}")
    if not rows:
        click.echo("No docs.")


@onboard_doc.command("rm")
@click.argument("org_id")
@click.argument("doc_id", type=int)
def onboard_doc_rm(org_id, doc_id) -> None:
    """Remove a doc pointer by id."""
    conn = get_db()
    try:
        if _get_org(conn, org_id) is None:
            _fail(f"Org '{org_id}' not found.")
        ob.remove_doc(conn, doc_id)
        log_audit(conn, _operator(), "onboard_doc_rm", org_id=org_id, detail={"doc_id": doc_id})
        click.echo(f"Removed doc #{doc_id}.")
    finally:
        conn.close()


# --- service (entitlements) -------------------------------------------------
@onboard.group("service")
def onboard_service() -> None:
    """Manage the client's entitlements (which services they get)."""


@onboard_service.command("add")
@click.argument("org_id")
@click.argument("service_key")
@click.option("--tier", default=None)
@click.option("--status", default="active", type=click.Choice(["trial", "active", "paused", "ended"]))
def onboard_service_add(org_id, service_key, tier, status) -> None:
    """Grant (or update) an entitlement. SERVICE_KEY is a SKU (see `onboard service catalog`)."""
    conn = get_db()
    try:
        if _get_org(conn, org_id) is None:
            _fail(f"Org '{org_id}' not found.")
        if service_key not in R.SERVICES:
            known = ", ".join(sorted(R.SERVICES))
            click.echo(f"⚠️  '{service_key}' is not a known SKU (allowed anyway). Known: {known}", err=True)
        ob.set_entitlement(conn, org_id, service_key, tier=tier, status=status)
        log_audit(conn, _operator(), "onboard_service_add", org_id=org_id, detail={"service_key": service_key, "status": status})
        click.echo(f"{service_key} ({status}) granted to {org_id}.")
    finally:
        conn.close()


@onboard_service.command("rm")
@click.argument("org_id")
@click.argument("service_key")
def onboard_service_rm(org_id, service_key) -> None:
    """Revoke an entitlement."""
    conn = get_db()
    try:
        if _get_org(conn, org_id) is None:
            _fail(f"Org '{org_id}' not found.")
        ob.remove_entitlement(conn, org_id, service_key)
        log_audit(conn, _operator(), "onboard_service_rm", org_id=org_id, detail={"service_key": service_key})
        click.echo(f"Revoked {service_key} from {org_id}.")
    finally:
        conn.close()


@onboard_service.command("list")
@click.argument("org_id")
@click.option("--json", "as_json", is_flag=True, default=False)
def onboard_service_list(org_id, as_json) -> None:
    """List the client's entitlements."""
    conn = get_db()
    try:
        rows = ob.list_entitlements(conn, org_id)
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(rows, default=str))
        return
    for e in rows or []:
        tier = f" {e['tier']}" if e.get("tier") else ""
        click.echo(f"  {e['service_key']:<16} {e['status']}{tier}")
    if not rows:
        click.echo("No entitlements.")


@onboard_service.command("catalog")
def onboard_service_catalog() -> None:
    """Show the SKU catalog + what inputs each requires."""
    for key, spec in R.SERVICES.items():
        reqs = ", ".join(R.INPUT_LABELS.get(r, r) for r in spec.required) or "(provisioning only)"
        click.echo(f"  {key:<16} [{spec.module:<8}] {spec.label}")
        click.echo(f"      needs: {reqs}")


# --- status (the core UX) ---------------------------------------------------
@onboard.command("status")
@click.argument("org_id")
@click.option("--json", "as_json", is_flag=True, default=False)
def onboard_status(org_id, as_json) -> None:
    """Show the readiness report: which required inputs are present vs MISSING (chase list).
    Exits non-zero when blocking items remain."""
    conn = get_db()
    try:
        org_row = _get_org(conn, org_id)
        if org_row is None:
            _fail(f"Org '{org_id}' not found.")
        st = compute_status(_build_evidence(conn, org_id, org_row))
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps({
            "org_id": st.org_id, "manifest_status": st.manifest_status,
            "services": [vars(s) for s in st.services],
            "inputs": [vars(i) for i in st.inputs],
            "blocking": [i.key for i in st.blocking],
            "is_ready": st.is_ready,
        }, default=str))
    else:
        click.echo(render(st))
    sys.exit(0 if st.is_ready else 1)


# --- activate (the go-live flip) --------------------------------------------
@onboard.command("activate")
@click.argument("org_id")
def onboard_activate(org_id) -> None:
    """Flip the org to status='active' (go-live). Distinct from `org graduate`."""
    conn = get_db()
    try:
        org_row = _get_org(conn, org_id)
        if org_row is None:
            _fail(f"Org '{org_id}' not found.")
        orgs_db.upsert_client_org(conn, org_id=org_id, display_name=org_row[0], status="active")
        log_audit(conn, _operator(), "onboard_activate", org_id=org_id)
        click.echo(f"✅ {org_id} is now active.")
    finally:
        conn.close()


# --- apply (reconcile SP + emit checklist) ----------------------------------
@onboard.command("apply")
@click.argument("org_id")
@click.option("--dry-run", is_flag=True, help="Show what would change without writing")
@click.option("--force", is_flag=True, help="Apply even with blocking inputs missing")
def onboard_apply(org_id, dry_run, force) -> None:
    """Reconcile the manifest into SablePlatform (activate org, project handles, derive checkin,
    scaffold) and print the remaining cross-repo checklist. Refuses on blocking inputs unless --force."""
    conn = get_db()
    try:
        org_row = _get_org(conn, org_id)
        if org_row is None:
            _fail(f"Org '{org_id}' not found.")
        ev = _build_evidence(conn, org_id, org_row)
        st = compute_status(ev)
        if st.blocking and not force:
            click.echo(render(st))
            click.echo("")
            _fail("blocking inputs missing — fix them or re-run with --force.")

        # canonical handles from the registry (the SSOT projection, kills duplication)
        twitter = next((a["handle"] for a in ev.accounts if a["platform"] == "twitter" and a.get("role") == "official"),
                       next((a["handle"] for a in ev.accounts if a["platform"] == "twitter"), None))
        discord = next((a["handle"] for a in ev.accounts if a["platform"] == "discord"), None)
        active_keys = [e["service_key"] for e in ev.entitlements if e.get("status") in ("trial", "active")]
        controlled = [a["handle"] for a in ev.accounts if a.get("controlled")]

        if dry_run:
            click.echo(f"[dry-run] would activate '{org_id}' (status=active)")
            click.echo(f"[dry-run] would set twitter_handle={twitter!r}, discord_server_id={discord!r}")
            if "checkin" in active_keys and ev.org_config.get("client_telegram_chat_id"):
                click.echo("[dry-run] would set checkin_enabled=true")
            click.echo("[dry-run] would scaffold any missing ~/.sable/orgs/<org>/ files")
            _emit_checklist(active_keys, ev, twitter=twitter, discord=discord, controlled=controlled)
            return

        orgs_db.upsert_client_org(conn, org_id=org_id, display_name=org_row[0], status="active",
                                  twitter_handle=twitter, discord_server_id=discord)
        if "checkin" in active_keys and ev.org_config.get("client_telegram_chat_id"):
            orgs_db.set_org_config(conn, org_id, "checkin_enabled", "true")
        org_dir = _org_dir(org_id)
        created = sc.scaffold(org_dir, display_name=org_row[0] or org_id, controlled_handles=controlled)
        _register_docs(conn, org_id, org_dir, created)
        ob.set_manifest_status(conn, org_id, "applied")
        log_audit(conn, _operator(), "onboard_apply", org_id=org_id,
                  detail={"twitter": twitter, "discord": discord, "services": active_keys})

        click.echo(f"✅ Applied '{org_id}': active, handles projected, manifest=applied.")
        if created:
            click.echo(f"   Scaffolded {len(created)} new file(s).")
        _emit_checklist(active_keys, ev, twitter=twitter, discord=discord, controlled=controlled)
    finally:
        conn.close()


@click.group("operator")
def operator() -> None:
    """Operator onboarding (lighter sibling of `onboard`)."""


@operator.command("checklist")
@click.argument("operator_id")
@click.option("--email", required=True, help="The operator's login email")
@click.option("--role", default="operator", type=click.Choice(["admin", "operator"]))
@click.option("--orgs", default=None, help="Comma-separated orgs for a scoped operator (else all)")
@click.option("--persona", "personas", multiple=True, help="Reply persona X-handle(s) to grant")
@click.option("--compose-as", "compose_as", multiple=True, help="Managed account(s) to grant compose-as")
def operator_checklist(operator_id, email, role, orgs, personas, compose_as) -> None:
    """Emit the copy-paste runbook to fully wire a NEW operator. Emit-only — the real grants
    live in file+redeploy locations (allowlist.json, ops-identity.ts, composeAccounts.ts), so
    this prints them rather than writing other repos (CLIENT_ONBOARDING_PLAN.md §6)."""
    assigned = [o.strip() for o in orgs.split(",")] if orgs else None
    entry = {"role": role, "operatorId": operator_id}
    if assigned:
        entry["assignedOrgs"] = assigned
    click.echo(f"── New-operator runbook: {operator_id} ({email}) ──")
    click.echo("")
    click.echo("1. SableWeb allowlist.json (deploy CODE before ALLOWLIST_JSON, then redeploy):")
    # emit as one json object so the email key is properly escaped, then strip the braces
    click.echo("   " + json.dumps({email: entry})[1:-1])
    click.echo("")
    click.echo("2. Shell identity (must match the allowlist operatorId):")
    click.echo(f"   export SABLE_OPERATOR_ID={operator_id}")
    click.echo("")
    click.echo("3. Adapter paths (for workflow verification):")
    for var in ("SABLE_TRACKING_PATH", "SABLE_SLOPPER_PATH", "SABLE_CULT_GRADER_PATH", "SABLE_LEAD_IDENTIFIER_PATH"):
        click.echo(f"   export {var}=~/Projects/...")
    if personas:
        click.echo("")
        click.echo(f"4. ops-identity.ts CLIENT_OPS_PERSONA_HANDLES: grant {', '.join(personas)} (+ redeploy)")
    if compose_as:
        click.echo("")
        click.echo(f"5. composeAccounts.ts: grant compose-as {', '.join(compose_as)} (+ redeploy)")
    click.echo("")
    click.echo("(Emit-only — no repos were edited. A DB-backed allowlist is a separate effort, PLAN §9.)")


def _emit_checklist(active_keys, ev: Evidence, *, twitter, discord, controlled) -> None:
    """Print the cross-repo steps `apply` can't do (they need a redeploy), with concrete
    values substituted where easy. v1 prints — it never edits other repos."""
    click.echo("")
    click.echo("── Remaining cross-repo provisioning (copy-paste; apply does NOT do these) ──")
    email = (ev.manifest or {}).get("primary_contact_email")
    seen = set()
    for sk in active_keys:
        spec = R.SERVICES.get(sk)
        if not spec:
            continue
        for step in spec.provisioning:
            if step in seen:
                continue
            seen.add(step)
            click.echo(f"  ⬜ [{sk}] {step}")
    # concrete snippets
    if "client_portal" in active_keys and email:
        click.echo("")
        click.echo("  allowlist.json:")
        click.echo(f'    "{email}": {{ "role": "client", "org": "{ev.org_id}" }}')
    if "compose" in active_keys and controlled:
        click.echo("")
        click.echo(f"  composeAccounts.ts: add managed account(s) {', '.join(controlled)} (org={ev.org_id})")
    if "tracking" in active_keys:
        grp = next((f"{a['platform']}:{a['handle']}" for a in ev.accounts if a["platform"] in ("discord", "telegram")), "<group_id>")
        click.echo("")
        click.echo(f"  SableTracking .env: map {grp} → \"{ev.org_id}\" (GROUP_TO_CLIENT_JSON / DISCORD_GUILD_TO_CLIENT)")
