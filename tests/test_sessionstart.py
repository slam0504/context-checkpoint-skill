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


if __name__ == "__main__":
    unittest.main()
