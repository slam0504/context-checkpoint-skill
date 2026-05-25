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


class TestCarryForwardAndBuild(unittest.TestCase):
    def test_carry_forward_empty(self):
        out = pc.carry_forward("")
        self.assertEqual(out["Current Goal"], cc.PLACEHOLDER)
        self.assertEqual(out["Next Steps"], cc.PLACEHOLDER)

    def test_carry_forward_preserves(self):
        prev = (
            "# Session Checkpoint\n\n"
            "## Current Goal\nbuild the thing\n\n"
            "## Current State\nhalf done\n\n"
            "## Next Steps\nfinish it\n"
        )
        out = pc.carry_forward(prev)
        self.assertEqual(out["Current Goal"], "build the thing")
        self.assertEqual(out["Current State"], "half done")
        self.assertEqual(out["Next Steps"], "finish it")

    def test_read_remember(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pc.read_remember(d, 100), "(none)")
            os.makedirs(os.path.join(d, ".remember"))
            with open(os.path.join(d, ".remember", "remember.md"), "w") as f:
                f.write("handoff note")
            self.assertEqual(pc.read_remember(d, 100), "handoff note")

    def test_build_checkpoint_has_all_sections(self):
        md = pc.build_checkpoint(
            "auto", "/p", "Branch: main", "**user:** hi", "(none)",
            {"Current Goal": "g", "Current State": "s", "Next Steps": "n"},
        )
        for h in ["## Current Goal", "## Current State", "## Git State",
                  "## Recent Transcript", "## Remembered Notes", "## Next Steps"]:
            self.assertIn(h, md)
        self.assertIn("Trigger: auto", md)


class TestPreCompactMainE2E(unittest.TestCase):
    def test_main_writes_atomic_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            tpath = os.path.join(d, "t.jsonl")
            _write_jsonl(tpath, [{"message": {"role": "user", "content": "do X"}}])
            payload = json.dumps({"cwd": d, "transcript_path": tpath, "trigger": "manual"})
            sh = os.path.join(os.path.dirname(__file__), "..", "hooks", "precompact-checkpoint.sh")
            r = subprocess.run(["bash", sh], input=payload, text=True,
                               capture_output=True)
            self.assertEqual(r.returncode, 0)
            cp = os.path.join(d, ".agent", "session-checkpoint.md")
            self.assertTrue(os.path.exists(cp))
            self.assertFalse(os.path.exists(cp + ".tmp"))
            with open(cp) as f:
                content = f.read()
            self.assertIn("Trigger: manual", content)
            self.assertIn("do X", content)

    def test_main_carries_forward_on_second_run(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".agent"))
            cp = os.path.join(d, ".agent", "session-checkpoint.md")
            with open(cp, "w") as f:
                f.write("# Session Checkpoint\n\n## Current Goal\nKEEPME\n\n"
                        "## Current State\nx\n\n## Next Steps\ny\n")
            tpath = os.path.join(d, "t.jsonl")
            _write_jsonl(tpath, [{"message": {"role": "user", "content": "later"}}])
            payload = json.dumps({"cwd": d, "transcript_path": tpath, "trigger": "auto"})
            sh = os.path.join(os.path.dirname(__file__), "..", "hooks", "precompact-checkpoint.sh")
            subprocess.run(["bash", sh], input=payload, text=True, capture_output=True)
            with open(cp) as f:
                self.assertIn("KEEPME", f.read())


if __name__ == "__main__":
    unittest.main()
