"""Vendored, in-tree copies of dependency-light engines from sibling Sable repos.

Each subpackage here is a ONE-WAY synced copy of a donor repo's frozen, dep-light
engine — NOT a sibling-repo import (which SablePlatform's pillar-1 rule forbids in
production paths) and NOT hand-written SP business logic. The packages here are
**GENERATED, NEVER EDITED IN PLACE**; they are refreshed by re-running the donor's
sync script and are content-hash drift-gated in CI.

Current members:
  * ``sable_pulse_core`` — the deterministic CM engine donated by ``sable-pulse``
    (``sable_pulse/core``), synced via that repo's ``scripts/sync_vendor.py``.
    Consumed by ``sable_platform.autocm`` (MEGAPLAN D-1 / R-1). See the SablePlatform
    ``CLAUDE.md`` "Architecture Decisions" record for the acknowledged pillar-2
    deviation, the named owner, and the generated-not-edited constraint.
"""
