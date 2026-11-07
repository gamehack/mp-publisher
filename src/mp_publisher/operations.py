"""Read-only delivery diagnostics and consistent private SQLite backups.

These functions never call WeChat, Notion or any content producer.
"""
from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import sqlite3
import tempfile

from .errors import PublisherError
from .state import StateStore


BLOCKED_STATES = {"UNKNOWN", "INTENT", "FAILED"}


def _existing_state(store: StateStore) -> Path:
    path = store.path
    if path.is_symlink() or not path.is_file():
        raise PublisherError("State SQLite file is missing or is a symlink")
    return path


def inspect_state(store: StateStore, profile: str | None = None) -> dict[str, object]:
    """Report suspicious delivery states without modifying state or fetching secrets."""
    source = _existing_state(store)
    query = """
        SELECT d.source_key, d.channel, d.state, d.updated_at
        FROM deliveries AS d JOIN articles AS a ON a.source_key = d.source_key
        WHERE d.channel='wechat'
    """
    params: tuple[str, ...] = ()
    if profile:
        query += " AND a.profile = ?"
        params = (profile,)
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(item) for item in db.execute(query, params)]
    except sqlite3.Error:
        raise PublisherError("Cannot inspect SQLite state database") from None
    counts = Counter(str(item["state"]) for item in rows)
    issues = [
        {"source_key": str(item["source_key"]), "channel": str(item["channel"]),
         "state": str(item["state"]), "updated_at": str(item["updated_at"])}
        for item in rows if item["state"] in BLOCKED_STATES
    ]
    return {
        "status": "ACTION_REQUIRED" if issues else "CLEAR",
        "scope": "local_delivery_state_only",
        "profile": profile or "all",
        "source_keys": len({str(item["source_key"]) for item in rows}),
        "channel_states": dict(sorted(counts.items())),
        "issues": issues,
    }


def backup_state(store: StateStore, destination: str | Path) -> dict[str, object]:
    """Create an atomic SQLite online backup, including uncheckpointed WAL data.

    Source and target remain private. Never copy a live SQLite database with
    shutil.copy, because uncheckpointed WAL transactions can be missed.
    """
    source = _existing_state(store)
    target = Path(destination)
    if target.is_symlink() or target.exists():
        raise PublisherError("Backup destination already exists; refusing overwrite")
    if target == source:
        raise PublisherError("Backup must not overwrite the source database")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".mp-state-", suffix=".sqlite", dir=target.parent)
    os.close(fd)
    try:
        os.chmod(temp, 0o600)
        # URI mode=ro prevents accidental database creation or writes.
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30) as original:
            with sqlite3.connect(temp, timeout=30) as copy:
                original.backup(copy)
        with sqlite3.connect(Path(temp).resolve().as_uri() + "?mode=ro", uri=True) as verify:
            if verify.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise PublisherError("Backup SQLite integrity check failed")
            rows = verify.execute("SELECT count(*) FROM deliveries").fetchone()[0]
        os.replace(temp, target)
        os.chmod(target, 0o600)
        return {"status": "BACKUP_CREATED", "path": str(target), "delivery_rows": rows}
    except sqlite3.Error:
        raise PublisherError("SQLite online backup or integrity verification failed") from None
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
