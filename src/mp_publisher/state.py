from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from .errors import StateConflict
from .models import Article


SCHEMA_VERSION = 1
TERMINAL_OR_AMBIGUOUS = {"SUCCESS", "UNKNOWN", "INTENT", "FAILED"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        created = not self.path.exists()
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
        finally:
            db.close()
        if created and self.path.exists():
            os.chmod(self.path, 0o600)

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS articles (
                    source_key TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    period TEXT NOT NULL,
                    notion_page_id TEXT NOT NULL,
                    notion_title TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    body_markdown TEXT NOT NULL,
                    wechat_html TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    content_version INTEGER NOT NULL,
                    snapshotted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    source_key TEXT NOT NULL,
                    channel TEXT NOT NULL CHECK(channel = 'wechat'),
                    state TEXT NOT NULL CHECK(state IN ('PENDING','INTENT','SUCCESS','UNKNOWN','FAILED')),
                    receipt TEXT,
                    detail TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_key, channel),
                    FOREIGN KEY (source_key) REFERENCES articles(source_key) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS manual_resolutions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    channel TEXT NOT NULL CHECK(channel = 'wechat'),
                    prior_state TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (source_key) REFERENCES articles(source_key) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS legacy_imports (
                    origin TEXT NOT NULL,
                    legacy_key TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    PRIMARY KEY (origin, legacy_key)
                );
                CREATE TABLE IF NOT EXISTS execution_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    period TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('SKIPPED')),
                    reason_code TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_events_profile_period
                    ON execution_events(profile, period, occurred_at DESC);
            """)
            row = db.execute("SELECT version FROM schema_meta").fetchone()
            if row is None:
                db.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row[0] != SCHEMA_VERSION:
                raise StateConflict(f"Unsupported state schema version: {row[0]}")
            db.commit()

    def save_snapshot(self, article: Article) -> None:
        self.initialize()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute(
                "SELECT content_sha256, notion_page_id FROM articles WHERE source_key=?",
                (article.source_key,),
            ).fetchone()
            if found:
                if found["content_sha256"] != article.content_sha256 or found["notion_page_id"] != article.notion_page_id:
                    raise StateConflict("Existing sourceKey snapshot differs; refusing overwrite")
                db.commit()
                return
            db.execute("""
                INSERT INTO articles(
                    source_key,profile,period,notion_page_id,notion_title,title,summary,
                    body_markdown,wechat_html,content_sha256,content_version,snapshotted_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                article.source_key, article.profile, article.period, article.notion_page_id,
                article.notion_title, article.title, article.summary, article.body_markdown,
                article.wechat_html, article.content_sha256, article.version, utcnow(),
            ))
            db.execute("INSERT INTO deliveries(source_key,channel,state,updated_at) VALUES (?,?,?,?)",
                       (article.source_key, "wechat", "PENDING", utcnow()))
            db.commit()

    def delivery_state(self, source_key: str, channel: str) -> str | None:
        self.initialize()
        with self.connect() as db:
            row = db.execute("SELECT state FROM deliveries WHERE source_key=? AND channel=?",
                             (source_key, channel)).fetchone()
            return row[0] if row else None

    def reserve(self, source_key: str, channel: str) -> None:
        self.initialize()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM deliveries WHERE source_key=? AND channel=?",
                             (source_key, channel)).fetchone()
            if row is None:
                raise StateConflict("Missing article snapshot for delivery")
            if row[0] != "PENDING":
                raise StateConflict(f"{channel} delivery is {row[0]}; manual reconciliation required")
            db.execute("UPDATE deliveries SET state='INTENT',updated_at=? WHERE source_key=? AND channel=?",
                       (utcnow(), source_key, channel))
            db.commit()

    def mark(self, source_key: str, channel: str, state: str, *, receipt: str | None = None,
             detail: str | None = None) -> None:
        if state not in {"SUCCESS", "UNKNOWN", "FAILED", "PENDING"}:
            raise ValueError("Invalid delivery state")
        self.initialize()
        with self.connect() as db:
            changed = db.execute("""
                UPDATE deliveries SET state=?,receipt=?,detail=?,updated_at=?
                WHERE source_key=? AND channel=?
            """, (state, receipt, detail, utcnow(), source_key, channel)).rowcount
            if changed != 1:
                raise StateConflict("Missing delivery row")
            db.commit()

    def rearm_verified_absent(self, source_key: str, channel: str, evidence: str) -> None:
        """Manually unlock one UNKNOWN/INTENT, with durable audit evidence.

        This does not contact a channel or trigger a draft. It is only safe
        when an operator has conclusively established that the first request
        produced no draft; never infer this from a network timeout.
        """
        if channel != "wechat":
            raise StateConflict("Invalid channel")
        if not isinstance(evidence, str) or not 20 <= len(evidence.strip()) <= 500 or any(
                c in evidence for c in "\r\n"):
            raise StateConflict("Rearm requires 20-500 characters of single-line evidence")
        self.initialize()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM deliveries WHERE source_key=? AND channel=?",
                (source_key, channel),
            ).fetchone()
            if row is None or row["state"] not in {"UNKNOWN", "INTENT"}:
                raise StateConflict("Only UNKNOWN or INTENT may be manually rearmed")
            db.execute(
                "INSERT INTO manual_resolutions(source_key,channel,prior_state,decision,evidence,recorded_at)"
                " VALUES (?,?,?,?,?,?)",
                (source_key, channel, row["state"], "VERIFIED_ABSENT", evidence.strip(), utcnow()),
            )
            db.execute(
                "UPDATE deliveries SET state='PENDING',receipt=NULL,detail=?,updated_at=?"
                " WHERE source_key=? AND channel=?",
                ("manual verified-absent; refer to manual_resolutions", utcnow(), source_key, channel),
            )
            db.commit()

    def import_delivery(self, *, origin: str, legacy_key: str, source_key: str,
                        profile: str, period: str, channel: str, state: str) -> None:
        if channel != "wechat":
            raise ValueError("Only WeChat delivery history may be imported")
        if state not in {"SUCCESS", "UNKNOWN"}:
            raise ValueError("Legacy import accepts only SUCCESS or UNKNOWN")
        self.initialize()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                INSERT OR IGNORE INTO articles(
                    source_key,profile,period,notion_page_id,notion_title,title,summary,
                    body_markdown,wechat_html,content_sha256,content_version,snapshotted_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (source_key, profile, period, "legacy", "legacy", "legacy", "legacy",
                  "legacy", "legacy", "legacy", 0, utcnow()))
            db.execute("""
                INSERT INTO deliveries(source_key,channel,state,receipt,detail,updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(source_key,channel) DO UPDATE SET
                    state=CASE WHEN deliveries.state='UNKNOWN' OR excluded.state='UNKNOWN' THEN 'UNKNOWN' ELSE 'SUCCESS' END,
                    detail='legacy import',updated_at=excluded.updated_at
            """, (source_key, channel, state, None, "legacy import", utcnow()))
            db.execute("INSERT OR IGNORE INTO legacy_imports(origin,legacy_key,source_key,channel,imported_at) VALUES (?,?,?,?,?)",
                       (origin, legacy_key, source_key, channel, utcnow()))
            db.commit()

    def rows(self, profile: str | None = None) -> list[dict[str, object]]:
        self.initialize()
        sql = """
            SELECT a.source_key,a.profile,a.period,a.content_sha256,d.channel,d.state,d.updated_at
            FROM articles a JOIN deliveries d USING(source_key)
            WHERE d.channel='wechat'
        """
        args: tuple[object, ...] = ()
        if profile:
            sql += " AND a.profile=?"
            args = (profile,)
        sql += " ORDER BY a.source_key,d.channel"
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def record_skipped(self, *, profile: str, period: str, source_key: str,
                       reason_code: str = "NOTION_NOT_READY") -> None:
        """Persist a safe, minimal record when a scheduled article is not READY.

        This is separate from delivery state: no article snapshot or WeChat
        request is created for a missing Notion page.
        """
        if not all(isinstance(value, str) and value for value in (profile, period, source_key)):
            raise ValueError("Skipped execution identity is incomplete")
        if reason_code != "NOTION_NOT_READY":
            raise ValueError("Unsupported skip reason code")
        self.initialize()
        with self.connect() as db:
            db.execute(
                "INSERT INTO execution_events(source_key,profile,period,status,reason_code,occurred_at)"
                " VALUES (?,?,?,'SKIPPED',?,?)",
                (source_key, profile, period, reason_code, utcnow()),
            )
            db.commit()
