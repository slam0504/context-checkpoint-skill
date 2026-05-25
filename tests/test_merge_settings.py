import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import merge_settings as ms


class TestMerge(unittest.TestCase):
    def test_preserves_existing_keys_and_hooks(self):
        existing = {
            "enabledPlugins": {"x": True},
            "effortLevel": "high",
            "hooks": {"SessionStart": [
                {"hooks": [{"type": "command", "command": "bash other.sh"}]}
            ]},
        }
        out = ms.merge(existing, "/h")
        self.assertEqual(out["enabledPlugins"], {"x": True})
        self.assertEqual(out["effortLevel"], "high")
        cmds = [h["command"] for e in out["hooks"]["SessionStart"] for h in e["hooks"]]
        self.assertIn("bash other.sh", cmds)
        self.assertTrue(any("sessionstart-restore.sh" in c for c in cmds))
        self.assertIn("PreCompact", out["hooks"])

    def test_idempotent(self):
        out1 = ms.merge({}, "/h")
        out2 = ms.merge(json.loads(json.dumps(out1)), "/h")
        self.assertEqual(
            len(out2["hooks"]["PreCompact"]), len(out1["hooks"]["PreCompact"])
        )
        self.assertEqual(len(out2["hooks"]["PreCompact"]), 1)

    def test_main_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"effortLevel": "high"}, f)
            sys.argv = ["merge_settings.py", path, "/h"]
            ms.main()
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["effortLevel"], "high")
            self.assertIn("PreCompact", data["hooks"])
            self.assertFalse(os.path.exists(path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
