"""Offline operational recovery and diagnostic regressions."""
import os
from pathlib import Path
import sqlite3

import pytest

from mp_publisher.errors import PublisherError
from mp_publisher.models import Article
from mp_publisher.operations import backup_state, inspect_state
from mp_publisher.state import StateStore


def _store(tmp_path: Path) -> tuple[StateStore, str]:
    key = "producthunt:2026-11-05"
    article = Article("producthunt", "2026-11-05", key, "page", "title",
                      "title", "summary", "body", "html", "a" * 64)
    store = StateStore(tmp_path / "state.sqlite")
    store.save_snapshot(article)
    store.mark(key, "wechat", "SUCCESS", receipt="existing-media-id")
    return store, key


def test_health_report_is_read_only_and_flags_unknown(tmp_path):
    store, key = _store(tmp_path)
    result = inspect_state(store)
    assert result["status"] == "CLEAR"
    assert result["scope"] == "local_delivery_state_only"
    assert result["channel_states"] == {"SUCCESS": 1}
    store.mark(key, "wechat", "UNKNOWN")
    result = inspect_state(store, "producthunt")
    assert result["status"] == "ACTION_REQUIRED"
    assert result["issues"][0]["source_key"] == key
    assert result["issues"][0]["channel"] == "wechat"
    assert result["issues"][0]["state"] == "UNKNOWN"


def test_online_backup_preserves_delivery_rows_and_permissions(tmp_path):
    store, key = _store(tmp_path)
    target = tmp_path / "backups" / "snapshot.sqlite"
    result = backup_state(store, target)
    assert result["status"] == "BACKUP_CREATED"
    assert result["delivery_rows"] == 1
    assert target.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(target) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute(
            "SELECT state FROM deliveries WHERE source_key=? AND channel='wechat'", (key,)
        ).fetchone()[0] == "SUCCESS"
    with pytest.raises(PublisherError, match="already exists"):
        backup_state(store, target)


def test_operations_reject_missing_state_and_symlink(tmp_path):
    store = StateStore(tmp_path / "missing.sqlite")
    with pytest.raises(PublisherError, match="missing"):
        inspect_state(store)
    with pytest.raises(PublisherError, match="missing"):
        backup_state(store, tmp_path / "backup.sqlite")
    original, _ = _store(tmp_path)
    link = tmp_path / "link.sqlite"
    link.symlink_to(original.path)
    with pytest.raises(PublisherError, match="symlink"):
        inspect_state(StateStore(link))


def test_cli_backup_requires_explicit_authorization(tmp_path, monkeypatch):
    from mp_publisher import cli
    store, key = _store(tmp_path)
    dest = tmp_path / "saved.sqlite"
    args = cli.parser().parse_args([
        "--state", str(store.path), "backup-state", "--output", str(dest)
    ])
    with pytest.raises(PublisherError, match="--authorize"):
        cli.execute(args)
    assert not dest.exists()
    args = cli.parser().parse_args([
        "--state", str(store.path), "backup-state", "--output", str(dest), "--authorize"
    ])
    result = cli.execute(args)
    assert result["status"] == "BACKUP_CREATED"
