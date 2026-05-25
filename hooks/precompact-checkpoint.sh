#!/usr/bin/env bash
# Non-blocking PreCompact wrapper. Must never block compaction.
set -u
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT="$(cat)"
if [ "${CONTEXT_CHECKPOINT_STRICT:-}" = "1" ]; then
  printf '%s' "$INPUT" | python3 "$HOOK_DIR/precompact_checkpoint.py"
  exit $?
fi
mkdir -p "${HOME}/.claude/hooks" 2>/dev/null || true
LOG="${HOME}/.claude/hooks/.wrapper-error.log"
if ! { : >>"$LOG"; } 2>/dev/null; then
  LOG="/tmp/context-checkpoint-wrapper-error.log"
fi
printf '%s' "$INPUT" | python3 "$HOOK_DIR/precompact_checkpoint.py" 2>>"$LOG" || true
exit 0
