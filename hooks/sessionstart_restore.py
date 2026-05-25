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
