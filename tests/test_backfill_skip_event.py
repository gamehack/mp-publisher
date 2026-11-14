"""Authorized backfill must preserve a durable not-READY operational signal."""
import sqlite3
import sys

import pytest

from mp_publisher import cli
from mp_publisher.errors import SkipArticle


def test_backfill_not_ready_records_skip_and_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLISH_PRODUCTHUNT_ENABLED", "true")
    monkeypatch.setenv("WECHAT_ENABLE_DRAFTS", "true")
    monkeypatch.setenv("WECHAT_PRODUCTHUNT_ENABLED", "true")
    path = tmp_path / "state.sqlite"

    def missing(*args, **kwargs):
        raise SkipArticle("Expected Notion article is missing")

    monkeypatch.setattr("mp_publisher.runner.read_article", missing)
    monkeypatch.setattr(sys, "argv", [
        "mp-publisher", "--state", str(path), "backfill", "--profile", "producthunt",
        "--period", "2026-11-13", "--authorize",
    ])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 3
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT source_key,status,reason_code FROM execution_events"
        ).fetchone()
    assert row == ("producthunt:2026-11-13", "SKIPPED", "NOTION_NOT_READY")
