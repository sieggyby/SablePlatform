"""Shared drafter prompt-assembly (C3.3) — the variable user-turn builder.

Both register composers (``compose_calm`` / ``compose_reactive``) build the SAME
variable user prompt: the delimiter-wrapped thread context + KB facts + the
(delimiter-wrapped) message. This is the volatile suffix that sits AFTER the
prompt-cached persona system block (the stable cache prefix). Factored here so the
two composers cannot drift in how they assemble — or wrap — untrusted input.

PROMPT-INJECTION HARDENING (SAFETY §2 + CLASSIFIER §3): the two untrusted strings
that enter the drafter LLM call — ``{message}`` and ``{thread_context}`` (the
last-5 turns from OTHER members) — are delimiter-wrapped via the C3.4a-owned
:func:`~sable_platform.autocm.classifier.filter.wrap_user_input`, the same
break-out-safe chokepoint the classifier uses, so a hostile closing tag cannot
break out of its block. KB chunk text is TRUSTED (it is the client's own seeded
knowledge base, authority-weighted), so it is interpolated without wrapping — but
each chunk carries its ``[chunk_id]`` marker so the C3.5a citation gate can verify
the draft cited a real chunk.
"""
from __future__ import annotations

from typing import List

from sable_platform.autocm.classifier.filter import wrap_user_input
from sable_platform.autocm.drafter.persona import DraftRequest


def cited_chunk_ids(request: DraftRequest) -> List[int]:
    """The chunk_ids the drafter was GIVEN (the citation-gate candidate set).

    The C3.5a citation gate verifies the draft's ``[chunk_id]`` markers against the
    chunks actually retrieved for the message; this is that candidate set.
    """
    return [c.chunk_id for c in request.kb_chunks]


def _kb_facts_block(request: DraftRequest) -> str:
    """Render the retrieved KB chunks as cite-able facts (TRUSTED — not wrapped).

    Each chunk is tagged ``[<chunk_id>]`` so the drafter can cite it and the C3.5a
    gate can verify the citation. Empty when no chunks were retrieved (the drafter
    then composes from the persona + on-chain habit alone — calm informational
    replies without a KB hit fall to the citation-loose / refusal path at the gate).
    """
    if not request.kb_chunks:
        return ""
    lines = ["<kb_facts>"]
    for c in request.kb_chunks:
        lines.append(f"[{c.chunk_id}] {c.text}")
    lines.append("</kb_facts>")
    return "\n".join(lines)


def build_user_prompt(request: DraftRequest) -> str:
    """Assemble the variable user turn for the drafter LLM call.

    Order: KB facts (trusted, cite-able) → wrapped thread context → wrapped message.
    The two untrusted blocks (``thread_context`` / ``message``) are delimiter-wrapped
    with wrapper tags neutralized (SAFETY §2 break-out defense) — NO untrusted string
    flows unwrapped. The wrapped message is LAST so it is the immediate thing the
    model answers.
    """
    parts: List[str] = []

    facts = _kb_facts_block(request)
    if facts:
        parts.append(facts)

    if request.thread_context:
        joined = "\n".join(request.thread_context)
        parts.append(wrap_user_input("thread_context", joined))

    parts.append(wrap_user_input("message", request.text))
    return "\n\n".join(parts)


__all__ = ["build_user_prompt", "cited_chunk_ids"]
