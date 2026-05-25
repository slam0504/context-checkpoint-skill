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
    window = msgs[-n:]
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
    git_max = cc.get_int("CC_GIT_MAXCHARS", 2000)
    jud_max = cc.get_int("CC_JUDGMENT_MAXCHARS", 2000)

    checkpoint_path = os.path.join(project_root, ".agent", "session-checkpoint.md")
    prev_md = ""
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path) as f:
                prev_md = f.read()
        except OSError:
            prev_md = ""

    carried = carry_forward(prev_md)
    for k in carried:
        carried[k] = cc.truncate(carried[k], jud_max)

    content = build_checkpoint(
        trigger,
        project_root,
        cc.truncate(build_git_state(project_root), git_max),
        extract_recent_transcript(transcript_path, n, msg_max, tr_max),
        read_remember(project_root, rem_max),
        carried,
    )
    cc.atomic_write(checkpoint_path, content)
    cc.log(project_root, f"PreCompact checkpoint written (trigger={trigger})")


if __name__ == "__main__":
    main()
