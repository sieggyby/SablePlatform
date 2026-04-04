"""Tests for cron schedule presets."""
from __future__ import annotations

from sable_platform.cron import SCHEDULE_PRESETS


def test_twice_weekly_preset_exists():
    assert "twice-weekly" in SCHEDULE_PRESETS


def test_twice_weekly_runs_monday_and_thursday():
    schedule = SCHEDULE_PRESETS["twice-weekly"]
    fields = schedule.split()
    assert len(fields) == 5
    # Day of week field should be "1,4" (Monday + Thursday)
    assert fields[4] == "1,4"


def test_all_presets_have_five_fields():
    for name, schedule in SCHEDULE_PRESETS.items():
        fields = schedule.split()
        assert len(fields) == 5, f"Preset {name!r} has {len(fields)} fields: {schedule}"
