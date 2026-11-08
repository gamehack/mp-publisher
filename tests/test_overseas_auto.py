from argparse import Namespace
from types import SimpleNamespace

import pytest

from mp_publisher import cli
from mp_publisher.errors import PublisherError, SkipArticle
from mp_publisher.notion import NotionReader


def test_notion_issue_inventory_rejects_gaps():
    reader = object.__new__(NotionReader)
    reader.children = lambda _: [
        {"type": "child_page", "child_page": {"title": "海外创业志 01|A"}},
        {"type": "child_page", "child_page": {"title": "海外创业志 03|C"}},
    ]
    with pytest.raises(PublisherError, match="gap"):
        reader.overseas_issues("x")


def test_auto_skips_successful_ledger_and_delivers_only_next(monkeypatch):
    monkeypatch.setenv("NOTION_API_KEY", "placeholder")
    class Reader:
        def __init__(self, *_): pass
        def overseas_issues(self, *_): return ("01", "02", "03")
        def close(self): pass
    class Store:
        def delivery_state(self, key, channel): return "SUCCESS" if key.endswith(":01") else None
    monkeypatch.setattr(cli, "NotionReader", Reader)
    monkeypatch.setattr(cli, "StateStore", lambda *_: Store())
    delivered = []
    def deliver(profile, period, store):
        delivered.append(period)
        return SimpleNamespace(article=SimpleNamespace(public_summary=lambda: {"period": period}), wechat="SUCCESS")
    monkeypatch.setattr(cli, "run", deliver)
    result = cli.execute(Namespace(command="run", env_file=None, state=None, profile="overseas", period=None, auto=True))
    assert result["status"] == "COMPLETED" and delivered == ["02"]


def test_auto_stops_at_first_not_ready(monkeypatch):
    monkeypatch.setenv("NOTION_API_KEY", "placeholder")
    class Reader:
        def __init__(self, *_): pass
        def overseas_issues(self, *_): return ("01", "02", "03")
        def close(self): pass
    monkeypatch.setattr(cli, "NotionReader", Reader)
    monkeypatch.setattr(cli, "StateStore", lambda *_: SimpleNamespace(delivery_state=lambda *_: "SUCCESS"))
    # First not-yet-SUCCESS is 02, even if 03 might be READY.
    monkeypatch.setattr(cli, "StateStore", lambda *_: SimpleNamespace(delivery_state=lambda key, channel: "SUCCESS" if key.endswith(":01") else None))
    monkeypatch.setattr(cli, "run", lambda *_: (_ for _ in ()).throw(SkipArticle("Notion page is not READY")))
    result = cli.execute(Namespace(command="run", env_file=None, state=None, profile="overseas", period=None, auto=True))
    assert result["status"] == "SKIPPED" and result["period"] == "02"
