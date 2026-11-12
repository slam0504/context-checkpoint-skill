"""PreCompact hook: write a bounded session checkpoint. Stdlib only."""
import os
import re
import subprocess
import sys
import time

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


import json


def _message_text(content):
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
    else:
        return ""
    # <system-reminder> is text the harness injects (often next to a
    # tool_result), not something the user or assistant said.
    parts = [p for p in parts if not p.lstrip().startswith("<system-reminder>")]
    return "\n".join(p for p in parts if p).strip()


def _tool_names(content):
    if not isinstance(content, list):
        return []
    return [
        b.get("name") or "?" for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]


def _tool_summary(names):
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return "[tool_use: " + ", ".join(
        f"{n} ×{c}" if c > 1 else n for n, c in counts.items()
    ) + "]"


def _demote_headings(text):
    # The checkpoint is split on lines starting with "## " (see
    # checkpoint_common.split_checkpoint and the Recent Transcript trimming in
    # sessionstart_restore). Embedded text that carries its own "## " headings
    # would be read back as checkpoint sections, so push those down a level.
    return "\n".join(
        "#" + line if line.startswith("## ") else line
        for line in text.split("\n")
    )


def extract_recent_transcript(transcript_path, n, msg_max, total_max):
    if not transcript_path or not os.path.exists(transcript_path):
        return "(transcript unavailable)"
    # Each entry is ("text", rendered) or ("tools", [names]). Tool-only
    # assistant messages are the bulk of a transcript, so consecutive ones are
    # folded into one entry instead of each taking a slot in the window.
    entries = []
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
                if obj.get("isMeta") or obj.get("isCompactSummary"):
                    continue
                # Other line types (api-request-blob) carry a copy of a
                # message that is already logged as type user/assistant.
                if obj.get("type") not in (None, "user", "assistant"):
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = message.get("content")
                text = _message_text(content)
                tools = _tool_names(content)
                if text:
                    entry = f"**{role}:** {cc.truncate(_demote_headings(text), msg_max)}"
                    if tools:
                        entry += "\n" + _tool_summary(tools)
                    entries.append(("text", entry))
                elif tools and role == "assistant":
                    if entries and entries[-1][0] == "tools":
                        entries[-1][1].extend(tools)
                    else:
                        entries.append(("tools", list(tools)))
    except OSError:
        return "(transcript unavailable)"
    text_idx = [i for i, (kind, _) in enumerate(entries) if kind == "text"]
    if len(text_idx) > n:
        entries = entries[text_idx[-n:][0]:]
    window = [
        v if kind == "text" else f"**assistant:** {_tool_summary(v)}"
        for kind, v in entries
    ]
    if not window:
        return "(no recent messages)"
    recent = list(window)
    while len(recent) > 1 and len("\n\n".join(recent)) > total_max:
        recent.pop(0)
    body = "\n\n".join(recent)
    if len(recent) < len(window):
        body = "(…earlier messages trimmed…)\n\n" + body
    return cc.truncate(body, total_max)


from datetime import datetime


def _main_checkout(base):
    # Same rule as the remember plugin, which keeps a linked worktree's notes
    # in the main checkout.
    if os.path.isdir(os.path.join(base, ".git")):
        return base
    try:
        r = subprocess.run(
            ["git", "-C", base, "rev-parse", "--path-format=absolute",
             "--git-common-dir", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        lines = r.stdout.splitlines()
        if r.returncode != 0 or len(lines) < 2 or lines[0] == lines[1]:
            return base
        main = os.path.dirname(lines[0])
        if os.path.isdir(main) and subprocess.run(
            ["git", "-C", main, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, timeout=5,
        ).returncode == 0:
            return main
    except (OSError, subprocess.SubprocessError):
        pass
    return base


def read_remember(project_root, maxchars):
    # The remember plugin keeps its store under CLAUDE_PROJECT_DIR (in the main
    # checkout when that is a linked worktree), while project_root follows the
    # hook's cwd, which drifts once the session cd's elsewhere. now.md is the
    # plugin's live buffer; it is empty right after being folded into the
    # daily file, so fall back to today's. (remember.md is the manual
    # /remember handoff, which the plugin delivers itself.)
    today = f"today-{datetime.now():%Y-%m-%d}.md"
    bases = []
    for root in (os.environ.get("CLAUDE_PROJECT_DIR"), project_root):
        if root:
            bases += [_main_checkout(root), root]
    for base in dict.fromkeys(bases):
        for name in ("now.md", today):
            try:
                with open(os.path.join(base, ".remember", name)) as f:
                    text = f.read().strip()
            except (OSError, ValueError):
                continue
            if text:
                return cc.tail(_demote_headings(text), maxchars)
    return "(none)"


def carry_forward(prev_md, session_id):
    result = {
        "Current Goal": cc.PLACEHOLDER,
        "Current State": cc.PLACEHOLDER,
        "Next Steps": cc.PLACEHOLDER,
    }
    # Judgment sections describe one session's work; another session's would
    # mislead this one.
    if not prev_md or not session_id or cc.parse_header(prev_md).get("Session") != session_id:
        return result
    for name in result:
        body = cc.get_section(prev_md, name).strip()
        if body and body != cc.PLACEHOLDER:
            result[name] = body
    return result


def build_checkpoint(trigger, project_root, git_state, transcript, remember, carried,
                     session_id="unknown", cwd=None):
    ts = datetime.now().isoformat(timespec="seconds")
    sections = [
        ("Current Goal", carried["Current Goal"]),
        ("Current State", carried["Current State"]),
        ("Git State", git_state),
        ("Recent Transcript", transcript),
        ("Remembered Notes", remember),
        ("Next Steps", carried["Next Steps"]),
    ]
    # This hook has no model, so a judgment section it could not carry forward
    # holds only the placeholder. Leave those out: they say nothing and eat
    # into the restore budget. sessionstart_restore already tells the reader
    # to rebuild any missing section.
    body = "\n\n".join(
        f"## {name}\n{text}" for name, text in sections if text != cc.PLACEHOLDER
    )
    header = f"Updated: {ts} | Trigger: {trigger} | Session: {session_id} | Project: {project_root}"
    if cwd and cwd != project_root:
        header += f" | Cwd: {cwd}"
    return f"# Session Checkpoint\n{header}\n\n{body}\n"


def prune_session_checkpoints(root, keep_path, max_age):
    d = os.path.join(root, ".agent", "session-checkpoints")
    cutoff = time.time() - max_age.total_seconds()
    try:
        for name in os.listdir(d):
            path = os.path.join(d, name)
            # ".tmp" is what atomic_write leaves behind when a write fails.
            if (path == keep_path or not re.fullmatch(r"[A-Za-z0-9_-]+\.md(\.tmp)?", name)
                    or not os.path.isfile(path)):
                continue
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def main():
    data = cc.read_hook_input(sys.stdin.read())
    cwd = cc.resolve_project_root(data)
    root = cc.resolve_checkpoint_root(data)
    session_id = data.get("session_id") or "unknown"
    trigger = data.get("trigger") or data.get("matcher_value") or "unknown"
    transcript_path = data.get("transcript_path")

    n = cc.get_int("CC_RECENT_MSGS", 12)
    msg_max = cc.get_int("CC_MSG_MAXCHARS", 1200)
    tr_max = cc.get_int("CC_TRANSCRIPT_MAXCHARS", 8000)
    rem_max = cc.get_int("CC_REMEMBER_MAXCHARS", 2000)
    git_max = cc.get_int("CC_GIT_MAXCHARS", 2000)
    jud_max = cc.get_int("CC_JUDGMENT_MAXCHARS", 2000)

    checkpoint_path = os.path.join(root, ".agent", "session-checkpoint.md")
    session_path = cc.session_checkpoint_path(root, data.get("session_id"))
    prev_path = session_path if session_path and os.path.exists(session_path) else checkpoint_path
    prev_md = ""
    if os.path.exists(prev_path):
        try:
            with open(prev_path) as f:
                prev_md = f.read()
        except OSError:
            prev_md = ""

    carried = carry_forward(prev_md, data.get("session_id"))
    for k in carried:
        carried[k] = cc.truncate(carried[k], jud_max)

    content = build_checkpoint(
        trigger,
        root,
        cc.truncate(build_git_state(cwd), git_max),
        extract_recent_transcript(transcript_path, n, msg_max, tr_max),
        read_remember(cwd, rem_max),
        carried,
        session_id,
        cwd,
    )
    if session_path:
        try:
            cc.atomic_write(session_path, content)
        except OSError as e:
            # The copy is an addition; without it the shared file still works.
            cc.log(root, f"per-session checkpoint not written: {e}")
    cc.atomic_write(checkpoint_path, content)
    # A copy can be read back for as long as either window allows.
    keep = max(cc.max_age("CC_RESTORE_MAX_AGE_HOURS", 24),
               cc.max_age("CC_POINTER_MAX_AGE_HOURS", 168))
    prune_session_checkpoints(root, session_path, keep)
    cc.log(root, f"PreCompact checkpoint written (trigger={trigger}, session={session_id})")


if __name__ == "__main__":
    main()
