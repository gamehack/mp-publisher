from argparse import Namespace

import pytest

from mp_publisher.cli import _period
from mp_publisher.config import profile_enabled, wechat_enabled
from mp_publisher.errors import PublisherError
from mp_publisher.profiles import PROFILES, display_title, expected_title, source_key


def test_overseas_profile_is_manual_only():
    p = PROFILES["overseas"]
    assert p.parent_id == "4109e605-ccf8-81f8-9094-c5a0023b36b6"
    assert source_key(p, "2026-11-14") == "overseas-stories:2026-11-14"
    assert expected_title(p, "2026-11-14") == ("2026-11-14｜海外创业志｜", False)
    assert display_title(p, "2026-11-14", "2026-11-14｜海外创业志｜Mihai 的反差创业") == "Mihai 的反差创业"
    with pytest.raises(PublisherError, match="explicit --period"):
        _period(Namespace(profile="overseas", period=None))


def test_overseas_cannot_send_without_explicit_flags(monkeypatch):
    monkeypatch.delenv("PUBLISH_OVERSEAS_ENABLED", raising=False)
    monkeypatch.delenv("WECHAT_OVERSEAS_ENABLED", raising=False)
    assert profile_enabled("overseas") is False
    assert wechat_enabled("overseas") is False
