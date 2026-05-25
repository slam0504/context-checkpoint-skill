import glob
import json
import os
import subprocess
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")


class TestInstall(unittest.TestCase):
    def test_backs_up_settings_before_merge(self):
        with tempfile.TemporaryDirectory() as home:
            claude = os.path.join(home, ".claude")
            os.makedirs(claude)
            settings = os.path.join(claude, "settings.json")
            with open(settings, "w") as f:
                json.dump({"effortLevel": "high"}, f)
            env = dict(os.environ, HOME=home)
            r = subprocess.run(["bash", os.path.join(REPO, "install.sh")],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            backups = glob.glob(settings + ".bak.*")
            self.assertTrue(backups, "installer must back up settings.json before merging")
            # backup preserves the pre-merge content
            with open(backups[0]) as f:
                self.assertEqual(json.load(f), {"effortLevel": "high"})
            # merged result keeps the key and adds hooks
            with open(settings) as f:
                merged = json.load(f)
            self.assertEqual(merged["effortLevel"], "high")
            self.assertIn("PreCompact", merged["hooks"])
            # hook files were copied
            self.assertTrue(os.path.exists(os.path.join(claude, "hooks", "precompact-checkpoint.sh")))

    def test_no_backup_when_no_settings(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home)
            r = subprocess.run(["bash", os.path.join(REPO, "install.sh")],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            # fresh settings.json created, no spurious backup
            self.assertTrue(os.path.exists(os.path.join(home, ".claude", "settings.json")))
            self.assertFalse(glob.glob(os.path.join(home, ".claude", "settings.json.bak.*")))


if __name__ == "__main__":
    unittest.main()
