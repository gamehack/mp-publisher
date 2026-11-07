import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate-env.py"
SPEC = importlib.util.spec_from_file_location("mp_env_migration", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_migration_filters_cms_secrets_and_old_state_path(tmp_path):
    src = tmp_path / "old.env"
    dest = tmp_path / "publisher.env"
    src.write_text("\n".join([
        "NOTION_API_KEY=test-notion",
        "WECHAT_APP_ID=test-id",
        "WECHAT_APP_SECRET=test-secret",
        "WECHAT_THUMB_MEDIA_ID_X=x",
        "WECHAT_THUMB_MEDIA_ID_PRODUCTHUNT=ph",
        "WECHAT_THUMB_MEDIA_ID_TRUSTMRR=trust",
        "WECHAT_ENABLE_DRAFTS=true",
        "PUBLISH_X_ENABLED=true",
        "WECHAT_X_ENABLED=true",
        "CMS_INGEST_TOKEN=must-not-copy",
        "CMS_ENABLE_DRAFTS=true",
        "QJAI_PUBLISHER_STATE=/var/lib/qjai-text-publisher/state.sqlite",
    ]), encoding="utf-8")
    names = MODULE.migrate(src, dest)
    data = dest.read_text(encoding="utf-8")
    assert "WECHAT_X_ENABLED" in names
    assert "test-secret" in data
    assert "CMS" not in data
    assert "must-not-copy" not in data
    assert "qjai-text-publisher" not in data
    assert dest.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="already exists"):
        MODULE.migrate(src, dest)


def test_migration_rejects_missing_mandatory_settings(tmp_path):
    src = tmp_path / "in.env"
    src.write_text("NOTION_API_KEY=only-notion\n")
    with pytest.raises(ValueError, match="Missing mandatory"):
        MODULE.migrate(src, tmp_path / "out.env")
