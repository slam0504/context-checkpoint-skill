# context-checkpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a zero-cost Claude Code hook pair that auto-saves a bounded session checkpoint on compaction and auto-restores it on compact/resume/clear, so context survives compaction and `/clear`.

**Architecture:** A `PreCompact` hook writes `<project>/.agent/session-checkpoint.md` (git state + bounded recent transcript + carried-forward judgment sections); a `SessionStart` hook injects a bounded payload back via `additionalContext`. Logic lives in three Python modules (testable with stdlib `unittest`); two tiny bash wrappers guarantee non-blocking behavior; an installer copies the files to `~/.claude/hooks/` and **merges** (never overwrites) `~/.claude/settings.json`.

**Tech Stack:** bash + python3 (stdlib only), Claude Code hooks, `unittest` for tests.

Spec: `docs/superpowers/specs/2026-05-25-context-checkpoint-hooks-design.md`

---

## File Structure

Dev/source location (version-controlled, testable): `/Users/eason/playground/context-checkpoint/`

| Path | Responsibility |
|------|----------------|
| `hooks/checkpoint_common.py` | Shared helpers: hook-input parse, config, project-root resolution, truncation, logging, atomic write, checkpoint section parsing |
| `hooks/precompact_checkpoint.py` | PreCompact logic: git state, transcript extraction, remember read, carry-forward, build + atomic write |
| `hooks/sessionstart_restore.py` | SessionStart logic: pointer (startup) + bounded restore payload (compact/resume/clear) |
| `hooks/precompact-checkpoint.sh` | Non-blocking bash wrapper → `precompact_checkpoint.py` |
| `hooks/sessionstart-restore.sh` | Non-blocking bash wrapper → `sessionstart_restore.py` |
| `hooks/merge_settings.py` | Idempotent, non-destructive merge of the two hooks into `~/.claude/settings.json` (install-time only; not copied as a hook) |
| `install.sh` | Copy hook files to `~/.claude/hooks/`, chmod, run merge |
| `tests/test_common.py` | Unit tests for `checkpoint_common` |
| `tests/test_precompact.py` | Unit + e2e tests for PreCompact |
| `tests/test_sessionstart.py` | Unit + e2e tests for SessionStart |
| `tests/test_merge_settings.py` | Tests for settings merge (preserve + idempotent) |

Install target: `~/.claude/hooks/` (the 5 hook files), wired in `~/.claude/settings.json`. Checkpoints land per-project in `<project>/.agent/`.

Run all tests: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest discover -s tests -v`

---

### Task 1: Scaffold dev project + git repo

**Files:**
- Create: `/Users/eason/playground/context-checkpoint/` (dir tree)

- [ ] **Step 1: Create directories**

Run:
```bash
mkdir -p /Users/eason/playground/context-checkpoint/hooks /Users/eason/playground/context-checkpoint/tests
```

- [ ] **Step 2: Init git repo (enables commits + makes Git State testable)**

Run:
```bash
cd /Users/eason/playground/context-checkpoint && git init && printf '.agent/\n__pycache__/\n*.tmp\n' > .gitignore
```
Expected: "Initialized empty Git repository". (This dev dir becomes its own repo; the playground root remains non-git.)

- [ ] **Step 3: Commit scaffold**

```bash
cd /Users/eason/playground/context-checkpoint && git add .gitignore && git commit -m "chore: scaffold context-checkpoint project"
```

---

### Task 2: `checkpoint_common.py` — input, config, project root, truncation

**Files:**
- Create: `/Users/eason/playground/context-checkpoint/hooks/checkpoint_common.py`
- Test: `/Users/eason/playground/context-checkpoint/tests/test_common.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_common.py`:
```python
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
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_common -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'checkpoint_common'`

- [ ] **Step 3: Implement**

Create `hooks/checkpoint_common.py`:
```python
"""Shared helpers for context-checkpoint hooks (stdlib only)."""
import json
import os
from datetime import datetime

PLACEHOLDER = "(reconstruct from Recent Transcript on resume)"


def read_hook_input(raw):
    if not raw or not str(raw).strip():
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def get_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def resolve_project_root(data):
    return (
        (data or {}).get("cwd")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )


def truncate(text, maxchars, marker="\n…(truncated)…"):
    if text is None:
        return ""
    if len(text) <= maxchars:
        return text
    keep = max(0, maxchars - len(marker))
    return text[:keep] + marker


def log(project_root, msg):
    try:
        d = os.path.join(project_root, ".agent")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "context-checkpoint.log"), "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_common -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/eason/playground/context-checkpoint && git add hooks/checkpoint_common.py tests/test_common.py && git commit -m "feat: checkpoint_common input/config/truncation helpers"
```

---

### Task 3: `checkpoint_common.py` — atomic write + section parsing

**Files:**
- Modify: `/Users/eason/playground/context-checkpoint/hooks/checkpoint_common.py`
- Test: `/Users/eason/playground/context-checkpoint/tests/test_common.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_common.py` (before the `if __name__` line):
```python
import tempfile


class TestWriteAndSections(unittest.TestCase):
    def test_atomic_write_no_tmp_left(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "out.md")
            cc.atomic_write(path, "content")
            with open(path) as f:
                self.assertEqual(f.read(), "content")
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_split_and_get_section(self):
        md = (
            "# Session Checkpoint\n"
            "Updated: x | Trigger: auto | Project: /p\n\n"
            "## Current Goal\nship it\n\n"
            "## Git State\nBranch: main\n\n"
            "## Next Steps\ndo thing\n"
        )
        self.assertEqual(cc.get_section(md, "Current Goal"), "ship it")
        self.assertEqual(cc.get_section(md, "Git State"), "Branch: main")
        self.assertEqual(cc.get_section(md, "Next Steps"), "do thing")
        self.assertEqual(cc.get_section(md, "Missing"), "")
```

- [ ] **Step 2: Run, verify fail**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_common -v`
Expected: FAIL — `AttributeError: module 'checkpoint_common' has no attribute 'atomic_write'`

- [ ] **Step 3: Implement**

Append to `hooks/checkpoint_common.py`:
```python
def atomic_write(path, content):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)


def split_checkpoint(md):
    """Return (preamble, [(name, body), ...]) preserving order."""
    preamble = []
    sections = []
    cur_name = None
    cur_body = []
    for line in (md or "").splitlines():
        if line.startswith("## "):
            if cur_name is None:
                preamble = cur_body
            else:
                sections.append((cur_name, "\n".join(cur_body).strip()))
            cur_name = line[3:].strip()
            cur_body = []
        else:
            cur_body.append(line)
    if cur_name is None:
        preamble = cur_body
    else:
        sections.append((cur_name, "\n".join(cur_body).strip()))
    return ("\n".join(preamble).strip(), sections)


def get_section(md, name):
    _, sections = split_checkpoint(md)
    for n, body in sections:
        if n == name:
            return body
    return ""
```

- [ ] **Step 4: Run, verify pass**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_common -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/eason/playground/context-checkpoint && git add hooks/checkpoint_common.py tests/test_common.py && git commit -m "feat: atomic write + checkpoint section parsing"
```

---

### Task 4: `precompact_checkpoint.py` — git state

**Files:**
- Create: `/Users/eason/playground/context-checkpoint/hooks/precompact_checkpoint.py`
- Test: `/Users/eason/playground/context-checkpoint/tests/test_precompact.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_precompact.py`:
```python
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import precompact_checkpoint as pc


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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_precompact -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'precompact_checkpoint'`

- [ ] **Step 3: Implement**

Create `hooks/precompact_checkpoint.py`:
```python
"""PreCompact hook: write a bounded session checkpoint. Stdlib only."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkpoint_common as cc


def build_git_state(project_root):
    def git(*args):
        return subprocess.run(
            ["git", "-C", project_root, *args],
            capture_output=True, text=True, timeout=10,
        )
    try:
        r = git("rev-parse", "--is-inside-work-tree")
        if r.returncode != 0 or r.stdout.strip() != "true":
            return "(not a git repository)"
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        status = git("status", "--short").stdout.strip() or "(clean)"
        commits = git("log", "--oneline", "-5").stdout.strip() or "(no commits)"
        return f"Branch: {branch}\n\nStatus:\n{status}\n\nRecent commits:\n{commits}"
    except (OSError, subprocess.SubprocessError):
        return "(git unavailable)"
```

- [ ] **Step 4: Run, verify pass**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_precompact -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/eason/playground/context-checkpoint && git add hooks/precompact_checkpoint.py tests/test_precompact.py && git commit -m "feat: precompact git state"
```

---

### Task 5: `precompact_checkpoint.py` — transcript extraction

**Files:**
- Modify: `/Users/eason/playground/context-checkpoint/hooks/precompact_checkpoint.py`
- Test: `/Users/eason/playground/context-checkpoint/tests/test_precompact.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_precompact.py` (before `if __name__`):
```python
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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_precompact -v`
Expected: FAIL — `AttributeError: ... has no attribute 'extract_recent_transcript'`

- [ ] **Step 3: Implement**

Append to `hooks/precompact_checkpoint.py`:
```python
import json


def _content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                parts.append(f"[tool_use: {block.get('name', '?')}]")
            elif btype == "tool_result":
                c = block.get("content", "")
                size = len(c) if isinstance(c, str) else len(json.dumps(c))
                parts.append(f"[tool_result: {size}B]")
        return "\n".join(p for p in parts if p)
    return ""


def extract_recent_transcript(transcript_path, n, msg_max, total_max):
    if not transcript_path or not os.path.exists(transcript_path):
        return "(transcript unavailable)"
    msgs = []
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in ("user", "assistant"):
                    continue
                text = _content_to_text(message.get("content")).strip()
                if not text:
                    continue
                msgs.append(f"**{role}:** {cc.truncate(text, msg_max)}")
    except OSError:
        return "(transcript unavailable)"
    recent = msgs[-n:]
    if not recent:
        return "(no recent messages)"
    while len(recent) > 1 and len("\n\n".join(recent)) > total_max:
        recent.pop(0)
    body = "\n\n".join(recent)
    if len(recent) < len(msgs[-n:]):
        body = "(…earlier messages trimmed…)\n\n" + body
    return cc.truncate(body, total_max)
```

- [ ] **Step 4: Run, verify pass**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_precompact -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/eason/playground/context-checkpoint && git add hooks/precompact_checkpoint.py tests/test_precompact.py && git commit -m "feat: bounded recent transcript extraction"
```

---

### Task 6: `precompact_checkpoint.py` — remember, carry-forward, build, main

**Files:**
- Modify: `/Users/eason/playground/context-checkpoint/hooks/precompact_checkpoint.py`
- Test: `/Users/eason/playground/context-checkpoint/tests/test_precompact.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_precompact.py` (before `if __name__`):
```python
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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_precompact -v`
Expected: FAIL — `AttributeError: ... has no attribute 'carry_forward'` (and the e2e tests fail because the `.sh` does not exist yet — that is fixed in this task's Step 3/Step 5)

- [ ] **Step 3: Implement Python pieces**

Append to `hooks/precompact_checkpoint.py`:
```python
from datetime import datetime


def read_remember(project_root, maxchars):
    path = os.path.join(project_root, ".remember", "remember.md")
    if not os.path.exists(path):
        return "(none)"
    try:
        with open(path) as f:
            return cc.truncate(f.read().strip(), maxchars) or "(none)"
    except OSError:
        return "(none)"


def carry_forward(prev_md):
    result = {
        "Current Goal": cc.PLACEHOLDER,
        "Current State": cc.PLACEHOLDER,
        "Next Steps": cc.PLACEHOLDER,
    }
    if not prev_md:
        return result
    for name in result:
        body = cc.get_section(prev_md, name).strip()
        if body and body != cc.PLACEHOLDER:
            result[name] = body
    return result


def build_checkpoint(trigger, project_root, git_state, transcript, remember, carried):
    ts = datetime.now().isoformat(timespec="seconds")
    return (
        "# Session Checkpoint\n"
        f"Updated: {ts} | Trigger: {trigger} | Project: {project_root}\n\n"
        f"## Current Goal\n{carried['Current Goal']}\n\n"
        f"## Current State\n{carried['Current State']}\n\n"
        f"## Git State\n{git_state}\n\n"
        f"## Recent Transcript\n{transcript}\n\n"
        f"## Remembered Notes\n{remember}\n\n"
        f"## Next Steps\n{carried['Next Steps']}\n"
    )


def main():
    data = cc.read_hook_input(sys.stdin.read())
    project_root = cc.resolve_project_root(data)
    trigger = data.get("trigger") or data.get("matcher_value") or "unknown"
    transcript_path = data.get("transcript_path")

    n = cc.get_int("CC_RECENT_MSGS", 12)
    msg_max = cc.get_int("CC_MSG_MAXCHARS", 1200)
    tr_max = cc.get_int("CC_TRANSCRIPT_MAXCHARS", 8000)
    rem_max = cc.get_int("CC_REMEMBER_MAXCHARS", 2000)

    checkpoint_path = os.path.join(project_root, ".agent", "session-checkpoint.md")
    prev_md = ""
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path) as f:
                prev_md = f.read()
        except OSError:
            prev_md = ""

    content = build_checkpoint(
        trigger,
        project_root,
        build_git_state(project_root),
        extract_recent_transcript(transcript_path, n, msg_max, tr_max),
        read_remember(project_root, rem_max),
        carry_forward(prev_md),
    )
    cc.atomic_write(checkpoint_path, content)
    cc.log(project_root, f"PreCompact checkpoint written (trigger={trigger})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create the bash wrapper**

Create `hooks/precompact-checkpoint.sh`:
```bash
#!/usr/bin/env bash
# Non-blocking PreCompact wrapper. Must never block compaction.
set -u
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT="$(cat)"
if [ "${CONTEXT_CHECKPOINT_STRICT:-}" = "1" ]; then
  printf '%s' "$INPUT" | python3 "$HOOK_DIR/precompact_checkpoint.py"
  exit $?
fi
printf '%s' "$INPUT" | python3 "$HOOK_DIR/precompact_checkpoint.py" \
  2>>"${HOME}/.claude/hooks/.wrapper-error.log" || true
exit 0
```

Then make it executable:
```bash
chmod +x /Users/eason/playground/context-checkpoint/hooks/precompact-checkpoint.sh
```

- [ ] **Step 5: Run, verify pass**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_precompact -v`
Expected: PASS (11 tests total in this file)

- [ ] **Step 6: Commit**

```bash
cd /Users/eason/playground/context-checkpoint && git add hooks/precompact_checkpoint.py hooks/precompact-checkpoint.sh tests/test_precompact.py && git commit -m "feat: precompact build/main + non-blocking wrapper"
```

---

### Task 7: `sessionstart_restore.py` — pointer + bounded restore payload

**Files:**
- Create: `/Users/eason/playground/context-checkpoint/hooks/sessionstart_restore.py`
- Test: `/Users/eason/playground/context-checkpoint/tests/test_sessionstart.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_sessionstart.py`:
```python
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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_sessionstart -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sessionstart_restore'`

- [ ] **Step 3: Implement**

Create `hooks/sessionstart_restore.py`:
```python
"""SessionStart hook: inject the saved checkpoint (bounded). Stdlib only."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkpoint_common as cc

INSTRUCTION = (
    "Resuming from checkpoint. Reconstruct Current Goal / Current State / "
    "Next Steps from the Git State and Recent Transcript below before continuing.\n\n"
)
_MARKER = "\n(truncated — full checkpoint at .agent/session-checkpoint.md)"


def build_pointer(md):
    ts = "unknown"
    for line in md.splitlines():
        if line.startswith("Updated:"):
            ts = line[len("Updated:"):].split("|")[0].strip()
            break
    return (
        f"A session checkpoint exists at .agent/session-checkpoint.md "
        f"(updated {ts}). Read it if continuing prior work."
    )


def build_restore_payload(md, restore_max):
    full = INSTRUCTION + md
    if len(full) <= restore_max:
        return full
    header = "## Recent Transcript\n"
    idx = md.find(header)
    if idx == -1:
        return cc.truncate(full, restore_max, _MARKER)
    body_start = idx + len(header)
    next_idx = md.find("\n## ", body_start)
    if next_idx == -1:
        next_idx = len(md)
    before = md[:body_start]
    after = md[next_idx:]
    budget = restore_max - len(INSTRUCTION) - len(before) - len(after) - len(_MARKER)
    if budget < 0:
        budget = 0
    new_rt = md[body_start:body_start + budget].rstrip() + _MARKER
    return INSTRUCTION + before + new_rt + after
```

- [ ] **Step 4: Run, verify pass**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_sessionstart -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/eason/playground/context-checkpoint && git add hooks/sessionstart_restore.py tests/test_sessionstart.py && git commit -m "feat: sessionstart pointer + bounded restore payload"
```

---

### Task 8: `sessionstart_restore.py` — main + wrapper + e2e

**Files:**
- Modify: `/Users/eason/playground/context-checkpoint/hooks/sessionstart_restore.py`
- Create: `/Users/eason/playground/context-checkpoint/hooks/sessionstart-restore.sh`
- Test: `/Users/eason/playground/context-checkpoint/tests/test_sessionstart.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_sessionstart.py` (before `if __name__`):
```python
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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_sessionstart -v`
Expected: FAIL — wrapper `sessionstart-restore.sh` does not exist / `main` not defined

- [ ] **Step 3: Implement main**

Append to `hooks/sessionstart_restore.py`:
```python
def emit(additional_context):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }))


def main():
    data = cc.read_hook_input(sys.stdin.read())
    project_root = cc.resolve_project_root(data)
    source = data.get("source", "")
    checkpoint_path = os.path.join(project_root, ".agent", "session-checkpoint.md")
    if not os.path.exists(checkpoint_path):
        return
    try:
        with open(checkpoint_path) as f:
            md = f.read()
    except OSError:
        return
    if source == "startup":
        emit(build_pointer(md))
    elif source in ("compact", "resume", "clear"):
        emit(build_restore_payload(md, cc.get_int("CC_RESTORE_MAXCHARS", 6000)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create the bash wrapper**

Create `hooks/sessionstart-restore.sh`:
```bash
#!/usr/bin/env bash
# Non-blocking SessionStart wrapper. Emits JSON on stdout only on success.
set -u
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT="$(cat)"
if [ "${CONTEXT_CHECKPOINT_STRICT:-}" = "1" ]; then
  printf '%s' "$INPUT" | python3 "$HOOK_DIR/sessionstart_restore.py"
  exit $?
fi
printf '%s' "$INPUT" | python3 "$HOOK_DIR/sessionstart_restore.py" \
  2>>"${HOME}/.claude/hooks/.wrapper-error.log" || true
exit 0
```

Then:
```bash
chmod +x /Users/eason/playground/context-checkpoint/hooks/sessionstart-restore.sh
```

- [ ] **Step 5: Run, verify pass**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_sessionstart -v`
Expected: PASS (6 tests total in this file)

- [ ] **Step 6: Commit**

```bash
cd /Users/eason/playground/context-checkpoint && git add hooks/sessionstart_restore.py hooks/sessionstart-restore.sh tests/test_sessionstart.py && git commit -m "feat: sessionstart main + non-blocking wrapper"
```

---

### Task 9: `merge_settings.py` — non-destructive, idempotent settings merge

**Files:**
- Create: `/Users/eason/playground/context-checkpoint/hooks/merge_settings.py`
- Test: `/Users/eason/playground/context-checkpoint/tests/test_merge_settings.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_merge_settings.py`:
```python
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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_merge_settings -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'merge_settings'`

- [ ] **Step 3: Implement**

Create `hooks/merge_settings.py`:
```python
"""Idempotent, non-destructive merge of context-checkpoint hooks into settings.json."""
import json
import os
import sys

HOOKS = {
    "PreCompact": "precompact-checkpoint.sh",
    "SessionStart": "sessionstart-restore.sh",
}


def merge(settings, hooks_dir):
    settings = dict(settings or {})
    settings.setdefault("hooks", {})
    for event, script in HOOKS.items():
        entries = settings["hooks"].setdefault(event, [])
        already = any(
            script in h.get("command", "")
            for entry in entries
            for h in entry.get("hooks", [])
        )
        if not already:
            entries.append({"hooks": [{
                "type": "command",
                "command": f'bash "{hooks_dir}/{script}"',
            }]})
    return settings


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.claude/settings.json")
    hooks_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/.claude/hooks")
    settings = {}
    if os.path.exists(path):
        with open(path) as f:
            settings = json.load(f)
    merged = merge(settings, hooks_dir)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(merged, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, verify pass**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest tests.test_merge_settings -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/eason/playground/context-checkpoint && git add hooks/merge_settings.py tests/test_merge_settings.py && git commit -m "feat: non-destructive idempotent settings merge"
```

---

### Task 10: Installer + full suite + manual integration verification

**Files:**
- Create: `/Users/eason/playground/context-checkpoint/install.sh`

- [ ] **Step 1: Create installer**

Create `install.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HOME}/.claude/hooks"
mkdir -p "$DEST"
cp "$SRC/hooks/checkpoint_common.py" \
   "$SRC/hooks/precompact_checkpoint.py" \
   "$SRC/hooks/sessionstart_restore.py" \
   "$SRC/hooks/precompact-checkpoint.sh" \
   "$SRC/hooks/sessionstart-restore.sh" \
   "$DEST/"
chmod +x "$DEST/precompact-checkpoint.sh" "$DEST/sessionstart-restore.sh"
python3 "$SRC/hooks/merge_settings.py" "${HOME}/.claude/settings.json" "$DEST"
echo "Installed context-checkpoint hooks to $DEST and merged ~/.claude/settings.json"
```

Then:
```bash
chmod +x /Users/eason/playground/context-checkpoint/install.sh
```

- [ ] **Step 2: Run the FULL test suite (all green before installing)**

Run: `cd /Users/eason/playground/context-checkpoint && python3 -m unittest discover -s tests -v`
Expected: PASS — all tests across the 4 test files (≈28 tests), 0 failures.

- [ ] **Step 3: Back up settings.json, then install**

Run:
```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak.$(date +%s) && bash /Users/eason/playground/context-checkpoint/install.sh
```
Expected: "Installed context-checkpoint hooks…". (Backup created in case you want to revert.)

- [ ] **Step 4: Verify settings.json merge preserved everything**

Run: `python3 -c "import json;d=json.load(open('$HOME/.claude/settings.json'));print('plugins:',len(d.get('enabledPlugins',{})));print('PreCompact:',d['hooks']['PreCompact']);print('SessionStart count:',len(d['hooks']['SessionStart']))"`
Expected: plugin count unchanged (21), `PreCompact` shows our `precompact-checkpoint.sh`, `effortLevel` still present (verify it prints without KeyError). Confirm no existing keys were dropped versus the `.bak` file:
Run: `diff <(python3 -c "import json;print(sorted(json.load(open('$HOME/.claude/settings.json')).keys()))") <(python3 -c "import json,glob;f=sorted(glob.glob('$HOME/.claude/settings.json.bak.*'))[-1];print(sorted(set(json.load(open(f)).keys())|{'hooks'}))")`
Expected: no differences (same top-level keys; `hooks` is the only addition if it was absent before).

- [ ] **Step 5: Manual end-to-end check (requires a real Claude Code session)**

This is a manual verification — document the result, do not fake it:
1. In any project, run `/compact`. Then check that `<project>/.agent/session-checkpoint.md` exists and contains a `## Git State` and `## Recent Transcript`. Confirm no `.agent/session-checkpoint.md.tmp` is left behind.
2. Run `/clear`. Confirm the next turn shows the injected checkpoint context (the "Resuming from checkpoint…" instruction). 
3. Confirm the `remember` plugin still runs at SessionStart (its `=== REMEMBER ===` banner still appears) — i.e., both SessionStart hooks coexist.

If any step fails, set `CONTEXT_CHECKPOINT_STRICT=1` and re-run to surface the error, and check `~/.claude/hooks/.wrapper-error.log` and `<project>/.agent/context-checkpoint.log`.

- [ ] **Step 6: Commit installer**

```bash
cd /Users/eason/playground/context-checkpoint && git add install.sh && git commit -m "feat: installer (copy hooks + merge settings)"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §5 hook pair → Tasks 6 (PreCompact wrapper+main), 8 (SessionStart wrapper+main).
- §6 checkpoint format → Task 6 `build_checkpoint` (all 6 sections asserted).
- §7 PreCompact logic (git/transcript/remember/carry-forward/atomic) → Tasks 4, 5, 6.
- §8 restore (pointer on startup; capped on compact/resume/clear; truncation marker) → Tasks 7, 8.
- §9 robustness (non-blocking exit 0; STRICT) → wrappers in Tasks 6/8; verification in Task 10 Step 5.
- §10 config env vars → read via `cc.get_int` in mains (Tasks 6, 8).
- §11 wiring **merge not overwrite** → Task 9 (`merge`, idempotent, preserves keys) + Task 10 Step 4 diff check.
- §12 limitations → documented; bare-`/clear` behavior reflected (restore uses whatever checkpoint exists).
- §13 extensibility → `carry_forward`/`build_checkpoint` isolated; restore path independent.
- §14 testing → unit + e2e per module; full suite Task 10 Step 2; manual integration Step 5.
- §15 success criteria → asserted across e2e tests + manual checks.

**Placeholder scan:** none — every code/test step contains complete code; every run step has an exact command + expected result.

**Type/name consistency:** function names consistent across tasks — `read_hook_input`, `get_int`, `resolve_project_root`, `truncate`, `atomic_write`, `split_checkpoint`, `get_section` (common); `build_git_state`, `extract_recent_transcript`, `_content_to_text`, `read_remember`, `carry_forward`, `build_checkpoint`, `main` (precompact); `build_pointer`, `build_restore_payload`, `INSTRUCTION`, `emit`, `main` (sessionstart); `merge`, `main` (merge_settings). Section names (`Current Goal`/`Current State`/`Git State`/`Recent Transcript`/`Remembered Notes`/`Next Steps`) identical in writer (Task 6) and reader (Tasks 7/8) and carry-forward (Task 6).
