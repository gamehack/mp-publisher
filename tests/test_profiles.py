from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from mp_publisher.errors import PublisherError
from mp_publisher.profiles import PROFILES, default_period, scheduled_periods, source_key, validate_period


def test_source_keys_and_period_validation():
    assert source_key(PROFILES["x"], "2026-11-06") == "x-daily:2026-11-06"
    assert source_key(PROFILES["producthunt"], "2026-11-05") == "producthunt:2026-11-05"
    assert source_key(PROFILES["trustmrr"], "weekly:2026-11-02") == "trustmrr-weekly:2026-11-02"
    assert source_key(PROFILES["trustmrr"], "monthly:2026-10") == "trustmrr-monthly:2026-10"
    with pytest.raises(PublisherError):
        validate_period(PROFILES["trustmrr"], "weekly:2026-11-03")


def test_producthunt_default_uses_last_completed_la_day():
    now = datetime(2026, 11, 6, 18, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert default_period(PROFILES["producthunt"], now) == "2026-11-05"


def test_producthunt_two_fixed_beijing_timer_slots_and_same_period():
    from pathlib import Path

    timer = (Path(__file__).resolve().parents[1] / "systemd" / "mp-publisher-producthunt.timer").read_text()
    calendars = [line for line in timer.splitlines() if line.startswith("OnCalendar=")]
    assert calendars == [
        "OnCalendar=*-*-* 17:30:00 Asia/Shanghai",
        "OnCalendar=*-*-* 18:30:00 Asia/Shanghai",
    ]
    assert "Persistent=false" in timer
    assert "Unit=mp-publisher@producthunt.service" in timer
    assert PROFILES["producthunt"].schedule == "daily 17:30 + 18:30 fallback Asia/Shanghai"

    tz = ZoneInfo("Asia/Shanghai")
    first = datetime(2026, 11, 7, 17, 30, tzinfo=tz)
    fallback = datetime(2026, 11, 7, 18, 30, tzinfo=tz)
    assert default_period(PROFILES["producthunt"], first) == "2026-11-06"
    assert default_period(PROFILES["producthunt"], fallback) == "2026-11-06"


def test_trust_default_is_current_beijing_monday():
    now = datetime(2026, 11, 6, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert default_period(PROFILES["trustmrr"], now) == "weekly:2026-11-02"


def test_trustmrr_first_monday_produces_both_report_periods():
    tz = ZoneInfo("Asia/Shanghai")
    assert scheduled_periods(PROFILES["trustmrr"], datetime(2026, 11, 2, 3, 0, tzinfo=tz)) == (
        "weekly:2026-11-02", "monthly:2026-11",
    )
    assert scheduled_periods(PROFILES["trustmrr"], datetime(2026, 12, 7, 3, 0, tzinfo=tz)) == (
        "weekly:2026-12-07", "monthly:2026-12",
    )
    assert scheduled_periods(PROFILES["trustmrr"], datetime(2026, 11, 9, 3, 0, tzinfo=tz)) == (
        "weekly:2026-11-09",
    )


def test_schedule_periods_does_not_add_monthly_for_other_profiles():
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 11, 2, 18, 30, tzinfo=tz)
    assert scheduled_periods(PROFILES["x"], now) == ("2026-11-02",)
    assert scheduled_periods(PROFILES["producthunt"], now) == ("2026-11-01",)


def test_monthly_canonical_period_is_current_month():
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2027, 2, 1, 3, 0, tzinfo=tz)
    assert scheduled_periods(PROFILES["trustmrr"], now) == (
        "weekly:2027-02-01", "monthly:2027-02",
    )
