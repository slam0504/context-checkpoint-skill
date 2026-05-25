import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import sessionstart_restore as ss

SAMPLE = (
    "# Session Checkpoint\n"
    "Updated: 2026-05-25T10:00:00 | Trigger: auto | Project: /p\n\n"
    "## Current Goal\ng\n\n"
    "## Current State\ns\n\n"
    "## Git State\nBranch: main\n\n"
    "## Recent Transcript\n" + ("LINE\n" * 500) + "\n"
    "## Remembered Notes\n(none)\n\n"
    "## Next Steps\nn\n"
)


class TestRestorePayload(unittest.TestCase):
    def test_pointer(self):
        out = ss.build_pointer(SAMPLE)
        self.assertIn(".agent/session-checkpoint.md", out)
        self.assertIn("2026-05-25T10:00:00", out)

    def test_full_when_small(self):
        small = "# Session Checkpoint\nUpdated: x\n\n## Git State\nok\n"
        out = ss.build_restore_payload(small, 6000)
        self.assertTrue(out.startswith(ss.INSTRUCTION))
        self.assertIn("## Git State", out)

    def test_capped_when_large(self):
        out = ss.build_restore_payload(SAMPLE, 1500)
        self.assertLessEqual(len(out), 1500)
        self.assertIn("## Current Goal", out)
        self.assertIn("## Next Steps", out)
        self.assertIn("truncated — full checkpoint", out)


import json
import subprocess
import tempfile


def _run_restore(payload):
    sh = os.path.join(os.path.dirname(__file__), "..", "hooks", "sessionstart-restore.sh")
    return subprocess.run(["bash", sh], input=payload, text=True, capture_output=True)


class TestMainE2E(unittest.TestCase):
    def test_no_checkpoint_no_output(self):
        with tempfile.TemporaryDirectory() as d:
            r = _run_restore(json.dumps({"cwd": d, "source": "resume"}))
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), "")

    def test_startup_emits_pointer(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".agent"))
            with open(os.path.join(d, ".agent", "session-checkpoint.md"), "w") as f:
                f.write(SAMPLE)
            r = _run_restore(json.dumps({"cwd": d, "source": "startup"}))
            obj = json.loads(r.stdout)
            ctx = obj["hookSpecificOutput"]["additionalContext"]
            self.assertIn(".agent/session-checkpoint.md", ctx)
            self.assertNotIn("## Git State", ctx)

    def test_compact_emits_full_restore(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".agent"))
            with open(os.path.join(d, ".agent", "session-checkpoint.md"), "w") as f:
                f.write(SAMPLE)
            r = _run_restore(json.dumps({"cwd": d, "source": "compact"}))
            obj = json.loads(r.stdout)
            ctx = obj["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(obj["hookSpecificOutput"]["hookEventName"], "SessionStart")
            self.assertTrue(ctx.startswith(ss.INSTRUCTION))
            self.assertIn("## Git State", ctx)


if __name__ == "__main__":
    unittest.main()
