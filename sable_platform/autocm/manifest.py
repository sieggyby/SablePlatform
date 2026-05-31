"""Per-client deployment manifest schema (MEGAPLAN C3.1 — seam 3 of 3).

The ``PRODUCTIZATION.md §3``-shaped per-client config (YAML or DB-backed in the
058 ``autocm_clients`` rows). A pydantic schema that VALIDATES a client manifest
and enforces two C3.1-owned invariants:

  (1) **Secrets-as-references (PRODUCTIZATION §5 / SAFETY §5 "no secrets in source
      or YAML/config rows").** Any credential field (``oauth_grant_ref``,
      ``bot_account``, and anything ``*_ref`` / token-shaped) MUST be a
      secret-store HANDLE or env-var NAME — ``env:RM_X_OAUTH`` or ``secret://…`` —
      NEVER a literal token. A manifest carrying an inline secret value is REJECTED
      (``ManifestSecretError``). §3's example shape embedded ``<secret>`` inline;
      this loader models the reference form instead.

  (2) **Config-schema convergence (tension #6 — OWNED here).** Relay owns
      transport/surface ENABLEMENT (``surfaces.{tg,x,discord}.enabled``,
      ``escalation_channel``) + polling, in ``relay_clients.config``. The AutoCM
      manifest owns persona / kb / categories / ops and **READS** the relay surface
      flags — it never re-declares a second ``surfaces.x.enabled``. So the manifest
      ``surfaces`` block carries per-surface DETAIL (chat_id, project_handle,
      credential refs, sub-surface lists) but the ``enabled`` flag is sourced from
      relay (see :func:`DeploymentManifest.surfaces_contradict_relay`).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ManifestSecretError(ValueError):
    """Raised when a manifest credential field carries an inline secret value
    instead of a secret-store handle / env-var name."""


# Sentinel embedded in every secret-validator message so ``load_manifest`` can
# distinguish a secrets-invariant failure (re-raised as ``ManifestSecretError``)
# from any other shape ValidationError. Field validators run inside pydantic, which
# wraps their raised errors in ``pydantic.ValidationError``; we translate back.
_SECRET_ERR_TAG = "[manifest-secret]"


# A value is an ACCEPTABLE secret reference iff it is a secret-store handle or an
# env-var NAME — never a literal token. ``env:NAME`` / ``secret://path`` are refs.
_SECRET_REF = re.compile(r"^(env:[A-Z0-9_]+|secret://\S+)$")
# Heuristic: a long opaque alnum/base64-ish run looks like an inline token/grant.
_LOOKS_LIKE_INLINE_TOKEN = re.compile(r"[A-Za-z0-9_\-]{20,}")


def _assert_secret_ref(field_name: str, value: Optional[str]) -> Optional[str]:
    """Validate a credential field is a reference, not an inline secret."""
    if value is None or value == "":
        return value
    v = value.strip()
    if _SECRET_REF.match(v):
        return v
    # Anything that is not an explicit env:/secret:// handle is rejected. The
    # §3 example's `<secret>` placeholder and any raw token both fail here. The
    # message carries _SECRET_ERR_TAG so ``load_manifest`` can re-raise the
    # pydantic-wrapped failure as a ``ManifestSecretError``.
    if v.startswith("<") and v.endswith(">"):
        raise ManifestSecretError(
            f"{_SECRET_ERR_TAG} {field_name}: placeholder {value!r} is not a secret "
            f"reference — use 'env:NAME' or 'secret://path'"
        )
    if _LOOKS_LIKE_INLINE_TOKEN.search(v):
        raise ManifestSecretError(
            f"{_SECRET_ERR_TAG} {field_name}: inline secret value detected — secrets "
            f"must be a reference ('env:NAME' or 'secret://path'), never a literal token"
        )
    raise ManifestSecretError(
        f"{_SECRET_ERR_TAG} {field_name}: {value!r} is not a recognized secret "
        f"reference; use 'env:NAME' or 'secret://path'"
    )


class ClientBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    display_name: Optional[str] = None
    founder_handle: Optional[str] = None
    escalation_channel: Optional[str] = None  # relay-owned; manifest only references


class PersonaBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ref: str


class TgSurface(BaseModel):
    model_config = ConfigDict(extra="ignore")
    chat_id: Optional[str] = None
    bot_account: Optional[str] = None  # Sable-owned bot ref — must be a reference

    @field_validator("bot_account")
    @classmethod
    def _bot_account_is_ref(cls, v: Optional[str]) -> Optional[str]:
        return _assert_secret_ref("surfaces.tg.bot_account", v)


class XSurface(BaseModel):
    model_config = ConfigDict(extra="ignore")
    project_handle: Optional[str] = None
    oauth_grant_ref: Optional[str] = None  # OAuth grant — must be a reference
    surfaces: List[str] = Field(default_factory=list)
    surfaces_excluded: List[str] = Field(default_factory=list)
    enabled_at: Optional[str] = None

    @field_validator("oauth_grant_ref")
    @classmethod
    def _oauth_is_ref(cls, v: Optional[str]) -> Optional[str]:
        return _assert_secret_ref("surfaces.x.oauth_grant_ref", v)


class DiscordSurface(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # No `enabled` here either — enablement is relay-owned (tension #6).


class SurfacesBlock(BaseModel):
    """Per-surface DETAIL only — enablement is relay-owned (tension #6).

    Note the deliberate ABSENCE of an ``enabled`` field on every surface: the
    manifest reads relay's ``surfaces.{tg,x,discord}.enabled`` rather than
    re-declaring it. An ``enabled`` key in a manifest surface is ignored
    (``extra="ignore"``) — there is exactly ONE source of truth.
    """

    model_config = ConfigDict(extra="ignore")
    tg: Optional[TgSurface] = None
    x: Optional[XSurface] = None
    discord: Optional[DiscordSurface] = None


class CategoryRule(BaseModel):
    model_config = ConfigDict(extra="ignore")
    initial_state: str = "hitl"
    threshold: float = 0.85

    @field_validator("initial_state")
    @classmethod
    def _valid_state(cls, v: str) -> str:
        allowed = {"hitl", "partial", "auto", "paused"}
        if v not in allowed:
            raise ValueError(f"category initial_state {v!r} not in {sorted(allowed)}")
        return v

    @field_validator("threshold")
    @classmethod
    def _valid_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"category threshold {v!r} must be in [0.0, 1.0]")
        return v


class LLMBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: str = "anthropic"
    model: Optional[str] = None
    api_key_ref: Optional[str] = None  # if set, must be a reference

    @field_validator("api_key_ref")
    @classmethod
    def _api_key_is_ref(cls, v: Optional[str]) -> Optional[str]:
        return _assert_secret_ref("llm.api_key_ref", v)


class DeploymentManifest(BaseModel):
    """The validated per-client deployment manifest (``PRODUCTIZATION §3`` shape).

    Owns persona / kb / categories / ops + per-surface detail. Enablement and
    polling are NOT here — they are relay-owned (read via
    :meth:`surfaces_contradict_relay`). Credential fields are reference-only
    (rejected if inline). Validate via :func:`load_manifest` (which raises
    ``ManifestSecretError`` on an inline secret) or ``DeploymentManifest(**data)``.
    """

    model_config = ConfigDict(extra="ignore")

    client: ClientBlock
    persona: PersonaBlock
    surfaces: SurfacesBlock = Field(default_factory=SurfacesBlock)
    kb_sources: List[Dict[str, Any]] = Field(default_factory=list)
    categories: Dict[str, CategoryRule] = Field(default_factory=dict)
    ops: Dict[str, Any] = Field(default_factory=dict)
    llm: LLMBlock = Field(default_factory=LLMBlock)

    def surfaces_contradict_relay(self, relay_surface_flags: Dict[str, bool]) -> List[str]:
        """Return surface names where the manifest CONTRADICTS the relay flags.

        Tension #6: relay owns ``surfaces.{tg,x,discord}.enabled``. The manifest
        must not re-declare them. This returns the surfaces the manifest carries
        DETAIL for that relay reports as DISABLED — i.e. a config that would point
        AutoCM at a transport relay hasn't enabled. An EMPTY list = non-contradictory
        (the single-source-of-truth convergence the C3.1 §7 test asserts).
        """
        contradictions: List[str] = []
        declared = {
            name: block
            for name, block in (
                ("tg", self.surfaces.tg),
                ("x", self.surfaces.x),
                ("discord", self.surfaces.discord),
            )
            if block is not None
        }
        for name in declared:
            if not relay_surface_flags.get(name, False):
                contradictions.append(name)
        return contradictions


def load_manifest(data: Dict[str, Any] | str) -> DeploymentManifest:
    """Validate a manifest from a dict or a YAML string into a :class:`DeploymentManifest`.

    Raises ``ManifestSecretError`` if any credential field carries an inline secret
    instead of a reference, and ``pydantic.ValidationError`` on a malformed shape.
    """
    if isinstance(data, str):
        parsed = yaml.safe_load(data)
        if not isinstance(parsed, dict):
            raise ValueError("manifest YAML did not parse to a mapping")
        data = parsed
    try:
        return DeploymentManifest(**data)
    except ValidationError as exc:
        # A secrets-invariant failure is raised by a field validator and wrapped by
        # pydantic; surface it as the semantic ``ManifestSecretError`` so callers can
        # distinguish "inline secret" from a generic shape error.
        if any(_SECRET_ERR_TAG in str(err.get("msg", "")) for err in exc.errors()):
            raise ManifestSecretError(str(exc)) from exc
        raise


__all__ = [
    "DeploymentManifest",
    "ManifestSecretError",
    "load_manifest",
    "CategoryRule",
    "SurfacesBlock",
    "LLMBlock",
]
