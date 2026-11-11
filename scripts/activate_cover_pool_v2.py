#!/usr/bin/env python3
"""Activate an already-uploaded V2 media pool without revealing private IDs.

Only reads the root-owned WeChat media checkpoint and dotenv fragment; it does
not call WeChat. By default this is a dry run. Explicit authorization creates
a private backup then atomically replaces the production dotenv file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
from tempfile import NamedTemporaryFile


POOLS = ("overseas", "x", "producthunt", "trustmrr_weekly", "trustmrr_monthly")
MEDIA_ID = re.compile(r"[A-Za-z0-9_-]{5,256}\Z")
HEX_SHA = re.compile(r"[0-9a-f]{64}\Z")
TARGET_KEYS = frozenset(("WECHAT_COVER_POOL_JSON", "WECHAT_COVER_ROUTING"))
PRODUCTION_ENV = Path("/etc/mp-publisher/publisher.env")


def secure_file(path: Path) -> bytes:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise ValueError("private file must be regular and owner-only")
    return path.read_bytes()


def approved_pool_mapping(state: dict, fragment: bytes, manifest: dict) -> dict:
    if not isinstance(state, dict) or not isinstance(state.get("uploads"), dict):
        raise ValueError("invalid upload checkpoint")
    if not isinstance(state.get("account_fingerprint"), str) or not HEX_SHA.fullmatch(state["account_fingerprint"]):
        raise ValueError("upload checkpoint account identity absent")
    assets = manifest.get("assets")
    if manifest.get("version") != 2 or not isinstance(assets, list) or len(assets) != 15:
        raise ValueError("invalid V2 manifest")
    expected = {key: [] for key in POOLS}
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("profile") not in expected:
            raise ValueError("invalid asset profile")
        ident = asset.get("id")
        record = state["uploads"].get(ident)
        if (not isinstance(record, dict) or record.get("status") != "SUCCESS"
                or record.get("sha256") != asset.get("sha256")
                or not isinstance(record.get("sha256"), str)
                or not HEX_SHA.fullmatch(record["sha256"])
                or not isinstance(record.get("media_id"), str)
                or not MEDIA_ID.fullmatch(record["media_id"])):
            raise ValueError("not all V2 media uploads confirmed")
        expected[asset["profile"]].append(record["media_id"])
    if any(len(values) != 3 for values in expected.values()):
        raise ValueError("incomplete V2 media pools")
    if len({media for values in expected.values() for media in values}) != 15:
        raise ValueError("duplicate V2 material IDs")
    text = fragment.decode("utf-8")
    prefix = "WECHAT_COVER_POOL_JSON='"
    if not text.startswith(prefix) or not text.endswith("'\n") or text.count("\n") != 1:
        raise ValueError("invalid private dotenv fragment")
    configured = json.loads(text[len(prefix):-2])
    if configured != expected:
        raise ValueError("private dotenv fragment disagrees with upload checkpoint")
    return expected


def updated_env(original: bytes, mapping: dict) -> bytes:
    text = original.decode("utf-8")
    lines = []
    for line in text.splitlines():
        key = line.partition("=")[0].strip()
        if key not in TARGET_KEYS:
            lines.append(line)
    lines.append("WECHAT_COVER_POOL_JSON='" + json.dumps(mapping, ensure_ascii=False, separators=(",", ":")) + "'")
    lines.append("WECHAT_COVER_ROUTING=semantic")
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_backup(env_file: Path, original: bytes) -> Path:
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = env_file.with_name(f"{env_file.name}.pre-cover-v2-{suffix}-{os.getpid()}.bak")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(original)
        output.flush()
        os.fsync(output.fileno())
    return target


def replace_private_file(target: Path, data: bytes) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix=".cover-v2-env-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        temporary = None
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely activate the private V2 WeChat cover pool")
    parser.add_argument("--env-file", type=Path, default=PRODUCTION_ENV)
    parser.add_argument("--state", type=Path, default=Path("/var/lib/mp-publisher/cover-media-v2-uploads.json"))
    parser.add_argument("--manifest", type=Path, default=Path("/opt/mp-publisher/assets/cover-pool-v2/manifest.json"))
    parser.add_argument("--authorize-activation", action="store_true")
    args = parser.parse_args()
    if args.authorize_activation and args.env_file == PRODUCTION_ENV and os.geteuid() != 0:
        raise ValueError("production activation requires root")
    original = secure_file(args.env_file)
    state = json.loads(secure_file(args.state))
    fragment = secure_file(args.state.with_name("cover-pool.env.fragment"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    mapping = approved_pool_mapping(state, fragment, manifest)
    new_value = updated_env(original, mapping)
    if new_value == original:
        status = "ALREADY_ACTIVE"
    elif not args.authorize_activation:
        status = "DRY_RUN_OK"
    else:
        write_backup(args.env_file, original)
        replace_private_file(args.env_file, new_value)
        status = "ACTIVATED"
    print(json.dumps({"status": status, "pool_count": len(mapping), "materials": 15,
                      "routing": "semantic", "credentials_printed": False,
                      "wechat_write": False}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError):
        print(json.dumps({"status": "FAILED", "reason": "unsafe or incomplete activation inputs; no secrets shown"}))
        sys.exit(2)
