"""The `onboard status` readiness computation (CLIENT_ONBOARDING_PLAN.md §4) — PURE.

Takes an `Evidence` snapshot the CLI assembles (manifest + accounts + entitlements +
docs + org config + which scaffold files exist + the org's reply personas) and returns
a structured `OnboardingStatus`. Entitlement-driven: an input is flagged ❌ MISSING only
when an ACTIVE service requires it. No DB, no filesystem, no network here — fully
unit-tested with fixtures; the CLI does the I/O and calls `compute_status` + `render`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sable_platform.onboarding import requirements as R


@dataclass
class Evidence:
    org_id: str
    display_name: str
    manifest: dict = field(default_factory=dict)  # client_intake row
    accounts: list[dict] = field(default_factory=list)  # client_accounts rows
    entitlements: list[dict] = field(default_factory=list)  # org_entitlements rows
    docs: list[dict] = field(default_factory=list)  # client_docs rows
    org_config: dict = field(default_factory=dict)  # orgs.config_json
    present_files: set[str] = field(default_factory=set)  # scaffold files under the org dir
    file_warnings: dict[str, str] = field(default_factory=dict)  # filename -> warn reason
    personas: list[str] = field(default_factory=list)  # operator reply personas (roster.yaml)


@dataclass
class InputStatus:
    key: str
    label: str
    state: str  # 'ok' | 'missing' | 'warn'
    detail: str
    needed_for: list[str]  # service_keys that require this input


@dataclass
class ServiceStatus:
    service_key: str
    status: str  # entitlement status: trial | active | paused | ended
    label: str


@dataclass
class ProvisioningItem:
    service_key: str
    step: str


@dataclass
class OnboardingStatus:
    org_id: str
    display_name: str
    manifest_status: str
    services: list[ServiceStatus]
    inputs: list[InputStatus]
    provisioning: list[ProvisioningItem]
    blocking: list[InputStatus]

    @property
    def is_ready(self) -> bool:
        return not self.blocking


# --- truthiness (matches client_checkin_loop._coerce_bool: bool / numeric / string-set) ---
def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)  # JSON-numeric checkin_enabled=1 reads as on (parity with the checkin loop)
    return str(v).strip().lower() in ("true", "yes", "1", "on")


# A structural account (the client's own presence) — prefer official, then community/team;
# NEVER let a `contact` (a person to reach) masquerade as the client's official handle/guild.
_ROLE_ORDER = {"official": 0, "community": 1, "team": 2}


def _pick(ev: Evidence, platforms, *, exclude_roles=("contact",)):
    cands = [
        a for a in ev.accounts
        if a.get("platform") in platforms and a.get("role") not in exclude_roles
    ]
    cands.sort(key=lambda a: _ROLE_ORDER.get(a.get("role"), 9))
    return cands[0] if cands else None


# --- per-input checkers: (Evidence) -> (state, detail) ----------------------
def _twitter(ev: Evidence):
    a = _pick(ev, ("twitter",))
    if a:
        return "ok", a["handle"]
    return "missing", "add a twitter account (role official)"


def _discord_guild(ev: Evidence):
    a = _pick(ev, ("discord",))
    if a:
        return "ok", a["handle"]
    return "missing", "add a discord account (the guild id)"


def _intake_group(ev: Evidence):
    a = _pick(ev, ("discord", "telegram"))
    if a:
        return "ok", f"{a['platform']}:{a['handle']}"
    return "missing", "add a `tracking` account (a discord guild or telegram group)"


def _controlled(ev: Evidence):
    c = [a for a in ev.accounts if a.get("controlled")]
    if c:
        return "ok", ", ".join(a["handle"] for a in c[:3])
    return "missing", "mark a team account --controlled"


def _voice_doc(ev: Evidence):
    if any(d.get("kind") == "voice" for d in ev.docs):
        return "ok", "voice doc registered"
    if any(str(f).startswith("voice/") for f in ev.present_files):
        return "ok", "voice/ present"
    return "missing", "add a voice doc per controlled account"


def _file_check(ev: Evidence, fname: str, hint: str):
    if fname in ev.file_warnings:
        return "warn", ev.file_warnings[fname]
    if fname in ev.present_files:
        return "ok", f"~/.sable/orgs/{ev.org_id}/{fname}"
    return "missing", hint


def _brief(ev: Evidence):
    return _file_check(ev, "brief.md", f"~/.sable/orgs/{ev.org_id}/brief.md")


def _guardrails(ev: Evidence):
    return _file_check(ev, "guardrails.yaml", f"~/.sable/orgs/{ev.org_id}/guardrails.yaml")


def _reply_persona(ev: Evidence):
    if ev.personas:
        return "ok", ", ".join(ev.personas[:3])
    return "missing", "assign an operator reply persona (roster.yaml org: <org>)"


def _contact_email(ev: Evidence):
    email = (ev.manifest or {}).get("primary_contact_email")
    if email:
        return "ok", email
    return "missing", f"onboard set {ev.org_id} primary_contact_email ..."


def _checkin_tg(ev: Evidence):
    cfg = ev.org_config or {}
    chat = cfg.get("client_telegram_chat_id")
    enabled = _truthy(cfg.get("checkin_enabled"))
    if chat and enabled:
        return "ok", str(chat)
    if chat and not enabled:
        return "warn", "chat set but checkin_enabled is off"
    return "missing", "set client_telegram_chat_id + checkin_enabled"


_CHECKERS = {
    R.REQ_TWITTER: _twitter,
    R.REQ_DISCORD_GUILD: _discord_guild,
    R.REQ_INTAKE_GROUP: _intake_group,
    R.REQ_CONTROLLED_ACCOUNT: _controlled,
    R.REQ_VOICE_DOC: _voice_doc,
    R.REQ_BRIEF: _brief,
    R.REQ_GUARDRAILS: _guardrails,
    R.REQ_REPLY_PERSONA: _reply_persona,
    R.REQ_CONTACT_EMAIL: _contact_email,
    R.REQ_CHECKIN_TG: _checkin_tg,
}


def compute_status(ev: Evidence) -> OnboardingStatus:
    # Tolerate a partial entitlement row (no service_key) rather than crashing — the pure
    # core never assumes its inputs are well-formed (service_key is NOT NULL in the DB, but
    # a fixture/caller could pass junk).
    valid_ents = [e for e in ev.entitlements if e.get("service_key")]
    active = [e for e in valid_ents if e.get("status") in ("trial", "active")]
    active_keys = [e["service_key"] for e in active]
    needed = R.required_inputs(active_keys)  # {req_key: [service_keys]}

    inputs: list[InputStatus] = []
    for req_key, services in needed.items():
        checker = _CHECKERS.get(req_key)
        state, detail = checker(ev) if checker else ("missing", "(no checker)")
        inputs.append(
            InputStatus(
                key=req_key,
                label=R.INPUT_LABELS.get(req_key, req_key),
                state=state,
                detail=detail,
                needed_for=sorted(set(services)),
            )
        )
    # stable, helpful order: missing first, then warn, then ok; alpha within.
    _rank = {"missing": 0, "warn": 1, "ok": 2}
    inputs.sort(key=lambda i: (_rank.get(i.state, 3), i.label))

    services_status = [
        ServiceStatus(
            service_key=e["service_key"],
            status=e.get("status", "active"),
            label=R.SERVICES[e["service_key"]].label if e["service_key"] in R.SERVICES else e["service_key"],
        )
        for e in sorted(valid_ents, key=lambda e: e["service_key"])
    ]

    provisioning: list[ProvisioningItem] = []
    for sk in active_keys:
        spec = R.SERVICES.get(sk)
        if spec:
            for step in spec.provisioning:
                provisioning.append(ProvisioningItem(sk, step))

    blocking = [i for i in inputs if i.state == "missing"]
    return OnboardingStatus(
        org_id=ev.org_id,
        display_name=ev.display_name,
        manifest_status=(ev.manifest or {}).get("manifest_status", "draft"),
        services=services_status,
        inputs=inputs,
        provisioning=provisioning,
        blocking=blocking,
    )


_GLYPH = {"ok": "✅", "missing": "❌", "warn": "⚠️"}
_SVC_GLYPH = {"active": "✅", "trial": "🧪", "paused": "⏸", "ended": "⛔"}


def render(st: OnboardingStatus) -> str:
    """The human report (CLIENT_ONBOARDING_PLAN.md §4). The CLI prints this verbatim."""
    lines: list[str] = []
    lines.append(f"{st.display_name} ({st.org_id}) — manifest: {st.manifest_status}")
    lines.append("")
    if st.services:
        svc = "   ".join(
            f"{_SVC_GLYPH.get(s.status, '•')} {s.service_key}"
            + (f"({s.status})" if s.status not in ("active",) else "")
            for s in st.services
        )
        lines.append(f"SERVICES   {svc}")
    else:
        lines.append("SERVICES   (none yet — `onboard service add <org> <service_key>`)")
    lines.append("")
    lines.append("REQUIRED INPUTS")
    if not st.inputs:
        lines.append("  (no services with required inputs yet)")
    for i in st.inputs:
        g = _GLYPH.get(i.state, "•")
        tail = f"   (needed for: {', '.join(i.needed_for)})" if i.state != "ok" else ""
        arrow = "→ " if i.state == "missing" else ""
        lines.append(f"  {g} {i.label:<24} {arrow}{i.detail}{tail}")
    if st.provisioning:
        lines.append("")
        lines.append("PROVISIONING (run `onboard apply` for the SP-side; the rest is a checklist)")
        seen: set[tuple[str, str]] = set()
        for p in st.provisioning:
            key = (p.service_key, p.step)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  ⬜ [{p.service_key}] {p.step}")
    lines.append("")
    if st.blocking:
        chase = ", ".join(i.label.lower() for i in st.blocking)
        lines.append(f"→ {len(st.blocking)} blocking item(s). Chase: {chase}.")
    else:
        lines.append("→ no blocking items — ready to `onboard apply`.")
    return "\n".join(lines)
