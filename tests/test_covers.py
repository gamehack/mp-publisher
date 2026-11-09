"""Cover pools are deterministic, private, and independent of delivery state."""
import json

import pytest

from mp_publisher.covers import CROP_SQUARE, CROP_WIDE, pool_id_for, select_cover
from mp_publisher.channels import wechat_payload
from mp_publisher.errors import PublisherError
from mp_publisher.models import Article


def story(profile="overseas", period="01", source_key="overseas-stories:01"):
    return Article(profile, period, source_key, "page", "title", "title", "summary",
                   "# body", "<p>body</p>", "a" * 64)


def test_existing_media_id_is_fallback_until_pool_is_configured(monkeypatch):
    monkeypatch.delenv("WECHAT_COVER_POOL_JSON", raising=False)
    monkeypatch.setenv("WECHAT_THUMB_MEDIA_ID_OVERSEAS", "original-cover")
    choice = select_cover(story())
    assert choice.media_id == "original-cover"
    assert choice.variant_id is None
    assert choice.crop_wide is None
    assert choice.crop_square is None
    assert "pic_crop_1_1" not in wechat_payload(story(), choice.media_id)["articles"][0]


def test_same_source_key_always_selects_same_variant(monkeypatch):
    monkeypatch.setenv("WECHAT_COVER_POOL_JSON", json.dumps({
        "overseas": ["material-os-01", "material-os-02", "material-os-03"]
    }))
    first = select_cover(story())
    assert first == select_cover(story())
    assert first.variant_id in {"OS-01", "OS-02", "OS-03"}
    assert first.media_id in {"material-os-01", "material-os-02", "material-os-03"}
    assert first.crop_wide == CROP_WIDE
    assert first.crop_square == CROP_SQUARE
    payload = wechat_payload(story(), first.media_id, crop_wide=first.crop_wide,
                             crop_square=first.crop_square)["articles"][0]
    assert payload["thumb_media_id"] == first.media_id
    assert payload["pic_crop_235_1"] == CROP_WIDE
    assert payload["pic_crop_1_1"] == CROP_SQUARE


def test_trustmrr_weekly_monthly_use_independent_pools(monkeypatch):
    monkeypatch.setenv("WECHAT_COVER_POOL_JSON", json.dumps({
        "trustmrr_weekly": ["weekly-m-1", "weekly-m-2", "weekly-m-3"],
        "trustmrr_monthly": ["monthly-m-1", "monthly-m-2", "monthly-m-3"],
    }))
    week = story("trustmrr", "weekly:2026-11-09", "trustmrr-weekly:2026-11-09")
    month = story("trustmrr", "monthly:2026-11", "trustmrr-monthly:2026-11")
    assert pool_id_for(week) == "trustmrr_weekly"
    assert pool_id_for(month) == "trustmrr_monthly"
    assert select_cover(week).media_id.startswith("weekly-")
    assert select_cover(month).media_id.startswith("monthly-")
    assert select_cover(week).variant_id.startswith("TW-")
    assert select_cover(month).variant_id.startswith("TM-")


@pytest.mark.parametrize("raw", ["not-json", "[]", '{"bad":["x","y","z"]}',
                                '{"x":["a","b"]}', '{"x":["same","same","same"]}'])
def test_invalid_pool_fails_closed_without_secret_echo(monkeypatch, raw):
    monkeypatch.setenv("WECHAT_COVER_POOL_JSON", raw)
    with pytest.raises(PublisherError) as err:
        select_cover(story())
    assert raw not in str(err.value)


def test_unconfigured_pool_falls_back_independently(monkeypatch):
    monkeypatch.setenv("WECHAT_COVER_POOL_JSON", json.dumps({
        "producthunt": ["prod-01", "prod-02", "prod-03"]
    }))
    monkeypatch.setenv("WECHAT_THUMB_MEDIA_ID_OVERSEAS", "old-overseas")
    assert select_cover(story()).media_id == "old-overseas"
    ph = story("producthunt", "2026-11-09", "producthunt:2026-11-09")
    assert select_cover(ph).media_id.startswith("prod-")


def test_invalid_trustmrr_period_fails(monkeypatch):
    with pytest.raises(PublisherError, match="period"):
        pool_id_for(story("trustmrr", "bogus", "trustmrr:bogus"))
