from __future__ import annotations

import json
from pathlib import Path

from .errors import PublisherError
from .profiles import get_profile, source_key
from .state import StateStore


def import_file(store: StateStore, path: str | Path) -> int:
    target = Path(path)
    if target.is_symlink() or not target.is_file() or target.stat().st_size > 256_000:
        raise PublisherError("Legacy import must be a bounded regular JSON file")
    try:
        rows = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError):
        raise PublisherError("Invalid legacy import JSON") from None
    if not isinstance(rows, list):
        raise PublisherError("Legacy import JSON must be an array")
    count = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"origin", "legacy_key", "profile", "period", "channel", "state"}:
            raise PublisherError("Legacy import row has unexpected fields")
        profile = get_profile(str(row["profile"]))
        period = str(row["period"])
        key = source_key(profile, period)
        store.import_delivery(
            origin=str(row["origin"]), legacy_key=str(row["legacy_key"]), source_key=key,
            profile=profile.name, period=period, channel=str(row["channel"]), state=str(row["state"]),
        )
        count += 1
    return count
