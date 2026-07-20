# context-checkpoint

Auto-save a durable, bounded **session checkpoint** right before Claude Code compacts the
conversation, and auto-restore it after a compaction, `/clear`, or session resume — so context
survives compaction and you can `/clear` and keep going.

It does **not** speed up Claude's built-in compaction (no hook can). What it does is make
compaction **lossless**: your own checkpoint file is written at the compaction moment and
re-injected into context afterwards.

## How it works

Two global Claude Code hooks (zero API cost, stdlib Python only):

| Hook | Fires on | What it does |
|------|----------|--------------|
| `PreCompact` | auto-compaction (context full) **and** manual `/compact` | Writes `<project>/.agent/session-checkpoint.md` |
| `SessionStart` | `compact` / `resume` / `clear` / `startup` | Injects the checkpoint back into context via `additionalContext` |

- The checkpoint is written **per project**, into that project's `.agent/` directory.
- On `compact` / `resume` / `clear`, the **full** (bounded) checkpoint is injected with a one-line
  instruction to reconstruct the judgment sections. On `startup`, only a **one-line pointer** is
  injected to avoid stale noise.
- The injected restore payload is **hard-capped** at `CC_RESTORE_MAXCHARS`, even when the git /
  judgment / remembered sections are individually large (the Recent Transcript is trimmed first, then
  the whole payload is truncated as a backstop). The checkpoint *file* itself is also bounded: git
  state and carried-forward judgment sections are capped.
- Writes are **atomic** (`.tmp` + `os.replace`), so a restore never reads a partial file.
- Hooks are **non-blocking by default**: any error is logged and the hook still exits 0, so the
  checkpoint system can never trap you during compaction. Set `CONTEXT_CHECKPOINT_STRICT=1` to make
  errors surface (non-zero exit) for debugging. The wrapper error log lives at
  `~/.claude/hooks/.wrapper-error.log`, falling back to `/tmp/context-checkpoint-wrapper-error.log`
  if that path isn't writable — so Python always runs regardless.

### Checkpoint file format

`<project>/.agent/session-checkpoint.md`:

```
# Session Checkpoint
Updated: <timestamp> | Trigger: auto|manual | Project: <cwd>

## Current Goal       # carried forward from prior checkpoint, else placeholder
## Current State      # carried forward from prior checkpoint, else placeholder
## Git State          # branch + status --short + log -5, or "(not a git repository)"
## Recent Transcript  # last ~12 user/assistant messages, trimmed & capped
                      # (tool-result-only messages are skipped and don't count)
## Remembered Notes   # copy of .remember/remember.md if present
## Next Steps         # carried forward from prior checkpoint, else placeholder
```

**Honest boundary:** a hook has no model, so `Current Goal` / `Current State` / `Next Steps` are
not authored by the hook. They are **carried forward** from the previous checkpoint (so a future
manual checkpoint persists), or left as a placeholder. On restore, the model reconstructs them from
the Git State + Recent Transcript.

## Install

Requires `python3` and `git`. The installer copies the hook files to `~/.claude/hooks/` and
**merges** (never overwrites) `~/.claude/settings.json`, preserving existing plugins, settings, and
hooks. If `settings.json` already exists, it is **backed up** to `settings.json.bak.<timestamp>`
before the merge.

```bash
git clone git@github.com:slam0504/context-checkpoint-skill.git
cd context-checkpoint-skill
bash install.sh
```

The merge is additive — it only adds `PreCompact` and `SessionStart` entries, and is idempotent
(re-running won't duplicate them). It coexists with other plugins' hooks (e.g. the `remember`
plugin's own `SessionStart`).

To revert, restore the automatic backup and remove the copied files:

```bash
# restore the most recent pre-install backup:
cp "$(ls -t ~/.claude/settings.json.bak.* | head -1)" ~/.claude/settings.json
# remove the copied hook files:
rm ~/.claude/hooks/{checkpoint_common,precompact_checkpoint,sessionstart_restore}.py \
   ~/.claude/hooks/{precompact-checkpoint,sessionstart-restore}.sh
```

## Verify (manual, in a real session)

The hooks only fire inside a live Claude Code session, so confirm end-to-end manually:

1. In any project with some work, run `/compact`. Check `<project>/.agent/session-checkpoint.md`
   exists with a populated `## Git State` and `## Recent Transcript`, and no `*.md.tmp` left behind.
2. Run `/clear`. On the next turn, the injected context should begin with
   *"Resuming from checkpoint. Reconstruct Current Goal / Current State / Next Steps…"*.
3. If anything misbehaves: re-run with `CONTEXT_CHECKPOINT_STRICT=1`, and check
   `~/.claude/hooks/.wrapper-error.log` and `<project>/.agent/context-checkpoint.log`.

## Configuration

All optional; sensible defaults are baked in. Override via environment variables:

| Var | Default | Meaning |
|-----|---------|---------|
| `CC_RECENT_MSGS` | 12 | # of recent user/assistant messages captured (tool-result-only messages excluded — they reduce to `[tool_result: NB]` placeholders and would crowd out real content in tool-heavy sessions) |
| `CC_MSG_MAXCHARS` | 1200 | per-message truncation in Recent Transcript |
| `CC_TRANSCRIPT_MAXCHARS` | 8000 | cap of the Recent Transcript section **in the file** |
| `CC_REMEMBER_MAXCHARS` | 2000 | cap of the Remembered Notes section |
| `CC_GIT_MAXCHARS` | 2000 | cap of the Git State section (large `git status` lists) |
| `CC_JUDGMENT_MAXCHARS` | 2000 | cap of each carried-forward judgment section (Goal/State/Next) |
| `CC_RESTORE_MAXCHARS` | 6000 | hard cap of the **injected** restore payload (kept under Claude's 10k hook output limit) |
| `CONTEXT_CHECKPOINT_STRICT` | unset | non-zero exit on error when `=1` |

## Known limitations

- A bare `/clear` does **not** fire `PreCompact`; it only restores whatever checkpoint already
  exists. For a clean save-then-clear, run `/compact` first.
- The judgment sections are reconstructed by the model on resume, not authored by the hook.
- Compaction speed is unchanged (platform limitation).

## Development

```bash
python3 -m unittest discover -s tests -v
```

Layout:

```
hooks/
  checkpoint_common.py       # shared helpers (input, config, truncation, atomic write, parsing)
  precompact_checkpoint.py   # PreCompact logic
  sessionstart_restore.py    # SessionStart logic
  precompact-checkpoint.sh   # non-blocking wrapper
  sessionstart-restore.sh    # non-blocking wrapper
  merge_settings.py          # install-time settings merge (not copied as a hook)
install.sh
tests/                       # unittest, stdlib only
docs/specs/                  # design spec
docs/plans/                  # implementation plan
```

## Roadmap

This is the **A1** (zero-cost) implementation. The checkpoint generator is isolated so later phases
drop in without changing the restore path:

- **A2** — Haiku distillation: have `PreCompact` summarize the transcript into the judgment sections.
- **A3** — a manual `context-checkpoint` skill + an optional nudge hook to checkpoint while context
  is still intact.
