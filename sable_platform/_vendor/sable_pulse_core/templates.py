"""Deterministic persona template engine — the core of `/review`.

No per-call LLM. Market data → a set of condition tags → a persona template whose
tags best match → slot-fill. Selection among equally-matching templates is a stable
hash of (address|symbol + persona) so a given token always yields the same read
(deterministic, but varied across tokens). LLM is optional garnish layered on top
in handlers, never here.

The persona/NULO YAML banks ship as PACKAGE DATA under this package
(`sable_pulse/core/personas/<key>/*.yaml` and `<key>/nulo/{calm,reactive}.yaml`),
NOT at the repo root. `persona_data_dir` / `load_personas` resolve them
PACKAGE-relative (`Path(__file__).parent`, NOT `REPO_ROOT`) so the banks travel
with the code when `core` is vendored into another repo — otherwise the vendored
loader would resolve against the host repo's root and the zero-LLM NULO banks
(the D-1 selling point) would silently break in the vendored deployment.

Persona YAML shape (see sable_pulse/core/personas/robotmoney/*.yaml):

    name: Athena
    title: the risk desk
    descriptor: "one-line who-they-are"
    explainer: "longer /who text"
    templates:
      - tags: [high_concentration]
        text: "...{symbol}... {price_change_24h_str} ..."
      - tags: [default]
        text: "..."
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from .sources.base import MarketData

# Package-relative root of the persona/NULO YAML banks (shipped as package DATA).
# Resolved against THIS module's directory — never against a host REPO_ROOT — so
# the banks resolve from inside the vendored copy of `core` at runtime.
PERSONAS_ROOT = Path(__file__).resolve().parent / "personas"


@dataclass
class Persona:
    name: str
    title: str
    descriptor: str
    explainer: str
    templates: list[dict]


def persona_data_dir(key: str, *subdirs: str) -> Path:
    """Resolve the packaged persona-bank directory for a project `key`.

    PACKAGE-relative (`PERSONAS_ROOT / key / *subdirs`), so the banks resolve from
    inside the vendored copy of `core`, NOT against any host repo root. Pass e.g.
    `persona_data_dir("robotmoney")` for the committee personas or
    `persona_data_dir("robotmoney", "nulo")` for the NULO calm/reactive banks.
    """
    return PERSONAS_ROOT.joinpath(key, *subdirs)


def load_personas(directory: str | Path) -> dict[str, Persona]:
    """Load every `*.yaml` persona in `directory` into {stem: Persona}.

    `directory` is a concrete path — callers resolve it package-relative via
    `persona_data_dir(...)`. (The standalone bot may pass a REPO_ROOT fallback
    path, but `core` itself never depends on REPO_ROOT.)
    """
    directory = Path(directory)
    personas: dict[str, Persona] = {}
    for path in sorted(directory.glob("*.yaml")):
        d = yaml.safe_load(path.read_text()) or {}
        key = path.stem
        personas[key] = Persona(
            name=d.get("name", key),
            title=d.get("title", ""),
            descriptor=d.get("descriptor", ""),
            explainer=d.get("explainer", ""),
            templates=d.get("templates", []),
        )
    return personas


# ---- humanizers -------------------------------------------------------------

def humanize_usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"${x / div:.2f}{unit}"
    return f"${x:,.2f}" if x >= 1 else f"${x:.6f}".rstrip("0").rstrip(".")


def humanize_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.1f}%"


def humanize_age(days: float | None) -> str:
    if days is None:
        return "unknown age"
    if days < 1:
        return "<1d old"
    if days < 90:
        return f"{int(days)}d old"
    return f"{int(days // 30)}mo old"


# ---- condition derivation ---------------------------------------------------
# Tags map onto the real committee's eligibility gates ($10M mcap / 90d / $100K vol)
# and its recurring themes (concentration, macro-vs-on-chain "mechanical fragility").

def derive_conditions(m: MarketData) -> list[str]:
    tags: list[str] = []
    if m.market_cap is not None and m.market_cap < 10_000_000:
        tags.append("below_midcap_gate")
    if m.pair_age_days is not None and m.pair_age_days < 90:
        tags.append("young")
    if m.volume_24h is not None and m.volume_24h < 100_000:
        tags.append("thin_volume")
    if m.liquidity_usd is not None and m.liquidity_usd < 100_000:
        tags.append("low_liquidity")
    if m.price_change_24h is not None and m.price_change_24h >= 20:
        tags.append("pumping")
    if m.price_change_24h is not None and m.price_change_24h <= -20:
        tags.append("dumping")
    if not tags:
        tags.append("clean")
    tags.append("default")
    return tags


def composite_and_regime(conditions: list[str]) -> tuple[float, str]:
    """Deterministic pseudo-composite in the committee's 0..1 idiom + a regime label."""
    score = 0.50
    weights = {
        "below_midcap_gate": -0.06,
        "young": -0.05,
        "thin_volume": -0.05,
        "low_liquidity": -0.06,
        "pumping": +0.08,
        "dumping": -0.10,
        "clean": +0.04,
    }
    for c in conditions:
        score += weights.get(c, 0.0)
    score = max(0.0, min(1.0, score))
    regime = "Risk-on" if score > 0.55 else "Risk-off" if score < 0.45 else "Neutral"
    return round(score, 3), regime


# ---- selection + fill -------------------------------------------------------

def _slots(m: MarketData, composite: float, regime: str) -> dict[str, str]:
    return {
        "symbol": m.symbol,
        "name": m.name,
        "price_str": humanize_usd(m.price_usd),
        "mcap_str": humanize_usd(m.market_cap),
        "fdv_str": humanize_usd(m.fdv),
        "vol_str": humanize_usd(m.volume_24h),
        "liq_str": humanize_usd(m.liquidity_usd),
        "price_change_24h_str": humanize_pct(m.price_change_24h),
        "age_str": humanize_age(m.pair_age_days),
        "composite_str": f"{composite:.3f}",
        "regime": regime,
        "chain": m.chain or "?",
    }


def _stable_index(seed: str, n: int) -> int:
    if n <= 1:
        return 0
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return h % n


def pick_template(persona: Persona, conditions: list[str], seed: str) -> dict:
    cond = set(conditions)
    scored = []
    for t in persona.templates:
        overlap = len(set(t.get("tags", [])) & cond)
        if overlap:
            scored.append((overlap, t))
    if not scored:
        # guaranteed fallback: any 'default'-tagged template, else first
        defaults = [t for t in persona.templates if "default" in t.get("tags", [])]
        pool = defaults or persona.templates
        return pool[_stable_index(seed + persona.name, len(pool))] if pool else {"text": ""}
    best = max(s[0] for s in scored)
    pool = [t for s, t in scored if s == best]
    return pool[_stable_index(seed + persona.name, len(pool))]


def render_persona_line(persona: Persona, m: MarketData, conditions: list[str], slots: dict[str, str]) -> str:
    seed = (m.address or m.symbol or "")
    tmpl = pick_template(persona, conditions, seed)
    try:
        body = tmpl.get("text", "").format(**slots)
    except (KeyError, IndexError):
        body = tmpl.get("text", "")
    return f"{persona.name.lower()} ({persona.title}): {body}"


def build_review_card(personas: dict[str, Persona], m: MarketData, emoji: str = "🤖💰", order: list[str] | None = None) -> str:
    conditions = derive_conditions(m)
    composite, regime = composite_and_regime(conditions)
    slots = _slots(m, composite, regime)
    header = f"{emoji} committee readout · {m.symbol}"
    market_line = f"{slots['price_str']} · mcap {slots['mcap_str']} · 24h {slots['price_change_24h_str']} · liq {slots['liq_str']} · {slots['age_str']}"
    keys = order or list(personas.keys())
    lines = [render_persona_line(personas[k], m, conditions, slots) for k in keys if k in personas]
    verdict = f"regime (derived): {regime} · composite {composite:.3f}"
    return "\n\n".join([header, market_line, "\n".join(lines), verdict])
