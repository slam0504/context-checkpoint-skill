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
