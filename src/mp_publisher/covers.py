"""Deterministic, private-media-id WeChat cover selection.

A single head-image permanent MediaID per article is used for both 2.35:1 and
1:1 placements. The source_key determines the variant so retries remain stable.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os

from . import config
from .errors import PublisherError
from .models import Article

CROP_WIDE = "0_0_1_1"
CROP_SQUARE = "0.2872_0_0.7128_1"
POOL_IDS = ("overseas", "x", "producthunt", "trustmrr_weekly", "trustmrr_monthly")
POOL_CODES = {"overseas": "OS", "x": "XI", "producthunt": "PH",
              "trustmrr_weekly": "TW", "trustmrr_monthly": "TM"}


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


def select_cover(article: Article) -> CoverChoice:
    key = pool_id_for(article)
    pools = _read_pool()
    if key not in pools:
        # Migration is deliberately opt-in per column; keep known-good old ID.
        return CoverChoice(config.cover_media_id(article.profile))
    index = int.from_bytes(hashlib.sha256(article.source_key.encode("utf-8")).digest()[:8], "big") % 3
    return CoverChoice(
        media_id=pools[key][index],
        variant_id=f"{POOL_CODES[key]}-{index + 1:02d}",
        crop_wide=CROP_WIDE,
        crop_square=CROP_SQUARE,
    )
