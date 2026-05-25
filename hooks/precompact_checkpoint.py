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
