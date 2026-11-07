"""A WAL-aware clone of the old SQLite v1 must remain safe and read as WeChat only."""
import sqlite3

from mp_publisher.models import Article
from mp_publisher.state import StateStore


def test_copied_legacy_state_keeps_wechat_dedup_and_ignores_cms(tmp_path):
    path = tmp_path / "state.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            INSERT INTO schema_meta VALUES (1);
            CREATE TABLE articles (
                source_key TEXT PRIMARY KEY, profile TEXT NOT NULL, period TEXT NOT NULL,
                notion_page_id TEXT NOT NULL, notion_title TEXT NOT NULL, title TEXT NOT NULL,
                summary TEXT NOT NULL, body_markdown TEXT NOT NULL, wechat_html TEXT NOT NULL,
                content_sha256 TEXT NOT NULL, content_version INTEGER NOT NULL, snapshotted_at TEXT NOT NULL
            );
            CREATE TABLE deliveries (
                source_key TEXT NOT NULL, channel TEXT NOT NULL CHECK(channel IN ('wechat','cms')),
                state TEXT NOT NULL, receipt TEXT, detail TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY (source_key,channel)
            );
        """)
        db.execute("INSERT INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            "producthunt:2026-11-06", "producthunt", "2026-11-06", "page", "title",
            "title", "summary", "body", "html", "a" * 64, 1, "2026-11-07T13:00:00Z",
        ))
        for channel, state in (("wechat", "SUCCESS"), ("cms", "PENDING")):
            db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?)", (
                "producthunt:2026-11-06", channel, state,
                "wechat-receipt" if channel == "wechat" else None, None, "2026-11-07T13:00:00Z",
            ))
    store = StateStore(path)
    item = Article("producthunt", "2026-11-06", "producthunt:2026-11-06", "page", "title",
                   "title", "summary", "body", "html", "a" * 64)
    store.save_snapshot(item)
    assert store.delivery_state(item.source_key, "wechat") == "SUCCESS"
    assert store.rows() == [{
        "source_key": item.source_key, "profile": "producthunt", "period": item.period,
        "content_sha256": item.content_sha256, "channel": "wechat", "state": "SUCCESS",
        "updated_at": "2026-11-07T13:00:00Z",
    }]
