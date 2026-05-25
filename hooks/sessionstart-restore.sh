#!/usr/bin/env bash
# Non-blocking SessionStart wrapper. Emits JSON on stdout only on success.
set -u
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT="$(cat)"
if [ "${CONTEXT_CHECKPOINT_STRICT:-}" = "1" ]; then
  printf '%s' "$INPUT" | python3 "$HOOK_DIR/sessionstart_restore.py"
  exit $?
fi
mkdir -p "${HOME}/.claude/hooks" 2>/dev/null || true
printf '%s' "$INPUT" | python3 "$HOOK_DIR/sessionstart_restore.py" \
  2>>"${HOME}/.claude/hooks/.wrapper-error.log" || true
exit 0
