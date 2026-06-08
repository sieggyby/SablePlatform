"""Scaffold the per-client `~/.sable/orgs/<org>/` prose files (CLIENT_ONBOARDING_PLAN.md
§1.5). Templates only — never clobbers an existing file (operator edits are sacred).

The shapes here MATCH what Slopper's `sable/shared/org_context.py` LOADS:
- `guardrails.yaml`: `do_not_mention` (list of strings), `forbidden_claims` (list of
  {term, why}), optional `style_allow` (list), `tickers: {appropriate: [...]}` (NESTED —
  a flat `tickers: [...]` would load as no-tickers).
- `brief.md`: read VERBATIM into the reply-gen prompt — structure is for the operator.

Pure logic + an injected base dir: `scaffold(Path, ...)` writes under any directory, so
tests run against tmp_path. Returns the relative paths it created so the CLI can register
them as `client_docs` rows.
"""
from __future__ import annotations

import re
from pathlib import Path

BRIEF_MD = """\
# {display_name} — reply brief

> Ground-truth for reply-assist. Read VERBATIM into the generation prompt. Keep it
> factual and on-message; this is what stops the model inventing claims.

## One-liner
<what {display_name} is, in one sentence>

## How it works
<the mechanism — the thing a smart replier should be able to explain>

## Proof / traction
<real deployments, numbers, partners — only things that are TRUE and citable>

## Narrative frames (on-message angles)
- <frame 1>
- <frame 2>

## Hard questions (hold the line, don't over-claim)
- Q: <the skeptical question> — A: <the honest, non-defensive answer>

## Canonical facts
- <fact the model keeps getting wrong>

## Voice doc index
| account | doc |
|---------|-----|
| <@handle> | voice/<handle>.md |
"""

GUARDRAILS_YAML = """\
# {display_name} guardrails (loaded by Slopper sable/shared/org_context.py).
# do_not_mention: handles/project names to NOT volunteer (strings only).
# forbidden_claims: overclaims to flag post-generation (term + why).
# style_allow: on-brand buzzwords exempt from the anti-AI-slop humanizer (optional).
# tickers.appropriate: the client's own cashtags (NESTED — not a flat list).

do_not_mention: []

forbidden_claims: []
  # - term: "guaranteed returns"
  #   why: "never promise financial returns"

style_allow: []

tickers:
  appropriate: []
"""

BIOS_MD = """\
# {display_name} — team bios

> Optional. Per-person context (founders/leads). Bios can also live per-handle on the
> account registry (`onboard account add ... --bio`).

## <Name> — <role>
<2-3 line bio: background, what they own, notable prior work>
"""

VOICE_MD = """\
# {handle} — voice doc

> How Sable should write AS / reply on behalf of {handle}. Calibrated to real posts.

## Register
<serious↔shitpost, formality, sentence length, emoji policy>

## Do
- <on-voice move>

## Don't
- <off-voice move>

## Calibration pairs
- ✅ "<a real on-voice line>"
- ❌ "<an off-voice line to avoid>"
"""

# The scaffold filenames `present_files` reports on (voice/* handled separately).
TOP_LEVEL_FILES = ("brief.md", "guardrails.yaml", "bios.md")


def _safe_handle(handle: str) -> str:
    """A filesystem-safe stem for a voice doc (strip @, non-alnum -> _)."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", handle.lstrip("@")).strip("_")
    return stem or "account"


def scaffold(
    base_dir: Path,
    *,
    display_name: str,
    controlled_handles: list[str] | None = None,
) -> list[str]:
    """Create the org's prose skeletons under ``base_dir`` (e.g. ~/.sable/orgs/<org>/).
    NEVER overwrites an existing file. Creates a `voice/<handle>.md` for each controlled
    account. Returns the RELATIVE paths actually created (empty if all already existed)."""
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    templates = {
        "brief.md": BRIEF_MD.format(display_name=display_name),
        "guardrails.yaml": GUARDRAILS_YAML.format(display_name=display_name),
        "bios.md": BIOS_MD.format(display_name=display_name),
    }
    for name, body in templates.items():
        path = base_dir / name
        if not path.exists():
            path.write_text(body, encoding="utf-8")
            created.append(name)

    voice_dir = base_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    used_stems: set[str] = set()
    for handle in controlled_handles or []:
        stem = _safe_handle(handle)
        # dedupe sanitized collisions (e.g. @a.b and @a_b both -> a_b) so no voice doc is
        # silently dropped by the never-clobber guard.
        unique = stem
        n = 2
        while unique in used_stems:
            unique = f"{stem}-{n}"
            n += 1
        used_stems.add(unique)
        rel = f"voice/{unique}.md"
        path = base_dir / rel
        if not path.exists():
            path.write_text(VOICE_MD.format(handle=handle), encoding="utf-8")
            created.append(rel)

    return created


def present_files(base_dir: Path) -> set[str]:
    """Which scaffold files currently exist under ``base_dir`` (relative names incl.
    `voice/<x>.md`). Feeds the `status` Evidence so it can check brief/guardrails/voice."""
    base_dir = Path(base_dir)
    present: set[str] = set()
    if not base_dir.exists():
        return present
    for name in TOP_LEVEL_FILES:
        if (base_dir / name).is_file():
            present.add(name)
    voice_dir = base_dir / "voice"
    if voice_dir.is_dir():
        for f in voice_dir.glob("*.md"):
            present.add(f"voice/{f.name}")
    return present
