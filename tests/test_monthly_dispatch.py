"""TrustMRR must deliver weekly and monthly drafts independently."""
import argparse

from mp_publisher import cli
from mp_publisher.errors import PublisherError, SkipArticle
from mp_publisher.models import Article
from mp_publisher.runner import RunResult


def _result(period):
    kind, value = period.split(":", 1)
    item = Article(
        profile="trustmrr", period=period,
        source_key=f"trustmrr-{kind}:{value}",
        notion_page_id="page", notion_title="title", title="title",
        summary="summary", body_markdown="body", wechat_html="<p>body</p>",
        content_sha256="a" * 64,
    )
    return RunResult(item, "SUCCESS")


def _args(*options):
    return cli.parser().parse_args(["run", "--profile", "trustmrr", *options])


def test_first_monday_dispatches_weekly_and_monthly(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "scheduled_periods", lambda profile: (
        "weekly:2026-11-02", "monthly:2026-11",
    ))
    monkeypatch.setattr(cli.config, "state_path", lambda: tmp_path / "state.sqlite")

    def fake_run(profile, period, store):
        called.append(period)
        return _result(period)

    monkeypatch.setattr(cli, "run", fake_run)
    result = cli.execute(_args())
    assert result["status"] == "COMPLETED"
    assert called == ["weekly:2026-11-02", "monthly:2026-11"]
    assert [item["status"] for item in result["reports"]] == ["COMPLETED", "COMPLETED"]


def test_monthly_absent_does_not_block_weekly(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "scheduled_periods", lambda profile: (
        "weekly:2026-11-02", "monthly:2026-11",
    ))
    monkeypatch.setattr(cli.config, "state_path", lambda: tmp_path / "state.sqlite")

    def fake_run(profile, period, store):
        if period.startswith("monthly:"):
            raise SkipArticle("Notion monthly article is not READY")
        return _result(period)

    monkeypatch.setattr(cli, "run", fake_run)
    result = cli.execute(_args())
    assert result["status"] == "COMPLETED"
    assert [item["status"] for item in result["reports"]] == ["COMPLETED", "SKIPPED"]


def test_weekly_failure_does_not_block_independent_monthly(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "scheduled_periods", lambda profile: (
        "weekly:2026-11-02", "monthly:2026-11",
    ))
    monkeypatch.setattr(cli.config, "state_path", lambda: tmp_path / "state.sqlite")

    def fake_run(profile, period, store):
        called.append(period)
        if period.startswith("weekly:"):
            raise PublisherError("Notion weekly read failed")
        return _result(period)

    monkeypatch.setattr(cli, "run", fake_run)
    result = cli.execute(_args())
    assert called == ["weekly:2026-11-02", "monthly:2026-11"]
    assert result["status"] == "FAILED"
    assert [item["status"] for item in result["reports"]] == ["FAILED", "COMPLETED"]


def test_explicit_monthly_runs_only_that_period(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli.config, "state_path", lambda: tmp_path / "state.sqlite")
    def fake_run(profile, period, store):
        called.append(period)
        return _result(period)
    monkeypatch.setattr(cli, "run", fake_run)
    result = cli.execute(_args("--period", "monthly:2026-11"))
    assert called == ["monthly:2026-11"]
    assert result["status"] == "COMPLETED"
