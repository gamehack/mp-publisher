"""Black-box tests for safely activating private WeChat V2 cover IDs."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "activate_cover_pool_v2.py"
MANIFEST = ROOT / "assets" / "cover-pool-v2" / "manifest.json"
POOL_NAMES = ("overseas", "x", "producthunt", "trustmrr_weekly", "trustmrr_monthly")


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "cover-media-v2-uploads.json"
        self.fragment = self.root / "cover-pool.env.fragment"
        self.env_file = self.root / "publisher.env"
        rows = json.loads(MANIFEST.read_text(encoding="utf-8"))["assets"]
        self.pools = {name: [] for name in POOL_NAMES}
        records = {}
        for row in rows:
            media_id = f"TEST_MEDIA_{row['id'].replace('-', '_')}"
            self.pools[row["profile"]].append(media_id)
            records[row["id"]] = {"status": "SUCCESS", "sha256": row["sha256"], "media_id": media_id}
        self.state.write_text(json.dumps({"account_fingerprint": "a" * 64, "uploads": records}), encoding="utf-8")
        self.fragment.write_text("WECHAT_COVER_POOL_JSON='" + json.dumps(self.pools, separators=(",", ":")) + "'\n", encoding="utf-8")
        os.chmod(self.state, 0o600)
        os.chmod(self.fragment, 0o600)
        self.env_file.write_text(
            "# user config\nWECHAT_COVER_POOL_JSON='legacy'\nWECHAT_COVER_ROUTING=hash\n"
            "WECHAT_APP_SECRET=private-secret\nOTHER_SETTING=keep-this\n",
            encoding="utf-8",
        )
        os.chmod(self.env_file, 0o600)

    def run_script(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--env-file", str(self.env_file),
             "--state", str(self.state), "--manifest", str(MANIFEST), *extra],
            capture_output=True, text=True, check=False,
        )

    def test_dry_run_does_not_change_secret_file(self):
        before = self.env_file.read_bytes()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "DRY_RUN_OK")
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.bak")), [])
        self.assertNotIn("private-secret", proc.stdout + proc.stderr)

    def test_authorized_activation_preserves_other_values_and_creates_backup(self):
        original = self.env_file.read_bytes()
        proc = self.run_script("--authorize-activation")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "ACTIVATED")
        content = self.env_file.read_text(encoding="utf-8")
        self.assertIn("WECHAT_APP_SECRET=private-secret", content)
        self.assertIn("OTHER_SETTING=keep-this", content)
        self.assertEqual(content.count("WECHAT_COVER_POOL_JSON="), 1)
        self.assertEqual(content.count("WECHAT_COVER_ROUTING="), 1)
        self.assertIn("WECHAT_COVER_ROUTING=semantic", content)
        self.assertNotIn("legacy", content)
        backups = list(self.root.glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.env_file.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private-secret", proc.stdout + proc.stderr)
        self.assertNotIn("TEST_MEDIA", proc.stdout + proc.stderr)
        second = self.run_script("--authorize-activation")
        self.assertEqual(json.loads(second.stdout)["status"], "ALREADY_ACTIVE")
        self.assertEqual(len(list(self.root.glob("*.bak"))), 1)

    def test_mismatched_fragment_rejected_without_changes(self):
        before = self.env_file.read_bytes()
        self.fragment.write_text("WECHAT_COVER_POOL_JSON='{}'\n", encoding="utf-8")
        proc = self.run_script("--authorize-activation")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["status"], "FAILED")
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.bak")), [])

    def test_incomplete_checkpoint_rejected_without_changes(self):
        data = json.loads(self.state.read_text(encoding="utf-8"))
        data["uploads"]["OS2-01"]["status"] = "UNKNOWN"
        self.state.write_text(json.dumps(data), encoding="utf-8")
        before = self.env_file.read_bytes()
        proc = self.run_script("--authorize-activation")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_world_readable_env_rejected(self):
        os.chmod(self.env_file, 0o644)
        proc = self.run_script("--authorize-activation")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
