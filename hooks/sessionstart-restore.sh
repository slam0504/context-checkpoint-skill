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
LOG="${HOME}/.claude/hooks/.wrapper-error.log"
if ! { : >>"$LOG"; } 2>/dev/null; then
  LOG="/tmp/context-checkpoint-wrapper-error.log"
fi
printf '%s' "$INPUT" | python3 "$HOOK_DIR/sessionstart_restore.py" 2>>"$LOG" || true
exit 0
