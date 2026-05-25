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


if __name__ == "__main__":
    unittest.main()
