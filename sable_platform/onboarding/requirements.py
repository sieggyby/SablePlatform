"""Service taxonomy + required-input matrix (CLIENT_ONBOARDING_PLAN.md §2).

Declarative + versioned with the schema. The entitlements a client holds DRIVE which
inputs `onboard status` flags as missing — an input is only "needed" when an active
service requires it. Each `service_key` is a SKU; `module` maps it to the canonical
Suite module (pulse|cm|engage|pairwise) or marks it a standalone product/surface.

Required-input keys here are only the ones VERIFIABLE from the manifest/scaffold/config.
Things that can't be checked from data (a BotFather token, a cross-repo redeploy) live in
`provisioning` (the checklist `apply` emits), never as a blocking input.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- Required-input keys (stable identifiers used in the status report) ------
REQ_TWITTER = "twitter_handle"
REQ_BRIEF = "brief_md"
REQ_GUARDRAILS = "guardrails_yaml"
REQ_REPLY_PERSONA = "reply_persona"
REQ_CONTROLLED_ACCOUNT = "controlled_account"
REQ_VOICE_DOC = "voice_doc"
REQ_INTAKE_GROUP = "intake_group"  # a discord guild OR a telegram intake group
REQ_DISCORD_GUILD = "discord_guild"
REQ_CONTACT_EMAIL = "primary_contact_email"
REQ_CHECKIN_TG = "checkin_telegram"  # config: client_telegram_chat_id + checkin_enabled

INPUT_LABELS: dict[str, str] = {
    REQ_TWITTER: "Twitter handle",
    REQ_BRIEF: "Reply brief",
    REQ_GUARDRAILS: "Guardrails",
    REQ_REPLY_PERSONA: "Reply persona",
    REQ_CONTROLLED_ACCOUNT: "Controlled account",
    REQ_VOICE_DOC: "Voice doc",
    REQ_INTAKE_GROUP: "Discord/Telegram intake",
    REQ_DISCORD_GUILD: "Discord server",
    REQ_CONTACT_EMAIL: "Client contact email",
    REQ_CHECKIN_TG: "Check-in Telegram chat",
}


@dataclass(frozen=True)
class ServiceSpec:
    service_key: str
    module: str  # pulse | cm | engage | pairwise | product | surface
    label: str
    required: tuple[str, ...]  # required-input keys (verifiable)
    provisioning: tuple[str, ...]  # cross-repo steps `apply` emits as a checklist


SERVICES: dict[str, ServiceSpec] = {
    "client_portal": ServiceSpec(
        "client_portal", "surface", "Client portal login",
        (REQ_CONTACT_EMAIL,),
        ("SableWeb allowlist.json {role:client, org} entry + redeploy",),
    ),
    "reply_assist": ServiceSpec(
        "reply_assist", "engage", "Tweet Assist — reply mode",
        (REQ_TWITTER, REQ_BRIEF, REQ_GUARDRAILS, REQ_REPLY_PERSONA),
        (
            "sable-platform relay enable <org>",
            "~/.sable/roster.yaml: add the reply persona(s) with org: <org>",
            "(optional) client_ops allowlist entry if the client drives it",
        ),
    ),
    "compose": ServiceSpec(
        "compose", "engage", "Tweet Assist — compose (managed accounts)",
        (REQ_CONTROLLED_ACCOUNT, REQ_VOICE_DOC),
        ("SableWeb composeAccounts.ts: add the managed account(s) + redeploy",),
    ),
    "tracking": ServiceSpec(
        "tracking", "product", "SableTracking content intake",
        (REQ_INTAKE_GROUP,),
        ("SableTracking .env GROUP_TO_CLIENT_JSON / DISCORD_GUILD_TO_CLIENT + restart",),
    ),
    "cult_grader": ServiceSpec(
        "cult_grader", "product", "Community-health diagnostics",
        (REQ_TWITTER,),
        ("Cult Grader prospect YAML (or the pre-collected internal-data path)",),
    ),
    "checkin": ServiceSpec(
        "checkin", "product", "Weekly client check-in",
        (REQ_CHECKIN_TG,),
        ("set by apply: org config set <org> checkin_enabled true + client_telegram_chat_id",),
    ),
    "kol": ServiceSpec(
        "kol", "product", "SableKOL outreach",
        (REQ_TWITTER,),
        ("SableKOL sidecar (already live) — seed a follow-graph extract run",),
    ),
    "audit": ServiceSpec(
        "audit", "product", "sable-audit community audit",
        (REQ_DISCORD_GUILD,),
        ("register the guild / self-invite the audit bot",),
    ),
    "engage_bot": ServiceSpec(
        "engage_bot", "engage", "sable-roles Discord (fitcheck/roast)",
        (REQ_DISCORD_GUILD,),
        ("sable-roles .env GUILD_TO_ORG + FITCHECK_CHANNELS + bot deploy",),
    ),
    "cm": ServiceSpec(
        "cm", "cm", "SableAutoCM (NULO) community manager",
        (),  # provisioning-only (bot token + tenant config can't be verified from the manifest)
        (
            "BotFather token -> tenant config/<org>.yaml + policy",
            "start the per-tenant bot process (OPERATIONS_RUNBOOK)",
        ),
    ),
    "pulse": ServiceSpec(
        "pulse", "pulse", "sable-pulse legibility bot",
        (),  # provisioning-only
        (
            "BotFather token -> tenant config/<org>.yaml",
            "start the per-tenant bot process (OPERATIONS_RUNBOOK)",
        ),
    ),
    "pairwise": ServiceSpec(
        "pairwise", "pairwise", "Pairwise reactor / tournament",
        (REQ_DISCORD_GUILD,),
        ("(per pairwise runbook — not yet live)",),
    ),
}


def required_inputs(active_service_keys) -> dict[str, list[str]]:
    """Union of required-input keys across the active services, mapped to the list of
    services that need each (so the report can say 'needed for: reply_assist, kol').
    Unknown service keys are ignored (forward-compatible)."""
    needed: dict[str, list[str]] = {}
    for sk in active_service_keys:
        spec = SERVICES.get(sk)
        if spec is None:
            continue
        for req in spec.required:
            needed.setdefault(req, []).append(sk)
    return needed
