"""Deterministic, private-media-id WeChat cover selection.

A single head-image permanent MediaID per article is used for both 2.35:1 and
1:1 placements. The source_key determines the variant so retries remain stable.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re

from . import config
from .errors import PublisherError
from .models import Article

CROP_WIDE = "0_0_1_1"
CROP_SQUARE = "0.2872_0_0.7128_1"
POOL_IDS = ("overseas", "x", "producthunt", "trustmrr_weekly", "trustmrr_monthly")
POOL_CODES = {"overseas": "OS", "x": "XI", "producthunt": "PH",
              "trustmrr_weekly": "TW", "trustmrr_monthly": "TM"}

# Index order corresponds to the three variants of each private MediaID pool.
# Use subject-specific words; column names and generic words are deliberately
# omitted so a standard article title does not bias every selection.
SCENE_KEYWORDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "overseas": (
        ("开发", "代码", "编程", "程序员", "工程师", "软件", "saas", "技术"),
        ("客户", "销售", "付费", "团队", "社区", "工作室", "内容创作"),
        ("旅行", "远程", "游牧", "跨境", "咖啡馆", "移居"),
    ),
    "x": (
        ("市场", "经济", "融资", "投资", "资本", "交易", "宏观"),
        ("ai", "智能体", "模型", "社交", "grok", "人工智能"),
        ("全球", "跨境", "国际", "出海", "海外", "供应链"),
    ),
    "producthunt": (
        ("手机", "app", "移动端", "ios", "android"),
        ("代码", "开发", "api", "github", "sdk", "编程"),
        ("效率", "办公", "生产力", "笔记", "协作", "工具箱"),
    ),
    "trustmrr_weekly": (
        ("飙升", "最快", "增长率"),
        ("榜单", "排行", "排名", "前25"),
        ("mrr", "收入", "营收"),
    ),
    "trustmrr_monthly": (
        ("趋势", "环比", "持续增长"),
        ("月份", "月历", "日历"),
        ("mrr", "收入", "营收", "经营"),
    ),
}


@dataclass(frozen=True)
class CoverChoice:
    media_id: str
    variant_id: str | None = None
    crop_wide: str | None = None
    crop_square: str | None = None


def pool_id_for(article: Article) -> str:
    if article.profile == "trustmrr":
        if article.period.startswith("weekly:"):
            return "trustmrr_weekly"
        if article.period.startswith("monthly:"):
            return "trustmrr_monthly"
        raise PublisherError("TrustMRR period needs weekly/monthly cover selection")
    if article.profile in {"overseas", "x", "producthunt"}:
        return article.profile
    raise PublisherError("Unknown cover profile")


def _read_pool() -> dict[str, tuple[str, str, str]]:
    """Read private IDs, never persist or log any media ID in Git/SQLite."""
    raw = os.getenv("WECHAT_COVER_POOL_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        raise PublisherError("WECHAT_COVER_POOL_JSON is not valid JSON") from None
    if not isinstance(parsed, dict):
        raise PublisherError("WECHAT_COVER_POOL_JSON must be an object")
    if set(parsed) - set(POOL_IDS):
        raise PublisherError("WECHAT_COVER_POOL_JSON contains an unknown pool")
    clean: dict[str, tuple[str, str, str]] = {}
    for key, values in parsed.items():
        if (not isinstance(values, list) or len(values) != 3 or
                not all(isinstance(x, str) and x and x == x.strip() and
                        5 <= len(x) <= 256 and not any(c.isspace() for c in x)
                        for x in values) or len(set(values)) != 3):
            raise PublisherError(f"{key} cover pool must have three distinct permanent media IDs")
        clean[key] = tuple(values)
    return clean


def _read_overrides() -> dict[str, str]:
    """Map exact article source keys to privately uploaded permanent MediaIDs."""
    raw = os.getenv("WECHAT_COVER_OVERRIDES_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        raise PublisherError("WECHAT_COVER_OVERRIDES_JSON is not valid JSON") from None
    if (not isinstance(parsed, dict) or len(parsed) > 500 or
            any(not isinstance(key, str) or not key or len(key) > 200 or
                not isinstance(media_id, str) or
                re.fullmatch(r"[A-Za-z0-9_-]{5,256}", media_id) is None
                for key, media_id in parsed.items())):
        raise PublisherError("WECHAT_COVER_OVERRIDES_JSON has invalid entries")
    return parsed


def _stable_index(article: Article) -> int:
    return int.from_bytes(hashlib.sha256(article.source_key.encode("utf-8")).digest()[:8], "big") % 3


def _contains(text: str, token: str) -> bool:
    if token.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", text) is not None
    return token in text


def _semantic_index(article: Article, pool_id: str) -> int | None:
    title = article.title.casefold().replace("海外创业志", "")
    text = f"{title} {article.summary.casefold()}"
    scores = [sum(_contains(text, token) for token in variant)
              for variant in SCENE_KEYWORDS[pool_id]]
    high = max(scores)
    if high == 0 or scores.count(high) != 1:
        return None
    return scores.index(high)


def select_cover(article: Article) -> CoverChoice:
    key = pool_id_for(article)
    pools = _read_pool()
    overrides = _read_overrides()
    route = os.getenv("WECHAT_COVER_ROUTING", "hash").strip().lower() or "hash"
    if route not in {"hash", "semantic"}:
        raise PublisherError("WECHAT_COVER_ROUTING must be hash or semantic")
    if article.source_key in overrides:
        return CoverChoice(overrides[article.source_key], "CUSTOM", CROP_WIDE, CROP_SQUARE)
    if key not in pools:
        # Migration is deliberately opt-in per column; keep known-good old ID.
        return CoverChoice(config.cover_media_id(article.profile))
    index = _stable_index(article)
    if route == "semantic":
        semantic = _semantic_index(article, key)
        if semantic is not None:
            index = semantic
    return CoverChoice(
        media_id=pools[key][index],
        variant_id=f"{POOL_CODES[key]}-{index + 1:02d}",
        crop_wide=CROP_WIDE,
        crop_square=CROP_SQUARE,
    )
