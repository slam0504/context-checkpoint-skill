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
SETTINGS="${HOME}/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
  BAK="${SETTINGS}.bak.$(date +%s)"
  cp "$SETTINGS" "$BAK"
  echo "Backed up settings to $BAK"
fi
python3 "$SRC/hooks/merge_settings.py" "$SETTINGS" "$DEST"
echo "Installed context-checkpoint hooks to $DEST and merged ~/.claude/settings.json"
