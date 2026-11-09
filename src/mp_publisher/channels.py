from __future__ import annotations

from typing import Any

import httpx

from .errors import PublisherError, UnknownDelivery
from .models import Article
from .state import StateStore


WECHAT_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
WECHAT_DRAFT_URL = "https://api.weixin.qq.com/cgi-bin/draft/add"


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    if len(response.content) > 8192:
        raise PublisherError("WeChat response exceeds safety limit")
    try:
        value = response.json()
    except ValueError:
        raise PublisherError("WeChat returned invalid JSON") from None
    if not isinstance(value, dict):
        raise PublisherError("WeChat returned a non-object")
    return value


def wechat_payload(article: Article, cover: str, author: str = "潜界 AI",
                   *, crop_wide: str | None = None, crop_square: str | None = None
                   ) -> dict[str, object]:
    if not cover.strip():
        raise PublisherError("WeChat permanent cover media_id is required")
    if not article.title or len(article.title.encode("utf-8")) > 64:
        raise PublisherError("WeChat title exceeds 64 UTF-8 bytes")
    if not article.summary.strip() or not article.wechat_html.strip():
        raise PublisherError("WeChat article content is incomplete")
    entry: dict[str, object] = {
        "title": article.title,
        "author": author,
        "digest": article.summary[:120],
        "content": article.wechat_html,
        "thumb_media_id": cover.strip(),
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }
    if crop_wide is not None:
        entry["pic_crop_235_1"] = crop_wide
    if crop_square is not None:
        entry["pic_crop_1_1"] = crop_square
    return {"articles": [entry]}


def check_wechat_credentials(*, app_id: str, app_secret: str, client: httpx.Client) -> str:
    """Read-only token check; never logs access tokens or secret values."""
    if not app_id or not app_secret:
        raise PublisherError("WeChat credentials are missing")
    try:
        response = client.get(WECHAT_TOKEN_URL, params={
            "grant_type": "client_credential", "appid": app_id, "secret": app_secret,
        })
    except httpx.HTTPError:
        raise PublisherError("WeChat credential check failed") from None
    if response.status_code != 200:
        raise PublisherError(f"WeChat credential check returned HTTP {response.status_code}")
    data = _safe_json(response)
    if data.get("errcode") or not data.get("access_token"):
        raise PublisherError(f"WeChat credential check failed with code {data.get('errcode', 'missing_token')}")
    return str(data["access_token"])


def create_wechat_draft(
    article: Article,
    store: StateStore,
    *,
    app_id: str,
    app_secret: str,
    cover_media_id: str,
    crop_wide: str | None = None,
    crop_square: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Make at most one draft/add attempt; ambiguous outcomes remain UNKNOWN."""
    payload = wechat_payload(article, cover_media_id,
                             crop_wide=crop_wide, crop_square=crop_square)
    session = client or httpx.Client(timeout=30, follow_redirects=False)
    try:
        token = check_wechat_credentials(app_id=app_id, app_secret=app_secret, client=session)
        store.reserve(article.source_key, "wechat")
        try:
            response = session.post(WECHAT_DRAFT_URL, params={"access_token": token}, json=payload)
            data = _safe_json(response)
            if response.status_code != 200 or data.get("errcode", 0) != 0 or not data.get("media_id"):
                raise RuntimeError("Unconfirmed WeChat draft acknowledgement")
        except Exception:
            store.mark(article.source_key, "wechat", "UNKNOWN",
                       detail="draft/add outcome requires manual reconciliation")
            raise UnknownDelivery("WeChat draft/add outcome is UNKNOWN; do not retry") from None
        receipt = str(data["media_id"])
        store.mark(article.source_key, "wechat", "SUCCESS", receipt=receipt)
        return receipt
    finally:
        if client is None:
            session.close()
