import json

import pytest

from mp_publisher.errors import StateConflict
from mp_publisher.legacy import import_file
from mp_publisher.models import Article
from mp_publisher.state import StateStore


def _article(digest="a" * 64):
    return Article("x", "2026-11-06", "x-daily:2026-11-06", "page", "label", "title",
                   "summary", "body", "html", digest)


def test_snapshot_is_immutable_and_database_is_private(tmp_path):
    path = tmp_path / "state.sqlite"
    store = StateStore(path)
    store.save_snapshot(_article())
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(StateConflict):
        store.save_snapshot(_article("b" * 64))


def test_history_success_prevents_new_delivery(tmp_path):
    rows = [{
        "origin": "x-attempts", "legacy_key": "hash", "profile": "x",
        "period": "2026-11-06", "channel": "wechat", "state": "SUCCESS",
    }]
    source = tmp_path / "history.json"
    source.write_text(json.dumps(rows), encoding="utf-8")
    store = StateStore(tmp_path / "state.sqlite")
    assert import_file(store, source) == 1
    assert store.delivery_state("x-daily:2026-11-06", "wechat") == "SUCCESS"
    with pytest.raises(StateConflict):
        store.reserve("x-daily:2026-11-06", "wechat")


def test_unknown_manual_rearm_requires_evidence_and_is_audited(tmp_path):
    store = StateStore(tmp_path / "state.sqlite")
    store.save_snapshot(_article())
    key = _article().source_key
    store.mark(key, "wechat", "UNKNOWN", detail="ambiguous remote response")
    with pytest.raises(StateConflict, match="evidence"):
        store.rearm_verified_absent(key, "wechat", "too short")
    with pytest.raises(StateConflict, match="evidence"):
        store.rearm_verified_absent(key, "wechat", "checked\nwith new line and no match")
    assert store.delivery_state(key, "wechat") == "UNKNOWN"
    store.rearm_verified_absent(key, "wechat", "Manual WeChat draftlist review 2026-11-06: confirmed no matching draft")
    assert store.delivery_state(key, "wechat") == "PENDING"
    with store.connect() as db:
        row = db.execute(
            "SELECT prior_state,decision,evidence FROM manual_resolutions WHERE source_key=?",
            (key,),
        ).fetchone()
        assert row["prior_state"] == "UNKNOWN"
        assert row["decision"] == "VERIFIED_ABSENT"
        assert "confirmed no matching" in row["evidence"]
    with pytest.raises(StateConflict, match="Only UNKNOWN"):
        store.rearm_verified_absent(key, "wechat", "Second attempt must never work after first was rearmed")
