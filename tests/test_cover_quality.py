"""A bad crop must never be sent to WeChat's draft/add endpoint."""
import pytest

from mp_publisher.channels import wechat_payload
from mp_publisher.covers import CROP_SQUARE, CROP_WIDE
from mp_publisher.errors import PublisherError
from mp_publisher.models import Article


def article():
    return Article("overseas", "04", "overseas-stories:04", "page", "x", "Story",
                   "summary", "body", "<p>body</p>", "a" * 64)


def test_wide_and_square_crops_are_valid():
    payload = wechat_payload(article(), "cover", crop_wide=CROP_WIDE, crop_square=CROP_SQUARE)
    assert payload["articles"][0]["pic_crop_1_1"] == CROP_SQUARE


@pytest.mark.parametrize("crop", [
    "0_0_0_1", "0_0_1_0", "0_0_2_1", "-0.1_0_1_1",
    "abc_0_1_1", "0_0_1", "NaN_0_1_1", "0_0_1_inf",
    "0_0_0.5_1",
])
def test_rejects_bad_wide_crop_before_submission(crop):
    with pytest.raises(PublisherError, match="crop"):
        wechat_payload(article(), "cover", crop_wide=crop)


def test_rejects_wrong_square_aspect():
    with pytest.raises(PublisherError, match="crop"):
        wechat_payload(article(), "cover", crop_square="0_0_1_1")
