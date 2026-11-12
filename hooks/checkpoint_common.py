"""Shared helpers for context-checkpoint hooks (stdlib only)."""
import json
import os
import re
from datetime import datetime, timedelta

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


def max_age(name, default_hours):
    # A timedelta holds at most 999999999 days. A longer setting means "never
    # expires"; it must not crash the hook.
    limit = timedelta.max.days * 24
    return timedelta(hours=max(-limit, min(limit, get_int(name, default_hours))))


def resolve_project_root(data):
    return (
        (data or {}).get("cwd")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )


def resolve_checkpoint_root(data):
    # CLAUDE_PROJECT_DIR stays at the directory the session started in, while
    # the hook's cwd follows every cd; the checkpoint must not move with it.
    return os.environ.get("CLAUDE_PROJECT_DIR") or resolve_project_root(data)


def session_checkpoint_path(root, session_id):
    # Sessions started in one directory share session-checkpoint.md, and
    # compaction takes minutes, so another session can overwrite it before the
    # first one reads it back. Each session also keeps its own copy.
    # The id comes from stdin, so only a plain token may become a file name.
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return None
    return os.path.join(root, ".agent", "session-checkpoints", session_id + ".md")


def truncate(text, maxchars, marker="\n…(truncated)…"):
    if text is None:
        return ""
    if len(text) <= maxchars:
        return text
    keep = max(0, maxchars - len(marker))
    return text[:keep] + marker


def tail(text, maxchars, marker="(…earlier notes trimmed…)\n"):
    # Notes are appended over time, so the newest entries sit at the end.
    if len(text) <= maxchars:
        return text
    keep = max(0, maxchars - len(marker))
    return marker + text[len(text) - keep:]


def log(project_root, msg):
    try:
        d = os.path.join(project_root, ".agent")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "context-checkpoint.log"), "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


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


def parse_header(md):
    for line in (md or "").splitlines():
        if line.startswith("Updated:"):
            header = {}
            for part in line.split(" | "):
                key, sep, value = part.partition(": ")
                if sep:
                    header[key] = value.strip()
            return header
    return {}
