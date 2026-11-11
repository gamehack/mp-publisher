"""V2 images are a reproducible, uploadable pack, not placeholder posters."""
from pathlib import Path
import fcntl
import hashlib
import json

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1] / "assets" / "cover-pool-v2"


def test_v2_manifest_is_complete_and_has_no_text_overlays():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 2
    assert manifest["image_dimensions"] == {"width": 900, "height": 383}
    assert manifest["crop_235_1"] == "0_0_1_1"
    assert manifest["crop_1_1"] == "0.2872_0_0.7128_1"
    assert manifest["in_image_text"] is False
    assert len(manifest["assets"]) == 15
    assert sorted({a["profile"] for a in manifest["assets"]}) == sorted([
        "overseas", "x", "producthunt", "trustmrr_weekly", "trustmrr_monthly"
    ])


def test_v2_pack_can_pass_offline_upload_preflight():
    from scripts.upload_cover_pool_v2 import checked_pack

    files, pools = checked_pack(ROOT)
    assert len(files) == 15
    assert all(len(v) == 3 for v in pools.values())


def test_v2_upload_script_dry_run_is_read_only(capsys, monkeypatch):
    from scripts.upload_cover_pool_v2 import main

    monkeypatch.setattr("sys.argv", ["upload_cover_pool_v2.py", "--pack", str(ROOT)])
    main()
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "DRY_RUN_OK"
    assert data["wechat_write"] is False


class FakeWeChatClient:
    def __init__(self, token_status=200, allow_upload=False):
        self.token_status = token_status
        self.allow_upload = allow_upload
        self.post_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        return httpx.Response(self.token_status, json={"access_token": "test-token"},
                              request=httpx.Request("GET", "https://api.weixin.qq.com/cgi-bin/token?secret=SECRET_VALUE"))

    def post(self, *_args, **_kwargs):
        self.post_calls += 1
        if self.allow_upload:
            return httpx.Response(200, json={"media_id": f"MATERIAL_ID_{self.post_calls:02d}"})
        raise AssertionError("unexpected upload")


def _authorized(monkeypatch, tmp_path, fake):
    from scripts import upload_cover_pool_v2 as module

    state = tmp_path / "v2-uploads.json"
    monkeypatch.setenv("WECHAT_APP_ID", "APP_ID_1")
    monkeypatch.setenv("WECHAT_APP_SECRET", "SECRET_VALUE")
    monkeypatch.setattr(module.httpx, "Client", lambda **_kw: fake)
    monkeypatch.setattr("sys.argv", [
        "upload_cover_pool_v2.py", "--pack", str(ROOT), "--state", str(state),
        "--authorize-upload",
    ])
    return module, state


def test_token_http_failure_does_not_expose_app_secret(monkeypatch, tmp_path):
    module, _state = _authorized(monkeypatch, tmp_path, FakeWeChatClient(token_status=403))
    with pytest.raises(ValueError, match="token") as err:
        module.main()
    assert "SECRET_VALUE" not in str(err.value)


def test_success_checkpoint_must_match_asset_sha(monkeypatch, tmp_path):
    fake = FakeWeChatClient()
    module, state = _authorized(monkeypatch, tmp_path, fake)
    checkpoint = {
        "account_fingerprint": hashlib.sha256(b"APP_ID_1").hexdigest(),
        "uploads": {"OS2-01": {"status": "SUCCESS", "media_id": "MEDIA_ID_123",
                               "sha256": "not-the-asset-sha"}},
    }
    state.write_text(json.dumps(checkpoint), encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint"):
        module.main()
    assert fake.post_calls == 0


def test_checkpoint_cannot_be_reused_for_another_account(monkeypatch, tmp_path):
    fake = FakeWeChatClient()
    module, state = _authorized(monkeypatch, tmp_path, fake)
    state.write_text(json.dumps({
        "account_fingerprint": hashlib.sha256(b"DIFFERENT_APP_ID").hexdigest(),
        "uploads": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="account"):
        module.main()
    assert fake.post_calls == 0


def test_parallel_uploader_is_rejected_by_exclusive_lock(monkeypatch, tmp_path):
    module, state = _authorized(monkeypatch, tmp_path, FakeWeChatClient())
    lock = state.with_name(state.name + ".lock")
    lock.touch()
    with lock.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="lock"):
            module.main()


def test_authorized_upload_is_checkpointed_and_resume_makes_no_new_posts(monkeypatch, tmp_path, capsys):
    fake = FakeWeChatClient(allow_upload=True)
    module, state = _authorized(monkeypatch, tmp_path, fake)
    module.main()
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "UPLOADS_CONFIRMED"
    assert first["env_activated"] is False
    assert fake.post_calls == 15
    checkpoint = json.loads(state.read_text(encoding="utf-8"))
    assert len(checkpoint["uploads"]) == 15
    assert checkpoint["account_fingerprint"] == hashlib.sha256(b"APP_ID_1").hexdigest()
    fragment = state.with_name("cover-pool.env.fragment")
    assert fragment.exists()
    assert "WECHAT_COVER_POOL_JSON" in fragment.read_text(encoding="utf-8")
    assert state.stat().st_mode & 0o777 == 0o600
    assert fragment.stat().st_mode & 0o777 == 0o600

    module.main()
    second = json.loads(capsys.readouterr().out)
    assert second["status"] == "UPLOADS_CONFIRMED"
    assert fake.post_calls == 15
