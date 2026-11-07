from __future__ import annotations

from dataclasses import dataclass

import httpx

from . import config
from .channels import create_wechat_draft
from .errors import PublisherError
from .models import Article
from .notion import NotionReader, load_article
from .profiles import Profile
from .state import StateStore


@dataclass(frozen=True)
class RunResult:
    article: Article
    wechat: str


def read_article(profile: Profile, period: str, *, transport: httpx.BaseTransport | None = None) -> Article:
    reader = NotionReader(config.required("NOTION_API_KEY"), transport=transport)
    try:
        return load_article(reader, profile, period)
    finally:
        reader.close()


def run(profile: Profile, period: str, store: StateStore, *, article: Article | None = None) -> RunResult:
    if not config.profile_enabled(profile.name) or not config.wechat_enabled(profile.name):
        raise PublisherError(f"WeChat profile {profile.name} is disabled")
    article = article or read_article(profile, period)
    store.save_snapshot(article)
    previous = store.delivery_state(article.source_key, "wechat")
    if previous == "SUCCESS":
        # A past success must never contact the WeChat API again.
        return RunResult(article, "ALREADY_DONE")
    if previous != "PENDING":
        raise PublisherError(f"wechat=state {previous} requires reconciliation")
    create_wechat_draft(
        article,
        store,
        app_id=config.required("WECHAT_APP_ID"),
        app_secret=config.required("WECHAT_APP_SECRET"),
        cover_media_id=config.cover_media_id(profile.name),
    )
    return RunResult(article, "SUCCESS")
