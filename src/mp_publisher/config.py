from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .errors import PublisherError


TRUE = {"1", "true", "yes"}
FALSE = {"0", "false", "no", ""}


def load_environment(path: str | Path | None = None) -> None:
    if path:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise PublisherError("Environment file must be a regular file")
        load_dotenv(target, override=False)


def enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw in TRUE:
        return True
    if raw in FALSE:
        return False
    raise PublisherError(f"{name} must be true or false")


def required(name: str, *, minimum: int = 1) -> str:
    value = os.getenv(name, "").strip()
    if len(value) < minimum:
        raise PublisherError(f"{name} is missing or invalid")
    return value


def state_path() -> Path:
    return Path(os.getenv("QJAI_PUBLISHER_STATE", "/data/state.sqlite"))


def profile_enabled(profile: str) -> bool:
    return enabled("PUBLISH_" + profile.upper() + "_ENABLED")


def wechat_enabled(profile: str) -> bool:
    return enabled("WECHAT_ENABLE_DRAFTS") and enabled(f"WECHAT_{profile.upper()}_ENABLED")


def cover_media_id(profile: str) -> str:
    return required("WECHAT_THUMB_MEDIA_ID_" + profile.upper())
