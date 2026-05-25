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
