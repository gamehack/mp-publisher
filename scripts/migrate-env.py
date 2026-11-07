#!/usr/bin/env python3
"""Copy only the WeChat/Notion settings from a legacy private .env.

Never print values or copy CMS credentials. Run under sudo on the production host.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import tempfile


KEYS = (
    "NOTION_API_KEY", "WECHAT_APP_ID", "WECHAT_APP_SECRET",
    "WECHAT_THUMB_MEDIA_ID_X", "WECHAT_THUMB_MEDIA_ID_PRODUCTHUNT",
    "WECHAT_THUMB_MEDIA_ID_TRUSTMRR", "WECHAT_ENABLE_DRAFTS",
    "PUBLISH_X_ENABLED", "PUBLISH_PRODUCTHUNT_ENABLED", "PUBLISH_TRUSTMRR_ENABLED",
    "WECHAT_X_ENABLED", "WECHAT_PRODUCTHUNT_ENABLED", "WECHAT_TRUSTMRR_ENABLED",
)
REQUIRED = set(KEYS[:6])
LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def migrate(source: Path, target: Path) -> list[str]:
    if source.is_symlink() or not source.is_file():
        raise ValueError("Source must be a regular private environment file")
    if target.is_symlink() or target.exists():
        raise ValueError("Destination already exists; refusing overwrite")
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise ValueError("Destination directory must already exist and not be a symlink")
    entries: dict[str, str] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        match = LINE.fullmatch(line.strip())
        if match and match.group(1) in KEYS:
            key = match.group(1)
            if key in entries:
                raise ValueError("Duplicate environment key: " + key)
            if "\x00" in match.group(2):
                raise ValueError("Invalid environment value")
            entries[key] = f"{key}={match.group(2)}"
    if REQUIRED - entries.keys():
        raise ValueError("Missing mandatory environment key(s): " + ", ".join(sorted(REQUIRED - entries.keys())))
    fd, temp = tempfile.mkstemp(prefix=".mp-publisher-", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("# Private WeChat-only settings; generated from the legacy publisher.\n")
            for key in KEYS:
                if key in entries:
                    stream.write(entries[key] + "\n")
        os.replace(temp, target)
        os.chmod(target, 0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return [key for key in KEYS if key in entries]


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely extract legacy WeChat-only environment variables")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args()
    names = migrate(args.source, args.dest)
    print(f"Copied {len(names)} allowlisted variable names; no values printed")


if __name__ == "__main__":
    main()
