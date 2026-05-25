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
printf '%s' "$INPUT" | python3 "$HOOK_DIR/precompact_checkpoint.py" \
  2>>"${HOME}/.claude/hooks/.wrapper-error.log" || true
exit 0
