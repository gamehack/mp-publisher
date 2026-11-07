from pathlib import Path

import httpx
import pytest

from mp_publisher.channels import check_wechat_credentials, create_wechat_draft, wechat_payload
from mp_publisher.errors import PublisherError, StateConflict, UnknownDelivery
from mp_publisher.models import Article
from mp_publisher.state import StateStore


def article() -> Article:
    return Article(
        profile="producthunt", period="2026-11-05", source_key="producthunt:2026-11-05",
        notion_page_id="11111111-1111-4111-8111-111111111111", notion_title="2026-11-05｜标题",
        title="标题", summary="摘要", body_markdown="# 标题\n\n正文\n", wechat_html="<p>正文</p>",
        content_sha256="a" * 64,
    )


def ready_store(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.sqlite")
    store.save_snapshot(article())
    return store


def test_wechat_timeout_is_unknown_and_never_retried(tmp_path):
    calls = {"post": 0}

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, json={"access_token": "temporary"})
        calls["post"] += 1
        raise httpx.ReadTimeout("ambiguous", request=request)

    store = ready_store(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UnknownDelivery):
        create_wechat_draft(article(), store, app_id="id", app_secret="secret",
                            cover_media_id="cover", client=client)
    assert store.delivery_state(article().source_key, "wechat") == "UNKNOWN"
    with pytest.raises(StateConflict):
        create_wechat_draft(article(), store, app_id="id", app_secret="secret",
                            cover_media_id="cover", client=client)
    assert calls["post"] == 1


def test_wechat_success_stores_receipt(tmp_path):
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, json={"access_token": "temporary"})
        assert request.url.path == "/cgi-bin/draft/add"
        return httpx.Response(200, json={"media_id": "draft-id"})

    store = ready_store(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        receipt = create_wechat_draft(article(), store, app_id="id", app_secret="secret",
                                      cover_media_id="cover", client=client)
    assert receipt == "draft-id"
    assert store.delivery_state(article().source_key, "wechat") == "SUCCESS"


def test_ip_whitelist_check_does_not_post_or_leak_token():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.method)
        return httpx.Response(200, json={"errcode": 40164, "errmsg": "invalid ip"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PublisherError, match="40164"):
            check_wechat_credentials(app_id="id", app_secret="secret", client=client)
    assert calls == ["GET"]


def test_payload_retains_wechat_comment_settings():
    payload = wechat_payload(article(), "cover")
    assert payload["articles"][0]["need_open_comment"] == 1
    assert payload["articles"][0]["only_fans_can_comment"] == 0
