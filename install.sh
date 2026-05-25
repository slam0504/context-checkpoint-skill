#!/usr/bin/env bash
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HOME}/.claude/hooks"
mkdir -p "$DEST"
cp "$SRC/hooks/checkpoint_common.py" \
   "$SRC/hooks/precompact_checkpoint.py" \
   "$SRC/hooks/sessionstart_restore.py" \
   "$SRC/hooks/precompact-checkpoint.sh" \
   "$SRC/hooks/sessionstart-restore.sh" \
   "$DEST/"
chmod +x "$DEST/precompact-checkpoint.sh" "$DEST/sessionstart-restore.sh"
python3 "$SRC/hooks/merge_settings.py" "${HOME}/.claude/settings.json" "$DEST"
echo "Installed context-checkpoint hooks to $DEST and merged ~/.claude/settings.json"
