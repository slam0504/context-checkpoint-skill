import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import checkpoint_common as cc


class TestInputAndConfig(unittest.TestCase):
    def test_read_hook_input_valid(self):
        self.assertEqual(cc.read_hook_input('{"a": 1}'), {"a": 1})

    def test_read_hook_input_empty_or_bad(self):
        self.assertEqual(cc.read_hook_input(""), {})
        self.assertEqual(cc.read_hook_input("not json"), {})
        self.assertEqual(cc.read_hook_input(None), {})

    def test_get_int_default_and_env(self):
        os.environ.pop("CC_TEST_X", None)
        self.assertEqual(cc.get_int("CC_TEST_X", 7), 7)
        os.environ["CC_TEST_X"] = "12"
        self.assertEqual(cc.get_int("CC_TEST_X", 7), 12)
        os.environ["CC_TEST_X"] = "abc"
        self.assertEqual(cc.get_int("CC_TEST_X", 7), 7)
        os.environ.pop("CC_TEST_X", None)

    def test_resolve_project_root_prefers_cwd(self):
        self.assertEqual(cc.resolve_project_root({"cwd": "/tmp/proj"}), "/tmp/proj")

    def test_resolve_project_root_falls_back(self):
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self.assertEqual(cc.resolve_project_root({}), os.getcwd())

    def test_truncate(self):
        self.assertEqual(cc.truncate("hello", 100), "hello")
        out = cc.truncate("x" * 50, 20, marker="!")
        self.assertEqual(len(out), 20)
        self.assertTrue(out.endswith("!"))
        self.assertEqual(cc.truncate(None, 10), "")


if __name__ == "__main__":
    unittest.main()
