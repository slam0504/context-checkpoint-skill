"""SessionStart hook: inject the saved checkpoint (bounded). Stdlib only."""
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkpoint_common as cc

INSTRUCTION = (
    "Resuming from checkpoint. First use Current Goal / Current State / "
    "Next Steps as the handoff. If any of those sections are missing or marked "
    "as placeholders, reconstruct them from Git State and Recent Transcript "
    "before continuing.\n\n"
)
# The checkpoint sits under CLAUDE_PROJECT_DIR, which is not always the cwd a
# reader would resolve a relative path against, so main() passes the full path.
_MARKER = "\n(truncated — full checkpoint at {path})"


NOTES_WARNING = (
    "Remembered Notes is a log shared by every session started in this project "
    "directory, so some entries may come from other sessions.\n\n"
)
# scripts/remember-branch.sh makes the remember plugin end each note heading
# with the first 8 characters of the id of the session it summarised, for ids
# that start with 8 lowercase hex digits; this pattern has to accept the same
# ids. Where that script is not wired in, no heading carries a tag and the
# sentence only says that any entry may be another session's.
NOTES_WARNING_TAGGED = (
    "Remembered Notes is a log shared by every session started in this project "
    "directory. A heading ending in {tag} marks an entry from this session; "
    "any other entry may come from another session.\n\n"
)
_TAGGABLE_SESSION = re.compile(r"[0-9a-f]{8}")
_MESSAGE_START = re.compile(r"\n\n(?=\*\*(?:user|assistant):\*\* )")


def format_age(age):
    minutes = int(age.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} d"


def build_pointer(path, header, age, same_session):
    ts = header.get("Updated", "unknown")
    who = "this session" if same_session else "another session"
    return (
        f"A session checkpoint exists at {path} "
        f"(updated {ts}, {format_age(age)} ago, written by {who}). "
        f"Read it only if you are continuing that work."
    )


def _latest_messages(body, budget):
    """Keep whole messages, newest first, within budget; flag if any were dropped."""
    messages = _MESSAGE_START.split(body)
    kept = []
    used = 0
    for msg in reversed(messages):
        cost = len(msg) + (2 if kept else 0)
        if used + cost > budget:
            break
        kept.append(msg)
        used += cost
    return kept[::-1], messages


def notes_warning(md):
    # main() only injects a checkpoint written by the session reading it, so
    # the header's Session is "this session".
    session = cc.parse_header(md).get("Session", "")
    if _TAGGABLE_SESSION.match(session):
        return NOTES_WARNING_TAGGED.format(tag=session[:8])
    return NOTES_WARNING


def build_restore_payload(md, restore_max, path=".agent/session-checkpoint.md"):
    notes = cc.get_section(md, "Remembered Notes")
    lead = INSTRUCTION + (notes_warning(md) if notes and notes != "(none)" else "")
    full = lead + md
    if len(full) <= restore_max:
        return full
    marker = _MARKER.format(path=path)
    preamble, sections = cc.split_checkpoint(md)
    bodies = []
    for name, body in sections:
        if name == "Remembered Notes":
            body = cc.tail(body, restore_max // 4)
        elif name != "Recent Transcript":
            body = cc.truncate(body, restore_max // 8)
        bodies.append(body)

    def assemble():
        text = "\n\n".join(
            f"## {name}\n{body}" for (name, _), body in zip(sections, bodies)
        )
        return lead + preamble + "\n\n" + text + marker

    for i, (name, body) in enumerate(sections):
        if name != "Recent Transcript":
            continue
        bodies[i] = ""
        budget = max(0, restore_max - len(assemble()))
        kept, messages = _latest_messages(body, budget)
        if len(kept) == len(messages):
            bodies[i] = "\n\n".join(kept)
            continue
        prefix = "(…earlier messages trimmed…)\n\n"
        budget = max(0, budget - len(prefix))
        kept, _ = _latest_messages(body, budget)
        kept = kept or [cc.truncate(messages[-1], budget)]
        bodies[i] = prefix + "\n\n".join(kept)
    # Hard backstop: the fixed sections can alone exceed the cap.
    return cc.truncate(assemble(), restore_max, marker)


def emit(additional_context):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }))


def checkpoint_age(header, path):
    try:
        return max(timedelta(0), datetime.now() - datetime.fromisoformat(header["Updated"]))
    except (KeyError, ValueError, TypeError):
        pass
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return timedelta(0)
    return max(timedelta(0), datetime.now() - mtime)


def read_checkpoint(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def main():
    data = cc.read_hook_input(sys.stdin.read())
    source = data.get("source", "")
    root = cc.resolve_checkpoint_root(data)
    session_id = data.get("session_id")
    checkpoint_path = os.path.abspath(os.path.join(root, ".agent", "session-checkpoint.md"))
    md = read_checkpoint(checkpoint_path)
    own_path = cc.session_checkpoint_path(root, session_id)
    own_md = read_checkpoint(own_path) if own_path else None
    if own_md is not None:
        # precompact writes the shared file even when the copy cannot be
        # written, which leaves an earlier copy beside a newer checkpoint of
        # this same session. Only then is the shared file the one to read.
        shared = cc.parse_header(md)
        if not (shared.get("Session") == session_id
                and shared.get("Updated", "") > cc.parse_header(own_md).get("Updated", "")):
            checkpoint_path, md = os.path.abspath(own_path), own_md
    if md is None:
        return
    header = cc.parse_header(md)
    age = checkpoint_age(header, checkpoint_path)
    same_session = bool(session_id) and header.get("Session") == session_id
    restore_max_age = cc.max_age("CC_RESTORE_MAX_AGE_HOURS", 24)
    pointer_max_age = cc.max_age("CC_POINTER_MAX_AGE_HOURS", 168)
    if source in ("compact", "resume", "clear") and same_session and age <= restore_max_age:
        emit(build_restore_payload(md, cc.get_int("CC_RESTORE_MAXCHARS", 6000), checkpoint_path))
    elif source in ("startup", "compact", "resume", "clear") and age <= pointer_max_age:
        emit(build_pointer(checkpoint_path, header, age, same_session))


if __name__ == "__main__":
    main()
