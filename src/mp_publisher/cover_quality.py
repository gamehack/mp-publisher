"""Validate WeChat crop coordinates for the 900×383 cover-pool assets.

The draft API uses fractions, not pixel coordinates. Only the legacy and V2
900×383 pools use these crops; an individually uploaded cover must have the
same aspect ratio before using the stock crop values.
"""
from __future__ import annotations

import re

from .errors import PublisherError

SOURCE_WIDTH = 900
SOURCE_HEIGHT = 383
_COORD = re.compile(r"(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)\Z")


def validate_crop(value: str, target_ratio: float) -> str:
    """Return validated coordinates or fail without echoing user/media data."""
    if not isinstance(value, str):
        raise PublisherError("WeChat cover crop must be a coordinate string")
    parts = value.split("_")
    if len(parts) != 4 or any(_COORD.fullmatch(part) is None for part in parts):
        raise PublisherError("WeChat cover crop coordinates are invalid")
    x1, y1, x2, y2 = (float(part) for part in parts)
    if x1 >= x2 or y1 >= y2:
        raise PublisherError("WeChat cover crop rectangle is empty")
    aspect = (x2 - x1) * SOURCE_WIDTH / ((y2 - y1) * SOURCE_HEIGHT)
    if abs(aspect / target_ratio - 1) > 0.015:
        raise PublisherError("WeChat cover crop aspect ratio is invalid")
    return value
