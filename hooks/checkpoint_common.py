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
