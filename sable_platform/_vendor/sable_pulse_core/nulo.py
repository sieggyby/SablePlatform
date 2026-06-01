"""NULO bimodal TEMPLATE render — deterministic, NO LLM. *(part of the frozen core)*

NULO is RobotMoney's CM persona, bimodal by design (SableAutoCM personas/nulo/VOICE.md):

  calm     (default)  — lowercase, no classification tags, tweet-shaped fragments.
                        Bill-Monday-tone. Used for greetings, glossary, slot-fill.
  reactive (charged)  — classification tags (`Statement:` / `Refusal:` / …),
                        capitalized, clipped. Used for hard refusals.

This module loads the two register banks and fills a chosen template deterministically.
There is NO per-call LLM here — the deterministic surface authors the wording once in
YAML and slot-fills it. Selection among equally-eligible templates reuses the intra-core
`templates._stable_index` hashing pattern, so the same (register, key, seed) always
yields the same line (deterministic, varied across seeds).

It lives in `core` (not bot-side) ON PURPOSE: MEGAPLAN D-1/R-4 make the zero-LLM
deterministic NULO reply layer a permanent property of AutoCM and the hard-fail
fallback. Shipping the renderer alongside the banks means the vendored copy can turn
the packaged YAML into a deterministic reply WITHOUT reimplementing `render`/`_pick`
or re-deriving the sha256 tie-break — so the determinism contract cannot silently
diverge between sable-pulse and the vendored deployment.

The register banks ship as PACKAGE DATA under the core package
(`sable_pulse/core/personas/<key>/nulo/{calm,reactive}.yaml`); resolve the dir via
`core.templates.persona_data_dir(key, "nulo")` so it works inside the vendored copy.
The bank YAML reuses the existing persona-YAML idiom — a `templates:` list of
`{tags, text}` dicts — where the `tags` carry the message *key* (greeting /
glossary_wrap / slotfill_wrap / refusal[/…]):

    register: calm
    templates:
      - tags: [greeting]
        text: "new arrival. welcome. ..."
      - tags: [glossary_wrap, default]
        text: "{term}: {definition}. pinned doc has the rest."

API:

    from sable_pulse.core.templates import persona_data_dir
    from sable_pulse.core.nulo import load_nulo, render
    banks = load_nulo(persona_data_dir("robotmoney", "nulo"))  # {"calm": .., "reactive": ..}
    render(banks, "calm", "greeting", slots={...}, seed="msg-123")
    render(banks, "reactive", "refusal", slots={"context": "..."}, seed="msg-123")
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .templates import _stable_index  # reuse the exact deterministic-select pattern

# The valid register names (bimodal).
CALM = "calm"
REACTIVE = "reactive"
REGISTERS = (CALM, REACTIVE)


@dataclass
class Register:
    name: str
    templates: list[dict]


def load_nulo(directory: str | Path) -> dict[str, Register]:
    """Load the calm + reactive register banks from a directory of YAML files.

    Each file's `register:` field (falling back to the filename stem) names the
    register. Mirrors `templates.load_personas` in shape.
    """
    directory = Path(directory)
    banks: dict[str, Register] = {}
    for path in sorted(directory.glob("*.yaml")):
        d = yaml.safe_load(path.read_text()) or {}
        name = d.get("register", path.stem)
        banks[name] = Register(name=name, templates=d.get("templates", []))
    return banks


def _pick(register: Register, key: str, seed: str) -> dict:
    """Pick the template whose tags best match `key`, deterministic among ties.

    Mirrors `templates.pick_template`: prefer templates tagged with `key`; fall back
    to `default`-tagged; final fallback is the whole bank. Selection among equally
    eligible templates is a stable hash of (seed + register-name + key).
    """
    exact = [t for t in register.templates if key in t.get("tags", [])]
    if exact:
        pool = exact
    else:
        defaults = [t for t in register.templates if "default" in t.get("tags", [])]
        pool = defaults or register.templates
    if not pool:
        return {"text": ""}
    return pool[_stable_index(seed + register.name + key, len(pool))]


def render(
    banks: dict[str, Register],
    register: str,
    key: str,
    slots: dict[str, str] | None = None,
    seed: str = "",
) -> str:
    """Render one NULO line deterministically.

    `register` selects calm/reactive; `key` selects the message kind (greeting,
    glossary_wrap, slotfill_wrap, refusal, …); `slots` fills `{placeholders}`;
    `seed` makes the tie-break deterministic per message. Missing slots degrade
    gracefully to the un-filled template text rather than raising (mirrors
    `templates.render_persona_line`).
    """
    slots = slots or {}
    reg = banks.get(register)
    if reg is None:
        return ""
    tmpl = _pick(reg, key, seed)
    text = tmpl.get("text", "")
    try:
        return text.format(**slots)
    except (KeyError, IndexError):
        return text
