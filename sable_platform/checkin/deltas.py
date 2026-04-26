"""Step 2 of client_checkin_loop: week-over-week deltas.

Pure functions. Inputs are dicts (current Tier 1/2 + previous snapshot's
{tier1, tier2}). Outputs are MetricDelta records the renderer formats and
the synthesizer reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricDelta:
    key: str
    current: Any
    previous: Any
    delta: float | None  # absolute change; None when either side is missing
    pct_change: float | None  # fractional change (0.05 = +5%); None when prev is 0/missing
    direction: str  # 'up' | 'down' | 'flat' | 'no_baseline'

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "current": self.current,
            "previous": self.previous,
            "delta": self.delta,
            "pct_change": self.pct_change,
            "direction": self.direction,
        }


@dataclass
class DeltaReport:
    tier1: list[MetricDelta] = field(default_factory=list)
    tier2: list[MetricDelta] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[dict]]:
        return {
            "tier1": [d.as_dict() for d in self.tier1],
            "tier2": [d.as_dict() for d in self.tier2],
        }


def _coerce_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):  # bool is subclass of int — exclude
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _delta(current: Any, previous: Any, *, key: str) -> MetricDelta:
    cur_n = _coerce_number(current)
    prev_n = _coerce_number(previous)

    if cur_n is None or prev_n is None:
        return MetricDelta(
            key=key, current=current, previous=previous,
            delta=None, pct_change=None,
            direction="no_baseline" if prev_n is None else "flat",
        )

    diff = cur_n - prev_n
    pct = (diff / prev_n) if prev_n != 0 else None

    if abs(diff) < 1e-9:
        direction = "flat"
    elif diff > 0:
        direction = "up"
    else:
        direction = "down"

    return MetricDelta(
        key=key, current=current, previous=previous,
        delta=round(diff, 4), pct_change=round(pct, 4) if pct is not None else None,
        direction=direction,
    )


def compute_deltas(
    current_tier1: dict[str, Any],
    current_tier2: dict[str, Any],
    previous_metrics: dict[str, Any] | None,
) -> DeltaReport:
    """Compare current Tier 1/2 against the previous metric_snapshot.

    `previous_metrics` shape: {"tier1": {...}, "tier2": {...}, ...}, or None / {}
    on the first run. With no baseline, every delta direction is 'no_baseline'.
    """
    prev = previous_metrics or {}
    prev_t1 = prev.get("tier1") or {}
    prev_t2 = prev.get("tier2") or {}

    return DeltaReport(
        tier1=[_delta(current_tier1.get(k), prev_t1.get(k), key=k) for k in current_tier1],
        tier2=[_delta(current_tier2.get(k), prev_t2.get(k), key=k) for k in current_tier2],
    )
