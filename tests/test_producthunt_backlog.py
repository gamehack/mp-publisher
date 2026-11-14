"""Rolling Product Hunt catch-up must not replay a successful/ambiguous WeChat draft."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from mp_publisher import cli
from mp_publisher.errors import PublisherError, SkipArticle
from mp_publisher.profiles import producthunt_backlog_periods


PERIODS = ("2026-11-11", "2026-11-12", "2026-11-13")


class Ledger:
    def __init__(self, states=None):
        self.states = states or {}
        self.lookups = []

    def delivery_state(self, key, channel):
        assert channel == "wechat"
        self.lookups.append(key)
        return self.states.get(key)


def _execute(monkeypatch, ledger):
    monkeypatch.setattr(cli, "producthunt_backlog_periods", lambda: PERIODS)
    monkeypatch.setattr(cli, "StateStore", lambda *_: ledger)
    return cli.execute(cli.parser().parse_args(["run", "--profile", "producthunt", "--auto"]))


def test_recent_complete_los_angeles_days_are_oldest_first():
    now = datetime(2026, 11, 15, 1, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert producthunt_backlog_periods(now) == PERIODS
    with pytest.raises(PublisherError, match="1..7"):
        producthunt_backlog_periods(now, days=8)


def test_rolls_forward_after_a_missing_ready_and_does_not_resend_success(monkeypatch):
    ledger = Ledger({"producthunt:2026-11-11": "SUCCESS"})
    called = []

    def fake_run(profile, period, store):
        called.append(period)
        if period == "2026-11-12":
            raise SkipArticle("Expected Notion article is missing")
        return SimpleNamespace(
            article=SimpleNamespace(source_key=f"producthunt:{period}"), wechat="SUCCESS"
        )

    monkeypatch.setattr(cli, "_run_and_record", fake_run)
    result = _execute(monkeypatch, ledger)
    assert called == ["2026-11-12", "2026-11-13"]
    assert result["status"] == "SKIPPED"
    assert [r["status"] for r in result["reports"]] == ["SKIPPED", "COMPLETED"]
    assert len(ledger.lookups) == 3


def test_ambiguous_or_failed_delivery_requires_manual_reconciliation(monkeypatch):
    ledger = Ledger({
        "producthunt:2026-11-11": "UNKNOWN",
        "producthunt:2026-11-12": "INTENT",
        "producthunt:2026-11-13": "FAILED",
    })
    monkeypatch.setattr(cli, "_run_and_record",
                        lambda *_: (_ for _ in ()).throw(AssertionError("never send")))
    result = _execute(monkeypatch, ledger)
    assert result["status"] == "ACTION_REQUIRED"
    assert len(result["reports"]) == 3
    assert all(r["status"] == "ACTION_REQUIRED" for r in result["reports"])


def test_all_successful_periods_never_touch_notions_or_wechat(monkeypatch):
    ledger = Ledger({f"producthunt:{p}": "SUCCESS" for p in PERIODS})
    monkeypatch.setattr(cli, "_run_and_record",
                        lambda *_: (_ for _ in ()).throw(AssertionError("must not retry")))
    result = _execute(monkeypatch, ledger)
    assert result == {"profile": "producthunt", "status": "COMPLETED", "reports": []}


def test_auto_rejects_explicit_period(monkeypatch):
    with pytest.raises(PublisherError, match="without --period"):
        cli.execute(cli.parser().parse_args([
            "run", "--profile", "producthunt", "--period", "2026-11-13", "--auto",
        ]))


def test_systemd_uses_dedicated_backlog_one_shot():
    folder = Path(__file__).resolve().parents[1] / "systemd"
    unit = (folder / "mp-publisher-producthunt.service").read_text()
    timer = (folder / "mp-publisher-producthunt.timer").read_text()
    assert "run --profile producthunt --auto" in unit
    assert "mp-publisher-producthunt.lock" in unit
    assert "Unit=mp-publisher-producthunt.service" in timer
    assert "Persistent=false" in timer
