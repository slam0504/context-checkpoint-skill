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
