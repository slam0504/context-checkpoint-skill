"""Tests for the PreCompact checkpoint's remembered notes and section layout.

The checkpoint exists so a compacted session can pick up from the remember
plugin's notes. These cases pin down where those notes are read from, that
placeholder-only judgment sections are left out, and that text embedded in the
checkpoint cannot pass for one of its sections.
"""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
sys.path.insert(0, HOOKS)
import checkpoint_common as cc  # noqa: E402
import precompact_checkpoint as pc  # noqa: E402

TODAY = f"today-{datetime.now():%Y-%m-%d}.md"
YESTERDAY = f"today-{datetime.now() - timedelta(days=1):%Y-%m-%d}.md"
JUDGMENT = ["Current Goal", "Current State", "Next Steps"]
INSTRUCTION = "Resuming from checkpoint."
MECHANICAL = ["Git State", "Recent Transcript", "Remembered Notes"]


@contextlib.contextmanager
def project_dir_env(value):
    """Pin CLAUDE_PROJECT_DIR so the runner's own value cannot leak in."""
    saved = os.environ.pop("CLAUDE_PROJECT_DIR", None)
    if value is not None:
        os.environ["CLAUDE_PROJECT_DIR"] = value
    try:
        yield
    finally:
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        if saved is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = saved


def write_note(base, name, text):
    store = os.path.join(base, ".remember")
    os.makedirs(store, exist_ok=True)
    with open(os.path.join(store, name), "w") as f:
        f.write(text)


def write_jsonl(path, rows):
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def user(content):
    return {"message": {"role": "user", "content": content}}


def assistant(content):
    return {"message": {"role": "assistant", "content": content}}


def tool_use(name="Bash"):
    return assistant([{"type": "tool_use", "name": name, "input": {}}])


def tool_result():
    return user([{"type": "tool_result", "tool_use_id": "t", "content": "output"}])


def git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def make_repo_with_worktree(base):
    main = os.path.join(base, "main")
    wt = os.path.join(base, "wt")
    os.makedirs(main)
    git("init", cwd=main)
    git("commit", "--allow-empty", "-m", "init", cwd=main)
    git("worktree", "add", wt, "-b", "feature", cwd=main)
    return main, wt


def transcript_of(rows, n=12, msg_max=1200, total_max=8000):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.jsonl")
        write_jsonl(path, rows)
        return pc.extract_recent_transcript(path, n, msg_max, total_max)


def section_names(md):
    return [name for name, _ in cc.split_checkpoint(md)[1]]


class TestReadRemember(unittest.TestCase):
    def test_reads_the_live_buffer(self):
        with tempfile.TemporaryDirectory() as d, project_dir_env(None):
            write_note(d, "now.md", "working on X\n")
            write_note(d, TODAY, "earlier today")
            self.assertEqual(pc.read_remember(d, 500), "working on X")

    def test_empty_buffer_falls_back_to_todays_file(self):
        with tempfile.TemporaryDirectory() as d, project_dir_env(None):
            write_note(d, "now.md", "\n")
            write_note(d, TODAY, "morning summary")
            self.assertEqual(pc.read_remember(d, 500), "morning summary")

    def test_stale_daily_file_and_manual_handoff_are_not_used(self):
        with tempfile.TemporaryDirectory() as d, project_dir_env(None):
            write_note(d, YESTERDAY, "yesterday")
            write_note(d, "remember.md", "handoff note")
            self.assertEqual(pc.read_remember(d, 500), "(none)")

    def test_no_store(self):
        with tempfile.TemporaryDirectory() as d, project_dir_env(None):
            self.assertEqual(pc.read_remember(d, 500), "(none)")

    def test_store_is_found_after_cwd_has_drifted(self):
        with tempfile.TemporaryDirectory() as root, project_dir_env(root):
            sub = os.path.join(root, "worktrees", "feature")
            os.makedirs(sub)
            write_note(root, "now.md", "notes from the project store")
            self.assertEqual(pc.read_remember(sub, 500), "notes from the project store")

    def test_project_dir_store_wins_over_cwd_store(self):
        with tempfile.TemporaryDirectory() as root, project_dir_env(root):
            sub = os.path.join(root, "nested")
            os.makedirs(sub)
            write_note(root, TODAY, "this session")
            write_note(sub, "now.md", "some other session")
            self.assertEqual(pc.read_remember(sub, 500), "this session")

    def test_cwd_store_is_used_when_project_dir_has_none(self):
        with tempfile.TemporaryDirectory() as root, project_dir_env(root):
            sub = os.path.join(root, "nested")
            os.makedirs(sub)
            write_note(sub, "now.md", "cwd store")
            self.assertEqual(pc.read_remember(sub, 500), "cwd store")

    def test_keeps_the_newest_notes_within_the_cap(self):
        with tempfile.TemporaryDirectory() as d, project_dir_env(None):
            write_note(d, "now.md", "OLDEST " + "x" * 400 + " NEWEST")
            out = pc.read_remember(d, 120)
        self.assertLessEqual(len(out), 120)
        self.assertTrue(out.startswith("(…earlier notes trimmed…)"))
        self.assertTrue(out.endswith("NEWEST"))
        self.assertNotIn("OLDEST", out)

    def test_unreadable_buffer_falls_back_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as d, project_dir_env(None):
            os.makedirs(os.path.join(d, ".remember", "now.md"))
            write_note(d, TODAY, "readable")
            self.assertEqual(pc.read_remember(d, 500), "readable")

    def test_undecodable_buffer_falls_back_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as d, project_dir_env(None):
            write_note(d, TODAY, "readable")
            with open(os.path.join(d, ".remember", "now.md"), "wb") as f:
                f.write(b"\xff\xfe\xff")
            self.assertEqual(pc.read_remember(d, 500), "readable")


class TestBuildCheckpoint(unittest.TestCase):
    def build(self, notes="(none)", **judgment):
        carried = dict.fromkeys(JUDGMENT, cc.PLACEHOLDER)
        carried.update(judgment)
        return pc.build_checkpoint("auto", "/p", "Branch: main", "**user:** hi", notes, carried)

    def test_placeholder_only_judgment_sections_are_left_out(self):
        md = self.build()
        self.assertEqual(section_names(md), MECHANICAL)
        self.assertNotIn(cc.PLACEHOLDER, md)

    def test_real_judgment_sections_keep_their_place(self):
        md = self.build(**{"Current Goal": "g", "Current State": "s", "Next Steps": "n"})
        self.assertEqual(
            section_names(md),
            ["Current Goal", "Current State", *MECHANICAL, "Next Steps"],
        )
        self.assertEqual(cc.get_section(md, "Next Steps"), "n")

    def test_only_the_placeholder_sections_are_dropped(self):
        md = self.build(**{"Current Goal": "ship v2"})
        self.assertEqual(section_names(md), ["Current Goal", *MECHANICAL])

    def test_note_headings_do_not_become_checkpoint_sections(self):
        with tempfile.TemporaryDirectory() as d, project_dir_env(None):
            write_note(d, "now.md", "## 10:57 | main\ndid a thing\n\n## 11:20 | main\ndid more")
            notes = pc.read_remember(d, 500)
        md = self.build(notes=notes)
        self.assertEqual(section_names(md), MECHANICAL)
        self.assertIn("did more", cc.get_section(md, "Remembered Notes"))


class TestTranscript(unittest.TestCase):
    def test_message_headings_are_demoted(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            write_jsonl(path, [
                {"message": {"role": "assistant", "content": "Summary\n## Next Steps\nship it"}},
            ])
            out = pc.extract_recent_transcript(path, 5, 500, 2000)
        self.assertIn("### Next Steps\nship it", out)
        self.assertFalse([line for line in out.split("\n") if line.startswith("## ")])


class TestReadRememberWorktree(unittest.TestCase):
    def test_linked_worktree_reads_the_main_checkouts_notes(self):
        with tempfile.TemporaryDirectory() as d:
            main, wt = make_repo_with_worktree(d)
            write_note(main, "now.md", "notes kept in the main checkout")
            with project_dir_env(wt):
                self.assertEqual(pc.read_remember(wt, 500), "notes kept in the main checkout")

    def test_subdirectory_of_a_linked_worktree_reads_the_main_checkouts_notes(self):
        with tempfile.TemporaryDirectory() as d:
            main, wt = make_repo_with_worktree(d)
            sub = os.path.join(wt, "pkg", "inner")
            os.makedirs(sub)
            write_note(main, "now.md", "main checkout notes")
            with project_dir_env(wt):
                self.assertEqual(pc.read_remember(sub, 500), "main checkout notes")

    def test_cwd_worktree_is_resolved_when_project_dir_is_unset(self):
        with tempfile.TemporaryDirectory() as d:
            main, wt = make_repo_with_worktree(d)
            write_note(main, "now.md", "from main")
            with project_dir_env(None):
                self.assertEqual(pc.read_remember(wt, 500), "from main")

    def test_worktree_own_store_is_still_read_when_main_has_none(self):
        with tempfile.TemporaryDirectory() as d:
            main, wt = make_repo_with_worktree(d)
            write_note(wt, "now.md", "worktree store")
            with project_dir_env(wt):
                self.assertEqual(pc.read_remember(wt, 500), "worktree store")

    def test_plain_checkout_does_not_shell_out_or_redirect(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            write_note(d, "now.md", "own notes")
            with project_dir_env(d), mock.patch.object(
                pc.subprocess, "run", side_effect=AssertionError("git must not run")
            ):
                self.assertEqual(pc.read_remember(d, 500), "own notes")

    def test_git_failure_means_not_a_worktree(self):
        with tempfile.TemporaryDirectory() as d, project_dir_env(d):
            write_note(d, "now.md", "own notes")
            with mock.patch.object(pc.subprocess, "run", side_effect=OSError("no git")):
                self.assertEqual(pc.read_remember(d, 500), "own notes")
            with mock.patch.object(
                pc.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 5)
            ):
                self.assertEqual(pc.read_remember(d, 500), "own notes")


class TestCarryForward(unittest.TestCase):
    PREV = (
        "# Session Checkpoint\n"
        "Updated: 2026-01-01T00:00:00 | Trigger: auto | {session}Project: /p\n\n"
        "## Current Goal\nKEEPME\n\n## Next Steps\nlater\n"
    )

    def test_same_session_judgment_is_carried(self):
        carried = pc.carry_forward(self.PREV.format(session="Session: mine | "), "mine")
        self.assertEqual(carried["Current Goal"], "KEEPME")
        self.assertEqual(carried["Next Steps"], "later")

    def test_other_sessions_judgment_is_not_carried(self):
        carried = pc.carry_forward(self.PREV.format(session="Session: theirs | "), "mine")
        self.assertEqual(set(carried.values()), {cc.PLACEHOLDER})

    def test_legacy_checkpoint_without_session_is_not_carried(self):
        carried = pc.carry_forward(self.PREV.format(session=""), "mine")
        self.assertEqual(set(carried.values()), {cc.PLACEHOLDER})

    def test_unknown_current_session_never_carries(self):
        for sid in ("", None):
            carried = pc.carry_forward(self.PREV.format(session="Session: | "), sid)
            self.assertEqual(set(carried.values()), {cc.PLACEHOLDER})

    def test_no_previous_checkpoint(self):
        self.assertEqual(set(pc.carry_forward("", "mine").values()), {cc.PLACEHOLDER})


class TestToolHeavyTranscript(unittest.TestCase):
    def test_dense_tool_use_is_folded_into_one_line_and_text_survives(self):
        rows = [user("FIRST-USER-TEXT"), assistant("FIRST-ASSISTANT-TEXT")]
        for i in range(30):
            rows.append(tool_use("Bash" if i < 15 else "Read" if i < 25 else "Edit"))
            rows.append(tool_result())
        out = transcript_of(rows)
        self.assertIn("FIRST-USER-TEXT", out)
        self.assertIn("FIRST-ASSISTANT-TEXT", out)
        self.assertEqual(out.count("[tool_use"), 1)
        self.assertIn("**assistant:** [tool_use: Bash ×15, Read ×10, Edit ×5]", out)
        self.assertNotIn("tool_result", out)

    def test_count_is_omitted_for_single_use_and_missing_name_is_a_question_mark(self):
        rows = [
            user("go"),
            tool_use("Edit"),
            assistant([{"type": "tool_use", "input": {}}]),
            tool_use("Edit"),
            assistant([{"type": "tool_use", "name": None, "input": {}}]),
        ]
        self.assertIn("[tool_use: Edit ×2, ? ×2]", transcript_of(rows))
        self.assertIn("[tool_use: Edit]", transcript_of([user("go"), tool_use("Edit")]))

    def test_window_counts_messages_with_text_not_tool_calls(self):
        rows = []
        for i in range(1, 6):
            rows.append(assistant(f"TEXT-{i}"))
            for _ in range(10):
                rows.append(tool_use(f"ToolAfter{i}"))
                rows.append(tool_result())
        out = transcript_of(rows, n=3)
        for i in (3, 4, 5):
            self.assertIn(f"TEXT-{i}", out)
        for i in (1, 2):
            self.assertNotIn(f"TEXT-{i}", out)
        # Tool runs between and after the windowed texts stay; earlier ones go.
        self.assertIn("ToolAfter3", out)
        self.assertIn("ToolAfter5", out)
        self.assertNotIn("ToolAfter2", out)
        self.assertNotIn("ToolAfter1", out)

    def test_fewer_texts_than_the_window_keeps_everything(self):
        rows = [tool_use("Early"), assistant("ONLY-TEXT"), tool_use("Late")]
        out = transcript_of(rows, n=5)
        self.assertIn("Early", out)
        self.assertIn("ONLY-TEXT", out)
        self.assertIn("Late", out)

    def test_tools_after_the_last_text_are_kept(self):
        rows = [user("q"), assistant("answer"), tool_use("Grep"), tool_result(),
                tool_use("Grep"), tool_use("Glob")]
        out = transcript_of(rows, n=1)
        self.assertTrue(out.rstrip().endswith("[tool_use: Grep ×2, Glob]"))
        self.assertLess(out.index("answer"), out.index("[tool_use"))
        self.assertNotIn("**user:** q", out)

    def test_a_text_message_ends_the_tool_run(self):
        rows = [assistant("a"), tool_use("Bash"), assistant("b"), tool_use("Bash")]
        out = transcript_of(rows)
        self.assertEqual(out.count("[tool_use: Bash]"), 2)

    def test_tool_summary_on_a_text_message_is_not_cut_by_the_message_cap(self):
        rows = [assistant([
            {"type": "text", "text": "t" * 200},
            {"type": "tool_use", "name": "Bash", "input": {}},
        ])]
        out = transcript_of(rows, msg_max=50)
        self.assertIn("\n[tool_use: Bash]", out)
        self.assertNotIn("t" * 100, out)

    def test_thinking_blocks_are_ignored(self):
        rows = [assistant([{"type": "thinking", "thinking": "SECRET-THOUGHT"},
                           {"type": "text", "text": "visible"}])]
        out = transcript_of(rows)
        self.assertIn("visible", out)
        self.assertNotIn("SECRET-THOUGHT", out)

    def test_older_entries_are_dropped_when_over_the_total_cap(self):
        rows = [assistant(f"MSG{i} " + "x" * 200) for i in range(6)]
        out = transcript_of(rows, total_max=500)
        self.assertTrue(out.startswith("(…earlier messages trimmed…)"))
        self.assertIn("MSG5", out)
        self.assertNotIn("MSG0", out)
        self.assertLessEqual(len(out), 500)

    def test_empty_and_unavailable(self):
        self.assertEqual(transcript_of([tool_result()]), "(no recent messages)")
        self.assertEqual(pc.extract_recent_transcript("/nonexistent/t.jsonl", 5, 100, 100),
                         "(transcript unavailable)")


class TestTranscriptNoise(unittest.TestCase):
    REMINDER = "<system-reminder>\nREMINDER-BODY\n</system-reminder>"

    def test_system_reminder_after_a_tool_result_is_not_a_message(self):
        rows = [user("REAL-QUESTION"), assistant("REAL-ANSWER")]
        rows += [user([{"type": "tool_result", "content": "x"},
                       {"type": "text", "text": self.REMINDER}]) for _ in range(5)]
        out = transcript_of(rows, n=2)
        self.assertNotIn("REMINDER-BODY", out)
        self.assertIn("REAL-QUESTION", out)
        self.assertIn("REAL-ANSWER", out)

    def test_reminder_is_stripped_but_the_users_words_in_the_same_message_stay(self):
        rows = [user([{"type": "text", "text": "USER-WORDS"},
                      {"type": "text", "text": self.REMINDER}])]
        out = transcript_of(rows)
        self.assertIn("**user:** USER-WORDS", out)
        self.assertNotIn("REMINDER-BODY", out)

    def test_reminder_as_a_plain_string_or_with_leading_whitespace_is_dropped(self):
        rows = [user("real"), user(self.REMINDER), user("   \n" + self.REMINDER)]
        out = transcript_of(rows, n=1)
        self.assertEqual(out, "**user:** real")

    def test_reminder_does_not_use_up_the_window(self):
        rows = [user("KEEP-ME")] + [user(self.REMINDER) for _ in range(10)]
        self.assertIn("KEEP-ME", transcript_of(rows, n=1))

    def test_meta_and_compact_summary_lines_are_skipped(self):
        rows = [user("REAL-1"), assistant("REAL-2"),
                dict(user("SKILL-BODY"), isMeta=True),
                dict(user("SUMMARY-BODY"), isCompactSummary=True),
                dict(assistant("META-ASSISTANT"), isMeta=True)]
        out = transcript_of(rows, n=2)
        for gone in ("SKILL-BODY", "SUMMARY-BODY", "META-ASSISTANT"):
            self.assertNotIn(gone, out)
        self.assertIn("REAL-1", out)
        self.assertIn("REAL-2", out)

    def test_false_meta_flag_does_not_hide_a_message(self):
        out = transcript_of([dict(user("VISIBLE"), isMeta=False)])
        self.assertIn("VISIBLE", out)

    def test_api_request_blob_copies_are_not_counted_again(self):
        # Real transcripts log the request sent to the API as extra lines that
        # repeat the user's prompt (behind a reminder) and each tool call.
        rows = [
            dict(user("REAL-QUESTION"), type="user"),
            dict(user([{"type": "text", "text": self.REMINDER},
                       {"type": "text", "text": "REAL-QUESTION"}]), type="api-request-blob"),
            dict(tool_use("Bash"), type="assistant"),
            dict(tool_use("Bash"), type="api-request-blob"),
        ]
        self.assertEqual(
            transcript_of(rows, n=1),
            "**user:** REAL-QUESTION\n\n**assistant:** [tool_use: Bash]",
        )


class TestHooksEndToEnd(unittest.TestCase):
    """Runs the installed entry points the way Claude Code invokes them."""

    def run_hook(self, script, payload, home, project_dir, **extra_env):
        env = {
            k: v for k, v in os.environ.items()
            if not k.startswith("CC_") and k not in ("CLAUDE_PROJECT_DIR", "CONTEXT_CHECKPOINT_STRICT")
        }
        env.update(HOME=home)
        env.update(extra_env)
        if project_dir is not None:
            env["CLAUDE_PROJECT_DIR"] = project_dir
        result = subprocess.run(
            ["bash", os.path.join(HOOKS, script)],
            input=json.dumps(payload), text=True, capture_output=True, env=env,
        )
        self.assertEqual(result.returncode, 0)
        # The wrapper swallows hook failures so it can never block compaction;
        # its error log is the only place a crash would show up.
        with open(os.path.join(home, ".claude", "hooks", ".wrapper-error.log")) as f:
            self.assertEqual(f.read(), "")
        return result.stdout

    def read(self, path):
        with open(path) as f:
            return f.read()

    def test_compacted_session_gets_its_notes_back(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            sub = os.path.join(root, "worktrees", "feature")
            os.makedirs(sub)
            write_note(root, "now.md", "## 10:57 | main\nrefactor half done, tests red")
            transcript = os.path.join(root, "t.jsonl")
            write_jsonl(transcript, [
                {"message": {"role": "user", "content": "keep going"}},
                {"message": {"role": "assistant",
                             "content": "Done so far.\n## Next Steps\nNOT A REAL SECTION"}},
            ])
            payload = {"cwd": sub, "transcript_path": transcript, "trigger": "auto",
                       "session_id": "sess-1"}
            checkpoint = os.path.join(root, ".agent", "session-checkpoint.md")

            # Twice: the second run reads the first checkpoint back to carry
            # judgment sections forward, which is where an embedded heading
            # would get promoted to a real section.
            for _ in range(2):
                self.run_hook("precompact-checkpoint.sh", payload, home, root)
                md = self.read(checkpoint)
                self.assertEqual(section_names(md), MECHANICAL)
                self.assertIn("refactor half done, tests red", cc.get_section(md, "Remembered Notes"))
                self.assertIn("NOT A REAL SECTION", cc.get_section(md, "Recent Transcript"))

            out = self.run_hook(
                "sessionstart-restore.sh",
                {"cwd": sub, "source": "compact", "session_id": "sess-1"}, home, root,
            )
            restored = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("refactor half done, tests red", restored)
            self.assertNotIn(cc.PLACEHOLDER, restored)

    def test_real_judgment_sections_survive_the_next_checkpoint(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            checkpoint = os.path.join(d, ".agent", "session-checkpoint.md")
            os.makedirs(os.path.dirname(checkpoint))
            with open(checkpoint, "w") as f:
                f.write(
                    "# Session Checkpoint\n"
                    "Updated: 2026-01-01T00:00:00 | Trigger: auto | Session: sess-1 | Project: x\n\n"
                    "## Current Goal\nKEEPME\n"
                )
            transcript = os.path.join(d, "t.jsonl")
            write_jsonl(transcript, [{"message": {"role": "user", "content": "later"}}])
            self.run_hook(
                "precompact-checkpoint.sh",
                {"cwd": d, "transcript_path": transcript, "trigger": "auto",
                 "session_id": "sess-1"}, home, d,
            )
            md = self.read(checkpoint)
        self.assertEqual(section_names(md), ["Current Goal", *MECHANICAL])
        self.assertEqual(cc.get_section(md, "Current Goal"), "KEEPME")

    def test_restore_stays_within_its_cap_and_keeps_the_notes(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            write_note(d, "now.md", "n" * 5000 + " NEWEST-NOTE")
            transcript = os.path.join(d, "t.jsonl")
            write_jsonl(transcript, [
                {"message": {"role": "user", "content": "m" * 3000}} for _ in range(12)
            ])
            self.run_hook(
                "precompact-checkpoint.sh",
                {"cwd": d, "transcript_path": transcript, "trigger": "auto",
                 "session_id": "sess-1"}, home, d,
            )
            out = self.run_hook(
                "sessionstart-restore.sh",
                {"cwd": d, "source": "compact", "session_id": "sess-1"}, home, d,
            )
        restored = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(restored), 6000)
        self.assertIn("NEWEST-NOTE", restored)


    def run_precompact(self, payload, home, project_dir, rows=None):
        if rows is not None:
            transcript = os.path.join(home, "t.jsonl")
            write_jsonl(transcript, rows)
            payload = dict(payload, transcript_path=transcript)
        self.run_hook("precompact-checkpoint.sh", payload, home, project_dir)

    def test_checkpoint_is_written_under_the_project_dir_not_the_drifted_cwd(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            sub = os.path.join(root, "sub")
            os.makedirs(sub)
            self.run_precompact({"cwd": sub, "trigger": "auto", "session_id": "s1"},
                                home, root, [user("hi")])
            md = self.read(os.path.join(root, ".agent", "session-checkpoint.md"))
            self.assertFalse(os.path.exists(os.path.join(sub, ".agent")))
            self.assertTrue(os.path.exists(os.path.join(root, ".agent", "context-checkpoint.log")))
        header = cc.parse_header(md)
        self.assertEqual(header["Project"], root)
        self.assertEqual(header["Cwd"], sub)

    def test_without_project_dir_the_checkpoint_goes_under_cwd(self):
        with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as home:
            self.run_precompact({"cwd": cwd, "trigger": "auto", "session_id": "s1"},
                                home, None, [user("hi")])
            md = self.read(os.path.join(cwd, ".agent", "session-checkpoint.md"))
        self.assertEqual(cc.parse_header(md)["Project"], cwd)
        self.assertNotIn("Cwd", cc.parse_header(md))

    def test_header_records_the_session_and_omits_cwd_when_it_matches_the_root(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            self.run_precompact({"cwd": d, "trigger": "manual", "session_id": "sess-abc"},
                                home, d, [user("hi")])
            md = self.read(os.path.join(d, ".agent", "session-checkpoint.md"))
        header = cc.parse_header(md)
        self.assertEqual(header["Session"], "sess-abc")
        self.assertEqual(header["Trigger"], "manual")
        self.assertNotIn("Cwd:", md)

    def test_missing_session_id_is_written_as_unknown(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            self.run_precompact({"cwd": d, "trigger": "auto"}, home, d, [user("hi")])
            md = self.read(os.path.join(d, ".agent", "session-checkpoint.md"))
        self.assertEqual(cc.parse_header(md)["Session"], "unknown")

    def test_another_sessions_judgment_sections_are_not_carried_by_the_hook(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            checkpoint = os.path.join(d, ".agent", "session-checkpoint.md")
            os.makedirs(os.path.dirname(checkpoint))
            with open(checkpoint, "w") as f:
                f.write("# Session Checkpoint\n"
                        "Updated: 2026-01-01T00:00:00 | Trigger: auto | Session: other | Project: x\n\n"
                        "## Current Goal\nSTALE-GOAL\n")
            self.run_precompact({"cwd": d, "trigger": "auto", "session_id": "mine"},
                                home, d, [user("hi")])
            md = self.read(checkpoint)
        self.assertNotIn("STALE-GOAL", md)

    def test_hook_in_a_linked_worktree_captures_the_main_checkouts_notes(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            main, wt = make_repo_with_worktree(d)
            write_note(main, "now.md", "MAIN-CHECKOUT-NOTE")
            self.run_precompact({"cwd": wt, "trigger": "auto", "session_id": "s1"},
                                home, wt, [user("hi")])
            md = self.read(os.path.join(wt, ".agent", "session-checkpoint.md"))
        self.assertIn("MAIN-CHECKOUT-NOTE", cc.get_section(md, "Remembered Notes"))

    def sessionstart(self, source, session_id, home, root):
        out = self.run_hook(
            "sessionstart-restore.sh",
            {"cwd": root, "source": source, "session_id": session_id}, home, root,
        )
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else None

    def two_sessions_compacting(self, root, home):
        for sid, text in (("sess-A", "A-ONLY-TEXT"), ("sess-B", "B-ONLY-TEXT")):
            self.run_precompact({"cwd": root, "trigger": "auto", "session_id": sid},
                                home, root, [user(text)])

    def test_session_gets_its_own_checkpoint_back_after_another_session_compacted(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            self.two_sessions_compacting(root, home)
            a = self.sessionstart("compact", "sess-A", home, root)
            b = self.sessionstart("compact", "sess-B", home, root)
        self.assertTrue(a.startswith(INSTRUCTION))
        self.assertIn("A-ONLY-TEXT", a)
        self.assertIn("Session: sess-A", a)
        self.assertNotIn("B-ONLY-TEXT", a)
        self.assertIn("B-ONLY-TEXT", b)
        self.assertNotIn("A-ONLY-TEXT", b)

    def test_unrelated_new_session_gets_a_pointer_to_the_latest_shared_checkpoint(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            self.two_sessions_compacting(root, home)
            ctx = self.sessionstart("startup", "sess-C", home, root)
            shared = os.path.join(root, ".agent", "session-checkpoint.md")
            header = cc.parse_header(self.read(shared))
        self.assertEqual(len(ctx.strip().splitlines()), 1)
        self.assertIn("written by another session", ctx)
        self.assertIn(shared, ctx)
        self.assertEqual(header["Session"], "sess-B")

    def test_carried_goal_survives_another_sessions_checkpoint(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            own = os.path.join(root, ".agent", "session-checkpoints", "sess-A.md")
            payload = {"cwd": root, "trigger": "auto", "session_id": "sess-A"}
            self.run_precompact(payload, home, root, [user("one")])
            md = self.read(own)
            with open(own, "w") as f:
                f.write(md.replace("## Git State", "## Current Goal\nREAL-GOAL-A\n\n## Git State"))
            self.run_precompact(dict(payload, session_id="sess-B"), home, root, [user("two")])
            b_md = self.read(os.path.join(root, ".agent", "session-checkpoint.md"))
            self.run_precompact(payload, home, root, [user("three")])
            md = self.read(own)
        self.assertEqual(cc.get_section(md, "Current Goal"), "REAL-GOAL-A")
        self.assertNotIn("REAL-GOAL-A", b_md)

    def age_file(self, path, hours):
        t = (datetime.now() - timedelta(hours=hours)).timestamp()
        os.utime(path, (t, t))

    def test_old_session_copies_are_pruned_and_everything_else_is_left_alone(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            d = os.path.join(root, ".agent", "session-checkpoints")
            os.makedirs(os.path.join(d, "old-dir.md"))
            for name in ("stale.md", "recent.md", "notes.txt", "a.b.md"):
                with open(os.path.join(d, name), "w") as f:
                    f.write("x")
            self.age_file(os.path.join(d, "stale.md"), 8 * 24)
            self.age_file(os.path.join(d, "recent.md"), 6 * 24)
            self.age_file(os.path.join(d, "notes.txt"), 30 * 24)
            self.age_file(os.path.join(d, "a.b.md"), 30 * 24)
            self.age_file(os.path.join(d, "old-dir.md"), 30 * 24)
            self.run_precompact({"cwd": root, "trigger": "auto", "session_id": "now"},
                                home, root, [user("hi")])
            left = sorted(os.listdir(d))
        self.assertEqual(left, ["a.b.md", "notes.txt", "now.md", "old-dir.md", "recent.md"])

    def copies_left_after_compacting(self, **age_settings):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            d = os.path.join(root, ".agent", "session-checkpoints")
            os.makedirs(d)
            for name in ("two-hours.md", "fresh.md"):
                with open(os.path.join(d, name), "w") as f:
                    f.write("x")
            self.age_file(os.path.join(d, "two-hours.md"), 2)
            transcript = os.path.join(home, "t.jsonl")
            write_jsonl(transcript, [user("hi")])
            self.run_hook(
                "precompact-checkpoint.sh",
                {"cwd": root, "trigger": "auto", "session_id": "now",
                 "transcript_path": transcript}, home, root, **age_settings,
            )
            return sorted(os.listdir(d))

    def test_pruning_window_follows_the_age_settings(self):
        left = self.copies_left_after_compacting(
            CC_POINTER_MAX_AGE_HOURS="1", CC_RESTORE_MAX_AGE_HOURS="1")
        self.assertEqual(left, ["fresh.md", "now.md"])

    def test_copy_is_kept_while_a_full_restore_could_still_read_it(self):
        # Only the pointer window is shortened; a resume within the restore
        # window (24 h by default) would still inject a 2-hour-old copy.
        left = self.copies_left_after_compacting(CC_POINTER_MAX_AGE_HOURS="1")
        self.assertEqual(left, ["fresh.md", "now.md", "two-hours.md"])

    def test_shared_checkpoint_is_still_written_when_the_session_copy_cannot_be(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(root, ".agent"))
            blocker = os.path.join(root, ".agent", "session-checkpoints")
            with open(blocker, "w") as f:
                f.write("a file where the directory should be")
            self.two_sessions_compacting(root, home)
            shared = self.read(os.path.join(root, ".agent", "session-checkpoint.md"))
            log = self.read(os.path.join(root, ".agent", "context-checkpoint.log"))
            b = self.sessionstart("compact", "sess-B", home, root)
            still_a_file = os.path.isfile(blocker)
        self.assertEqual(cc.parse_header(shared)["Session"], "sess-B")
        self.assertIn("per-session checkpoint not written", log)
        self.assertTrue(b.startswith(INSTRUCTION))
        self.assertIn("B-ONLY-TEXT", b)
        self.assertTrue(still_a_file)

    def test_unusable_session_ids_only_write_the_shared_checkpoint(self):
        for bad in ("../evil", "a/b", "a.b", "", None):
            with self.subTest(session_id=bad), \
                    tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as home:
                root = os.path.join(base, "proj")
                os.makedirs(root)
                payload = {"cwd": root, "trigger": "auto"}
                if bad is not None:
                    payload["session_id"] = bad
                self.run_precompact(payload, home, root, [user("hi")])
                self.assertTrue(os.path.exists(os.path.join(root, ".agent", "session-checkpoint.md")))
                d = os.path.join(root, ".agent", "session-checkpoints")
                self.assertEqual(os.listdir(d) if os.path.isdir(d) else [], [])
                self.assertEqual(os.listdir(base), ["proj"])
                for dirpath, _, files in os.walk(base):
                    self.assertNotIn("unknown.md", files)
                    self.assertNotIn("evil.md", files)

    def test_log_line_names_the_session(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            self.run_precompact({"cwd": root, "trigger": "manual", "session_id": "sess-log"},
                                home, root, [user("hi")])
            self.run_precompact({"cwd": root, "trigger": "auto"}, home, root, [user("hi")])
            log = self.read(os.path.join(root, ".agent", "context-checkpoint.log"))
        self.assertIn("PreCompact checkpoint written (trigger=manual, session=sess-log)", log)
        self.assertIn("PreCompact checkpoint written (trigger=auto, session=unknown)", log)


if __name__ == "__main__":
    unittest.main()
