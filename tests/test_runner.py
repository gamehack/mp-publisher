import pytest

from mp_publisher.errors import PublisherError, SkipArticle
from mp_publisher.models import Article
from mp_publisher.profiles import PROFILES
from mp_publisher.runner import run
from mp_publisher.state import StateStore


def article(period="2026-11-05"):
    return Article("producthunt", period, f"producthunt:{period}", "page", "label",
                   "title", "summary", "body", "html", "a" * 64)


def switches(monkeypatch):
    monkeypatch.setenv("PUBLISH_PRODUCTHUNT_ENABLED", "true")
    monkeypatch.setenv("WECHAT_ENABLE_DRAFTS", "true")
    monkeypatch.setenv("WECHAT_PRODUCTHUNT_ENABLED", "true")
    monkeypatch.setenv("WECHAT_APP_ID", "id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "secret")
    monkeypatch.setenv("WECHAT_THUMB_MEDIA_ID_PRODUCTHUNT", "cover")


def test_wechat_failure_never_calls_any_other_channel(tmp_path, monkeypatch):
    switches(monkeypatch)
    store = StateStore(tmp_path / "state.sqlite")
    calls = []

    def fail_wechat(*args, **kwargs):
        calls.append("wechat")
        raise PublisherError("wechat stopped")

    monkeypatch.setattr("mp_publisher.runner.create_wechat_draft", fail_wechat)
    with pytest.raises(PublisherError, match="wechat stopped"):
        run(PROFILES["producthunt"], "2026-11-05", store, article=article())
    assert calls == ["wechat"]
    assert store.rows()[0]["channel"] == "wechat"


def test_success_never_retries_and_ignores_removed_cms_switches(tmp_path, monkeypatch):
    switches(monkeypatch)
    monkeypatch.setenv("CMS_ENABLE_DRAFTS", "true")
    monkeypatch.setenv("CMS_PRODUCTHUNT_ENABLED", "true")
    store = StateStore(tmp_path / "state.sqlite")
    item = article()
    store.save_snapshot(item)
    store.mark(item.source_key, "wechat", "SUCCESS", receipt="existing-media-id")
    monkeypatch.setattr("mp_publisher.runner.create_wechat_draft",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resend")))
    result = run(PROFILES["producthunt"], "2026-11-05", store, article=item)
    assert result.wechat == "ALREADY_DONE"
    assert len(store.rows()) == 1


def test_unknown_blocks_and_never_retries(tmp_path, monkeypatch):
    switches(monkeypatch)
    store = StateStore(tmp_path / "state.sqlite")
    item = article()
    store.save_snapshot(item)
    store.mark(item.source_key, "wechat", "UNKNOWN")
    monkeypatch.setattr("mp_publisher.runner.create_wechat_draft",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not replay")))
    with pytest.raises(PublisherError, match="UNKNOWN requires reconciliation"):
        run(PROFILES["producthunt"], "2026-11-05", store, article=item)


def test_producthunt_missing_first_slot_then_fallback_once(tmp_path, monkeypatch):
    switches(monkeypatch)
    item = article("2026-11-06")
    store = StateStore(tmp_path / "state.sqlite")
    ready = False
    calls = []

    def fake_read_article(*args, **kwargs):
        if not ready:
            raise SkipArticle("Notion page is not READY")
        return item

    def deliver_wechat(article, state, **kwargs):
        calls.append("wechat")
        state.mark(article.source_key, "wechat", "SUCCESS", receipt="wx-draft")

    monkeypatch.setattr("mp_publisher.runner.read_article", fake_read_article)
    monkeypatch.setattr("mp_publisher.runner.create_wechat_draft", deliver_wechat)
    with pytest.raises(SkipArticle, match="not READY"):
        run(PROFILES["producthunt"], "2026-11-06", store)
    assert calls == []
    ready = True
    assert run(PROFILES["producthunt"], "2026-11-06", store).wechat == "SUCCESS"
    assert run(PROFILES["producthunt"], "2026-11-06", store).wechat == "ALREADY_DONE"
    assert calls == ["wechat"]


def test_master_switch_fail_closed(tmp_path, monkeypatch):
    switches(monkeypatch)
    monkeypatch.setenv("WECHAT_ENABLE_DRAFTS", "false")
    store = StateStore(tmp_path / "state.sqlite")
    with pytest.raises(PublisherError, match="disabled"):
        run(PROFILES["producthunt"], "2026-11-05", store, article=article())
    assert not store.path.exists()

def test_cli_records_missing_ready_for_ops_and_exits_nonzero(tmp_path, monkeypatch):
    import sqlite3
    import sys
    from mp_publisher import cli

    switches(monkeypatch)
    state_path = tmp_path / "state.sqlite"

    def missing_ready(*args, **kwargs):
        raise SkipArticle("Expected Notion article is missing")

    monkeypatch.setattr("mp_publisher.runner.read_article", missing_ready)
    monkeypatch.setattr(sys, "argv", [
        "mp-publisher", "--state", str(state_path),
        "run", "--profile", "producthunt", "--period", "2026-11-06",
    ])

    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 3

    with sqlite3.connect(state_path) as db:
        assert db.execute("SELECT count(*) FROM articles").fetchone()[0] == 0
        row = db.execute(
            "SELECT source_key,profile,period,status,reason_code "
            "FROM execution_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row == (
        "producthunt:2026-11-06", "producthunt", "2026-11-06",
        "SKIPPED", "NOTION_NOT_READY",
    )

