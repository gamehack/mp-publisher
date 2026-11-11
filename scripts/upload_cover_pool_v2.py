#!/usr/bin/env python3
"""One-way, checkpointed WeChat V2 permanent cover upload. Never creates drafts.

Usage (dry run is default):
  python upload_cover_pool_v2.py --pack /srv/qjai-covers-v2

After copying the cover pack to the allowlisted Silicon Valley host and
confirming permissions, upload only with explicit authorization:
  python upload_cover_pool_v2.py --pack /srv/qjai-covers-v2 \
    --state /var/lib/mp-publisher/cover-media-v2-uploads.json \
    --authorize-upload

Reads WECHAT_APP_ID and WECHAT_APP_SECRET from inherited environment. Never
prints credentials, raw media IDs, requests, or response bodies. Partial or
ambiguous uploads halt without retry. Use the private state checkpoint for
manual reconciliation. No material delete, draft add, draft update, or publish.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

import httpx

TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
UPLOAD_URL = "https://api.weixin.qq.com/cgi-bin/material/add_material"
VALID_POOLS = ("overseas", "x", "producthunt", "trustmrr_weekly", "trustmrr_monthly")
ID_PREFIXES = {"overseas": "OS2", "x": "XI2", "producthunt": "PH2",
               "trustmrr_weekly": "TW2", "trustmrr_monthly": "TM2"}


def jpeg_dimensions(content: bytes) -> tuple[int, int]:
    """Read JPEG SOF dimensions using only the standard library."""
    if not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("invalid JPEG")
    offset = 2
    while offset < len(content):
        if content[offset] != 0xff:
            raise ValueError("invalid JPEG marker")
        while offset < len(content) and content[offset] == 0xff:
            offset += 1
        if offset >= len(content):
            break
        marker = content[offset]
        offset += 1
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(content):
            break
        segment_len = int.from_bytes(content[offset:offset + 2], "big")
        if segment_len < 2 or offset + segment_len > len(content):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if segment_len < 7:
                break
            height = int.from_bytes(content[offset + 3:offset + 5], "big")
            width = int.from_bytes(content[offset + 5:offset + 7], "big")
            return width, height
        offset += segment_len
    raise ValueError("JPEG dimensions missing")


def checked_pack(root: Path):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("pack must be a real directory")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("symlink manifest rejected")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (data.get("version") != 2 or len(data.get("assets", [])) != 15 or
            data.get("image_dimensions") != {"width": 900, "height": 383} or
            data.get("in_image_text") is not False):
        raise ValueError("pack must contain exactly 15 V2 assets")
    seen = set()
    files = []
    pools = {key: [] for key in VALID_POOLS}
    for item in data["assets"]:
        key = item["profile"]
        ident = item["id"]
        if (key not in pools or ident in seen or
                re.fullmatch(re.escape(ID_PREFIXES.get(key, "?")) + r"-0[123]", ident) is None):
            raise ValueError("invalid pool or duplicate asset")
        seen.add(ident)
        rel = item["cover_file"]
        if rel != f"covers/{ident}.jpg":
            raise ValueError("untrusted file name")
        path = root / rel
        if path.is_symlink() or not path.is_file():
            raise ValueError("symlink or missing file")
        content = path.read_bytes()
        if not (content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")
                and 128 <= len(content) <= 64 * 1024
                and hashlib.sha256(content).hexdigest() == item["sha256"]):
            raise ValueError("invalid JPEG file or checksum")
        if jpeg_dimensions(content) != (900, 383):
            raise ValueError("invalid cover dimensions")
        files.append((ident, key, path))
        pools[key].append(ident)
    if any(len(v) != 3 for v in pools.values()):
        raise ValueError("each pool must have exactly 3 images")
    return files, pools


def _save_secure(path: Path, state: dict) -> None:
    if path.is_symlink():
        raise ValueError("state symlink rejected")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", prefix=".cover-media-", suffix=".tmp",
                            dir=path.parent, encoding="utf-8", delete=False) as f:
        tmp = Path(f.name)
        os.chmod(tmp, 0o600)
        json.dump(state, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextmanager
def exclusive_upload_lock(state: Path):
    """Protect the whole checkpoint read/reserve/upload/fragment transaction."""
    state.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = state.with_name(state.name + ".lock")
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        raise ValueError("upload lock could not be opened") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("upload lock is already held") from None
        yield
    finally:
        os.close(fd)


def _upload_authorized(args, files, pools):
    app_id, app_secret = os.environ.get("WECHAT_APP_ID",""), os.environ.get("WECHAT_APP_SECRET","")
    if not app_id or not app_secret:
        raise ValueError("WeChat account credentials not available")
    if args.state.is_symlink():
        raise ValueError("state symlink rejected")
    existing = json.loads(args.state.read_text()) if args.state.exists() else {"uploads": {}}
    if not isinstance(existing, dict) or not isinstance(existing.get("uploads"), dict):
        raise ValueError("invalid checkpoint")
    fingerprint = hashlib.sha256(app_id.encode("utf-8")).hexdigest()
    recorded_fingerprint = existing.get("account_fingerprint")
    if recorded_fingerprint is not None and recorded_fingerprint != fingerprint:
        raise ValueError("checkpoint belongs to a different WeChat account")
    if recorded_fingerprint is None and existing["uploads"]:
        raise ValueError("checkpoint account identity is missing")
    existing["account_fingerprint"] = fingerprint
    # Check every previous record *before* any network request or new write.
    for ident, _key, path in files:
        previous = existing["uploads"].get(ident)
        if previous is None:
            continue
        if not isinstance(previous, dict) or previous.get("status") != "SUCCESS":
            raise ValueError("checkpoint has an ambiguous upload requiring reconciliation")
        if (previous.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest() or
                not isinstance(previous.get("media_id"), str) or
                re.fullmatch(r"[A-Za-z0-9_-]{5,256}", previous["media_id"]) is None):
            raise ValueError("checkpoint does not match current cover asset")
    done = 0
    with httpx.Client(timeout=45, follow_redirects=False) as client:
        try:
            response = client.get(TOKEN_URL, params={"grant_type":"client_credential", "appid":app_id,"secret":app_secret})
            if response.status_code != 200 or len(response.content) > 8192:
                raise ValueError("WeChat token request failed")
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("WeChat token request failed") from None
        if not isinstance(payload, dict):
            raise ValueError("WeChat token request failed")
        token = payload.get("access_token")
        if not token or payload.get("errcode"):
            raise ValueError("WeChat token request failed")
        for ident, key, path in files:
            previous = existing["uploads"].get(ident)
            if previous is not None:
                if previous.get("status") == "SUCCESS":
                    done += 1
                    continue
                raise ValueError(f"{ident}: ambiguous previous upload requires manual reconciliation")
            # Mark UNKNOWN *before* network write: a crash or timeout cannot
            # cause accidental duplicate material upload on the next run.
            existing["uploads"][ident] = {"status":"UNKNOWN","sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
            _save_secure(args.state,existing)
            try:
                with path.open("rb") as fp:
                    resp = client.post(UPLOAD_URL, params={"access_token":token,"type":"image"},
                                       files={"media":(path.name,fp,"image/jpeg")})
                if resp.status_code != 200 or len(resp.content)>8192:
                    raise ValueError("WeChat material endpoint did not acknowledge upload")
                data = resp.json()
                media_id=data.get("media_id")
                if data.get("errcode",0) != 0 or not isinstance(media_id,str) or not re.fullmatch(r"[A-Za-z0-9_-]{5,256}",media_id):
                    raise ValueError("WeChat material upload lacked confirmed permanent media_id")
                existing["uploads"][ident] = {"status":"SUCCESS", "media_id":data["media_id"],
                                              "sha256":existing["uploads"][ident]["sha256"]}
                _save_secure(args.state,existing)
                done += 1
            except Exception:
                # Never replay a write with an unknown outcome.
                print(json.dumps({"status":"BLOCKED_UNKNOWN","asset":ident,"confirmed":done,
                                  "manual_reconciliation_required":True},ensure_ascii=False))
                raise SystemExit(2)
    if done != 15:
        raise ValueError("missing confirmed media IDs")
    env_mapping={key:[existing["uploads"][id]["media_id"] for id in keys] for key,keys in pools.items()}
    fragment=args.state.with_name("cover-pool.env.fragment")
    # A valid dotenv fragment, written to root-only temporary file atomically.
    json_value=json.dumps(env_mapping,ensure_ascii=False,separators=(',',':'))
    if fragment.is_symlink():
        raise ValueError("fragment symlink rejected")
    with NamedTemporaryFile(mode="w", prefix=".pool-env-", suffix=".tmp",
                            dir=fragment.parent, encoding="utf-8", delete=False) as f:
        tmp=Path(f.name)
        os.chmod(tmp,0o600)
        f.write("WECHAT_COVER_POOL_JSON='"+json_value+"'\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,fragment)
    print(json.dumps({"status":"UPLOADS_CONFIRMED","materials":done,"pool_count":len(pools),
                     "env_fragment_written":True,"env_activated":False,"wechat_draft_requests":0},ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="WeChat V2 cover uploader (dry-run by default)")
    ap.add_argument("--pack", type=Path, required=True)
    ap.add_argument("--state", type=Path, default=Path("/var/lib/mp-publisher/cover-media-v2-uploads.json"))
    ap.add_argument("--authorize-upload", action="store_true")
    args = ap.parse_args()
    files, pools = checked_pack(args.pack)
    if not args.authorize_upload:
        print(json.dumps({"status":"DRY_RUN_OK","image_count":len(files),"pools":list(pools),"wechat_write":False},ensure_ascii=False))
        return
    with exclusive_upload_lock(args.state):
        _upload_authorized(args, files, pools)


if __name__ == "__main__":
    try: main()
    except (OSError,ValueError,KeyError,json.JSONDecodeError):
        print(json.dumps({"status":"FAILED","reason":"invalid pack, missing permission or unsafe state; see operator checklist","wechat_draft_requests":0}))
        sys.exit(2)
