import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import precompact_checkpoint as pc
import checkpoint_common as cc


class TestGitState(unittest.TestCase):
    def test_non_git_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pc.build_git_state(d), "(not a git repository)")

    def test_git_dir(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init"], cwd=d, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, capture_output=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=d, capture_output=True)
            open(os.path.join(d, "a.txt"), "w").close()
            subprocess.run(["git", "add", "a.txt"], cwd=d, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True)
            out = pc.build_git_state(d)
            self.assertIn("Branch:", out)
            self.assertIn("Recent commits:", out)


import json


def _write_jsonl(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class TestTranscript(unittest.TestCase):
    def test_missing_path(self):
        self.assertEqual(
            pc.extract_recent_transcript("/no/such", 5, 100, 1000),
            "(transcript unavailable)",
        )

    def test_extract_roles_and_markers(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.jsonl")
            _write_jsonl(p, [
                {"type": "summary", "summary": "ignore me"},
                {"message": {"role": "user", "content": "hello there"}},
                {"message": {"role": "assistant", "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "name": "Bash"},
                    {"type": "tool_result", "content": "x" * 500},
                ]}},
            ])
            out = pc.extract_recent_transcript(p, 5, 100, 1000)
            self.assertIn("**user:** hello there", out)
            self.assertIn("**assistant:**", out)
            self.assertIn("[tool_use: Bash]", out)
            self.assertIn("[tool_result:", out)
            self.assertNotIn("ignore me", out)

    def test_last_n_and_total_cap(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.jsonl")
            rows = [{"message": {"role": "user", "content": f"msg{i}"}} for i in range(20)]
            _write_jsonl(p, rows)
            out = pc.extract_recent_transcript(p, 3, 100, 1000)
            self.assertIn("msg19", out)
            self.assertNotIn("msg10", out)


if __name__ == "__main__":
    unittest.main()
