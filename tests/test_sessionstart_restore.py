"""Tests for the SessionStart hook: when a checkpoint is injected, when only a
pointer is, and how an oversized checkpoint is cut down."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
sys.path.insert(0, HOOKS)
import checkpoint_common as cc  # noqa: E402
import sessionstart_restore as sd  # noqa: E402

NOTE_SENTENCE = (
    "Remembered Notes is a log shared by every session started in this project "
    "directory, so some entries may come from other sessions."
)
TAGGABLE_SESSION = "394e2224-8316-46e4-abd0-ba88f10a481d"
TAGGED_NOTE_SENTENCE = (
    "Remembered Notes is a log shared by every session started in this project "
    "directory. A heading ending in 394e2224 marks an entry from this session; "
    "any other entry may come from another session."
)


def checkpoint_md(session="sess-1", age=timedelta(minutes=1), notes="NOTE-X",
                  git="Branch: main", transcript="**user:** hello there"):
    ts = (datetime.now() - age).isoformat(timespec="seconds")
    session_part = f"Session: {session} | " if session is not None else ""
    return (
        "# Session Checkpoint\n"
        f"Updated: {ts} | Trigger: auto | {session_part}Project: /p\n\n"
        f"## Git State\n{git}\n\n"
        f"## Recent Transcript\n{transcript}\n\n"
        f"## Remembered Notes\n{notes}\n"
    )


def write_checkpoint(root, md):
    path = os.path.join(root, ".agent", "session-checkpoint.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(md)
    return path


def run_hook(payload, home, project_dir, **extra_env):
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("CC_") and k not in ("CLAUDE_PROJECT_DIR", "CONTEXT_CHECKPOINT_STRICT")
    }
    env.update(HOME=home, CLAUDE_PROJECT_DIR=project_dir)
    env.update(extra_env)
    result = subprocess.run(
        ["bash", os.path.join(HOOKS, "sessionstart-restore.sh")],
        input=json.dumps(payload), text=True, capture_output=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    with open(os.path.join(home, ".claude", "hooks", ".wrapper-error.log")) as f:
        assert f.read() == "", "hook crashed behind the wrapper"
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


class TestWhatGetsInjected(unittest.TestCase):
    def setUp(self):
        self.root_ctx = tempfile.TemporaryDirectory()
        self.home_ctx = tempfile.TemporaryDirectory()
        self.root = self.root_ctx.name
        self.home = self.home_ctx.name
        self.addCleanup(self.root_ctx.cleanup)
        self.addCleanup(self.home_ctx.cleanup)

    def run_source(self, source, session_id="sess-1", cwd=None, **extra_env):
        payload = {"cwd": cwd or self.root, "source": source}
        if session_id is not None:
            payload["session_id"] = session_id
        return run_hook(payload, self.home, self.root, **extra_env)

    def test_compact_in_the_same_fresh_session_injects_the_whole_checkpoint(self):
        write_checkpoint(self.root, checkpoint_md())
        ctx = self.run_source("compact")
        self.assertIn("NOTE-X", ctx)
        self.assertIn("hello there", ctx)
        self.assertTrue(ctx.startswith(sd.INSTRUCTION))

    def test_restored_notes_name_the_heading_tag_of_the_restoring_session(self):
        write_checkpoint(self.root, checkpoint_md(session=TAGGABLE_SESSION))
        ctx = self.run_source("compact", session_id=TAGGABLE_SESSION)
        self.assertTrue(ctx.startswith(
            sd.INSTRUCTION + TAGGED_NOTE_SENTENCE + "\n\n# Session Checkpoint"))

    def test_resume_and_clear_from_another_session_only_get_a_pointer(self):
        path = write_checkpoint(self.root, checkpoint_md(session="someone-else"))
        for source in ("resume", "clear"):
            with self.subTest(source=source):
                ctx = self.run_source(source)
                self.assertEqual(len(ctx.strip().splitlines()), 1)
                self.assertIn(path, ctx)
                self.assertIn("another session", ctx)
                self.assertNotIn("NOTE-X", ctx)
                self.assertNotIn("hello there", ctx)

    def test_compact_from_another_session_is_not_injected_either(self):
        write_checkpoint(self.root, checkpoint_md(session="someone-else"))
        ctx = self.run_source("compact")
        self.assertNotIn("NOTE-X", ctx)
        self.assertIn("another session", ctx)

    def test_resume_of_the_same_session_past_the_restore_age_gets_a_pointer(self):
        write_checkpoint(self.root, checkpoint_md(age=timedelta(hours=30)))
        ctx = self.run_source("resume")
        self.assertNotIn("NOTE-X", ctx)
        self.assertIn("this session", ctx)
        self.assertIn("30 h ago", ctx)

    def test_restore_age_limit_is_configurable(self):
        write_checkpoint(self.root, checkpoint_md(age=timedelta(hours=30)))
        ctx = self.run_source("resume", CC_RESTORE_MAX_AGE_HOURS="48")
        self.assertIn("NOTE-X", ctx)

    def test_startup_past_the_pointer_age_prints_nothing(self):
        write_checkpoint(self.root, checkpoint_md(age=timedelta(hours=200)))
        self.assertIsNone(self.run_source("startup"))

    def test_pointer_age_limit_is_configurable(self):
        write_checkpoint(self.root, checkpoint_md(age=timedelta(hours=200)))
        self.assertIsNotNone(self.run_source("startup", CC_POINTER_MAX_AGE_HOURS="300"))

    def test_startup_with_a_recent_checkpoint_gets_a_one_line_pointer_with_its_age(self):
        path = write_checkpoint(self.root, checkpoint_md(age=timedelta(hours=2, minutes=5)))
        ctx = self.run_source("startup")
        self.assertEqual(len(ctx.strip().splitlines()), 1)
        self.assertIn(path, ctx)
        self.assertIn("2 h ago", ctx)
        self.assertIn("updated 20", ctx)
        self.assertNotIn("NOTE-X", ctx)

    def test_legacy_checkpoint_without_a_session_is_never_injected_in_full(self):
        write_checkpoint(self.root, checkpoint_md(session=None))
        for source in ("resume", "clear", "compact"):
            with self.subTest(source=source):
                ctx = self.run_source(source)
                self.assertNotIn("NOTE-X", ctx)
                self.assertIn("another session", ctx)

    def test_hook_input_without_a_session_id_is_never_the_same_session(self):
        write_checkpoint(self.root, checkpoint_md(session="unknown"))
        ctx = self.run_source("resume", session_id=None)
        self.assertNotIn("NOTE-X", ctx)
        self.assertIn("another session", ctx)

    def test_fork_and_unknown_sources_print_nothing(self):
        write_checkpoint(self.root, checkpoint_md())
        for source in ("fork", "", "other"):
            with self.subTest(source=source):
                self.assertIsNone(self.run_source(source))

    def test_no_checkpoint_prints_nothing(self):
        self.assertIsNone(self.run_source("compact"))

    def test_checkpoint_is_read_from_the_project_dir_when_cwd_is_a_subdirectory(self):
        write_checkpoint(self.root, checkpoint_md())
        sub = os.path.join(self.root, "pkg", "inner")
        os.makedirs(sub)
        self.assertIn("NOTE-X", self.run_source("compact", cwd=sub))

    def test_checkpoint_under_the_cwd_is_ignored_when_the_project_dir_differs(self):
        sub = os.path.join(self.root, "pkg")
        os.makedirs(sub)
        write_checkpoint(sub, checkpoint_md())
        self.assertIsNone(self.run_source("compact", cwd=sub))

    def test_unparseable_header_time_falls_back_to_file_mtime(self):
        md = checkpoint_md().replace("Updated: ", "Updated: not-a-time #", 1)
        path = write_checkpoint(self.root, md)
        old = (datetime.now() - timedelta(hours=100)).timestamp()
        os.utime(path, (old, old))
        ctx = self.run_source("resume")
        self.assertNotIn("NOTE-X", ctx)
        self.assertIn("4 d ago", ctx)

    def test_checkpoint_dated_in_the_future_counts_as_fresh(self):
        write_checkpoint(self.root, checkpoint_md(age=timedelta(hours=-5)))
        self.assertIn("NOTE-X", self.run_source("compact"))

    def test_age_is_shown_in_minutes_hours_or_days(self):
        cases = [(timedelta(minutes=7, seconds=30), "7 min ago"),
                 (timedelta(hours=47, minutes=59), "47 h ago"),
                 (timedelta(hours=50), "2 d ago")]
        for age, expected in cases:
            with self.subTest(age=age):
                write_checkpoint(self.root, checkpoint_md(age=age))
                self.assertIn(expected, self.run_source("startup"))


def write_session_copy(root, session_id, md):
    path = os.path.join(root, ".agent", "session-checkpoints", f"{session_id}.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(md)
    return path


class TestPerSessionCopy(unittest.TestCase):
    def setUp(self):
        self.root_ctx = tempfile.TemporaryDirectory()
        self.home_ctx = tempfile.TemporaryDirectory()
        self.root = self.root_ctx.name
        self.home = self.home_ctx.name
        self.addCleanup(self.root_ctx.cleanup)
        self.addCleanup(self.home_ctx.cleanup)

    def run_source(self, source, session_id="sess-1"):
        return run_hook({"cwd": self.root, "source": source, "session_id": session_id},
                        self.home, self.root)

    def test_own_copy_is_injected_in_full_while_the_shared_file_belongs_to_another_session(self):
        write_checkpoint(self.root, checkpoint_md(session="other", transcript="**user:** OTHER-TEXT"))
        write_session_copy(self.root, "sess-1", checkpoint_md(transcript="**user:** MY-TEXT"))
        for source in ("compact", "resume", "clear"):
            with self.subTest(source=source):
                ctx = self.run_source(source)
                self.assertTrue(ctx.startswith(sd.INSTRUCTION))
                self.assertIn("MY-TEXT", ctx)
                self.assertNotIn("OTHER-TEXT", ctx)

    def test_stale_own_copy_gets_a_pointer_to_the_copy_even_if_the_shared_file_is_fresh(self):
        write_checkpoint(self.root, checkpoint_md(session="other"))
        copy = write_session_copy(self.root, "sess-1", checkpoint_md(age=timedelta(days=2)))
        ctx = self.run_source("compact")
        self.assertEqual(len(ctx.strip().splitlines()), 1)
        self.assertIn(copy, ctx)
        self.assertIn("this session", ctx)

    def test_truncation_marker_points_at_the_own_copy(self):
        write_checkpoint(self.root, checkpoint_md(session="other"))
        copy = write_session_copy(
            self.root, "sess-1", checkpoint_md(transcript="**user:** " + "x" * 9000))
        ctx = self.run_source("compact")
        self.assertTrue(ctx.endswith(f"full checkpoint at {copy})"))

    def test_without_an_own_copy_the_shared_file_is_used_as_before(self):
        path = write_checkpoint(self.root, checkpoint_md(session="other"))
        write_session_copy(self.root, "someone-else", checkpoint_md(session="someone-else"))
        ctx = self.run_source("compact")
        self.assertEqual(len(ctx.strip().splitlines()), 1)
        self.assertIn(path, ctx)
        self.assertIn("another session", ctx)

    def test_unusable_session_id_never_looks_for_a_copy(self):
        path = write_checkpoint(self.root, checkpoint_md(session="other"))
        ctx = self.run_source("compact", session_id="../x")
        self.assertIn(path, ctx)


class TestOversizedCheckpoint(unittest.TestCase):
    def test_cap_keeps_newest_messages_notes_and_bounded_git_state(self):
        messages = [f"**user:** MSG{i} " + "x" * 1000 for i in range(10)]
        md = checkpoint_md(
            git="GIT-START " + "g" * 3000,
            transcript="\n\n".join(messages),
            notes="OLDEST-NOTE " + "n" * 5000 + " NEWEST-NOTE",
        )
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            path = write_checkpoint(root, md)
            ctx = run_hook({"cwd": root, "source": "compact", "session_id": "sess-1"}, home, root)
        marker = sd._MARKER.format(path=path)
        self.assertLessEqual(len(ctx), 6000)
        transcript = cc.get_section(ctx, "Recent Transcript")
        self.assertIn(messages[9], transcript)
        self.assertNotIn("MSG0 ", ctx)
        self.assertTrue(transcript.startswith("(…earlier messages trimmed…)"))
        notes = cc.get_section(ctx, "Remembered Notes")
        self.assertIn("NEWEST-NOTE", notes)
        self.assertNotIn("OLDEST-NOTE", notes)
        self.assertTrue(notes.startswith("(…earlier notes trimmed…)"))
        self.assertLessEqual(len(notes), 6000 // 4 + len(marker))
        git_state = cc.get_section(ctx, "Git State")
        self.assertTrue(git_state.startswith("GIT-START"))
        self.assertLessEqual(len(git_state), 6000 // 8)
        self.assertTrue(ctx.endswith(marker))

    def test_truncation_marker_gives_the_absolute_path_when_cwd_is_elsewhere(self):
        md = checkpoint_md(transcript="**user:** " + "x" * 9000)
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            sub = os.path.join(root, "pkg")
            os.makedirs(sub)
            path = write_checkpoint(root, md)
            ctx = run_hook({"cwd": sub, "source": "compact", "session_id": "sess-1"}, home, root)
        self.assertTrue(ctx.endswith(f"full checkpoint at {path})"))

    def test_transcript_keeps_whole_messages_only_and_in_order(self):
        messages = [f"**assistant:** M{i} " + "y" * 300 for i in range(20)]
        md = checkpoint_md(transcript="\n\n".join(messages), notes="(none)")
        out = sd.build_restore_payload(md, 2500)
        transcript = cc.get_section(out, "Recent Transcript")
        kept = [i for i in range(20) if messages[i] in transcript]
        self.assertGreater(len(kept), 2)
        self.assertEqual(kept, list(range(kept[0], 20)))
        self.assertLessEqual(len(out), 2500)

    def test_a_single_message_too_big_for_the_cap_keeps_its_beginning(self):
        md = checkpoint_md(transcript="**user:** START-OF-HUGE " + "z" * 5000, notes="(none)")
        out = sd.build_restore_payload(md, 2000)
        self.assertLessEqual(len(out), 2000)
        self.assertIn("START-OF-HUGE", cc.get_section(out, "Recent Transcript"))

    def test_judgment_sections_get_their_own_limit(self):
        md = (
            "# Session Checkpoint\nUpdated: x\n\n"
            "## Current Goal\n" + "G" * 3000 + "\n\n"
            "## Recent Transcript\n**user:** LATEST\n\n"
            "## Next Steps\nNS-" + "s" * 3000 + "\n"
        )
        out = sd.build_restore_payload(md, 2400)
        self.assertLessEqual(len(out), 2400)
        self.assertLessEqual(len(cc.get_section(out, "Current Goal")), 2400 // 8)
        self.assertIn("LATEST", out)
        self.assertEqual([n for n, _ in cc.split_checkpoint(out)[1]],
                         ["Current Goal", "Recent Transcript", "Next Steps"])

    def test_checkpoint_without_a_transcript_section_is_still_capped(self):
        md = "# Session Checkpoint\nUpdated: x\n\n## Git State\n" + "g" * 9000 + "\n"
        out = sd.build_restore_payload(md, 3000)
        self.assertLessEqual(len(out), 3000)
        self.assertIn("## Git State", out)

    def test_small_checkpoint_is_returned_unchanged(self):
        md = checkpoint_md(notes="small")
        self.assertEqual(sd.build_restore_payload(md, 6000), sd.INSTRUCTION + NOTE_SENTENCE + "\n\n" + md)

    def test_cap_never_exceeded_even_when_the_fixed_parts_alone_are_too_big(self):
        md = checkpoint_md(git="g" * 5000, notes="n" * 5000, transcript="**user:** t")
        for cap in (300, 800, 1500):
            with self.subTest(cap=cap):
                self.assertLessEqual(len(sd.build_restore_payload(md, cap)), cap)


class TestSharedNotesWarning(unittest.TestCase):
    def test_notes_present_get_the_warning_between_instruction_and_checkpoint(self):
        out = sd.build_restore_payload(checkpoint_md(notes="something"), 6000)
        self.assertTrue(out.startswith(sd.INSTRUCTION + NOTE_SENTENCE + "\n\n# Session Checkpoint"))

    def test_no_notes_means_no_warning(self):
        out = sd.build_restore_payload(checkpoint_md(notes="(none)"), 6000)
        self.assertNotIn("shared by every session", out)
        self.assertTrue(out.startswith(sd.INSTRUCTION + "# Session Checkpoint"))

    def test_checkpoint_without_a_notes_section_has_no_warning(self):
        md = "# Session Checkpoint\nUpdated: x\n\n## Git State\nBranch: main\n"
        self.assertNotIn("shared by every session", sd.build_restore_payload(md, 6000))

    def test_warning_is_counted_against_the_cap(self):
        md = checkpoint_md(notes="something")
        exact = len(sd.INSTRUCTION) + len(NOTE_SENTENCE) + 2 + len(md)
        self.assertEqual(sd.build_restore_payload(md, exact), sd.INSTRUCTION + NOTE_SENTENCE + "\n\n" + md)
        self.assertLessEqual(len(sd.build_restore_payload(md, exact - 1)), exact - 1)
        self.assertNotEqual(sd.build_restore_payload(md, exact - 1),
                            sd.INSTRUCTION + NOTE_SENTENCE + "\n\n" + md)

    def test_warning_names_the_heading_tag_of_this_sessions_entries(self):
        md = checkpoint_md(session=TAGGABLE_SESSION, notes="something")
        self.assertTrue(sd.build_restore_payload(md, 6000).startswith(
            sd.INSTRUCTION + TAGGED_NOTE_SENTENCE + "\n\n# Session Checkpoint"))

    def test_session_ids_that_get_no_heading_tag_keep_the_plain_warning(self):
        for session in ("394e222", "394e222g-8316", "ABCDEF12-8316", "-394e2224", "unknown", None):
            with self.subTest(session=session):
                md = checkpoint_md(session=session, notes="something")
                self.assertTrue(sd.build_restore_payload(md, 6000).startswith(
                    sd.INSTRUCTION + NOTE_SENTENCE + "\n\n# Session Checkpoint"))

    def test_taggable_session_without_notes_has_no_warning(self):
        md = checkpoint_md(session=TAGGABLE_SESSION, notes="(none)")
        self.assertTrue(sd.build_restore_payload(md, 6000).startswith(
            sd.INSTRUCTION + "# Session Checkpoint"))

    def test_tagged_warning_is_counted_against_the_cap(self):
        md = checkpoint_md(session=TAGGABLE_SESSION, notes="something")
        whole = sd.INSTRUCTION + TAGGED_NOTE_SENTENCE + "\n\n" + md
        self.assertEqual(sd.build_restore_payload(md, len(whole)), whole)
        capped = sd.build_restore_payload(md, len(whole) - 1)
        self.assertLessEqual(len(capped), len(whole) - 1)
        self.assertTrue(capped.startswith(sd.INSTRUCTION + TAGGED_NOTE_SENTENCE))


class TestParseHeader(unittest.TestCase):
    def test_new_format(self):
        md = ("# Session Checkpoint\n"
              "Updated: 2026-11-12T10:00:00 | Trigger: auto | Session: abc-1 | "
              "Project: /r | Cwd: /r/sub\n\n## Git State\nx\n")
        self.assertEqual(cc.parse_header(md), {
            "Updated": "2026-11-12T10:00:00", "Trigger": "auto", "Session": "abc-1",
            "Project": "/r", "Cwd": "/r/sub",
        })

    def test_legacy_format_has_no_session(self):
        md = "# Session Checkpoint\nUpdated: 2026-11-12T10:00:00 | Trigger: auto | Project: /r\n"
        header = cc.parse_header(md)
        self.assertEqual(header["Updated"], "2026-11-12T10:00:00")
        self.assertNotIn("Session", header)

    def test_missing_header_line(self):
        self.assertEqual(cc.parse_header("# Session Checkpoint\n\n## Git State\nx\n"), {})
        self.assertEqual(cc.parse_header(""), {})
        self.assertEqual(cc.parse_header(None), {})

    def test_only_the_first_colon_space_splits_key_from_value(self):
        header = cc.parse_header("Updated: 2026-11-12T10:00:00 | Project: C: drive: x\n")
        self.assertEqual(header["Project"], "C: drive: x")

    def test_a_later_updated_line_in_a_body_is_ignored(self):
        md = "Updated: first | Session: a\n\n## Notes\nUpdated: second | Session: b\n"
        self.assertEqual(cc.parse_header(md)["Session"], "a")


if __name__ == "__main__":
    unittest.main()
