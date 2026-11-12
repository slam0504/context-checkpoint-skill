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
| `PreCompact` | auto-compaction (context full) **and** manual `/compact` | Writes `<project>/.agent/session-checkpoint.md` and a per-session copy |
| `SessionStart` | `compact` / `resume` / `clear` / `startup` | Injects the checkpoint, or a pointer to it, via `additionalContext` |

- `<project>` is `CLAUDE_PROJECT_DIR` (the directory the session started in), falling back to the
  hook's cwd. The checkpoint does not follow later `cd`s; git state is still read from the hook's
  cwd.
- Besides the shared `session-checkpoint.md`, each checkpoint is also written to
  `<project>/.agent/session-checkpoints/<session_id>.md`, because sessions started in the same
  directory share the one file and another session can overwrite it while this one is compacting.
  `SessionStart` reads the session's own copy when it exists, otherwise the shared file. Copies other
  than the current one are deleted once they are older than the larger of
  `CC_RESTORE_MAX_AGE_HOURS` and `CC_POINTER_MAX_AGE_HOURS`.
- The **full** (bounded) checkpoint is injected, with a one-line instruction to reconstruct missing
  judgment sections, only when all three hold: the source is `compact` / `resume` / `clear`, the
  checkpoint was written by the **same session**, and it is no older than
  `CC_RESTORE_MAX_AGE_HOURS`. Otherwise, if the checkpoint is no older than
  `CC_POINTER_MAX_AGE_HOURS` (any of `startup` / `compact` / `resume` / `clear`), only a **one-line
  pointer** with the file path, age and writing session is injected. Beyond that, nothing is injected.
  The age comes from the header's `Updated` time, falling back to the file's mtime.
- The injected restore payload is **hard-capped** at `CC_RESTORE_MAXCHARS`. When the checkpoint is
  larger, Remembered Notes keep at most their newest quarter of the cap, every other fixed section is cut to
  an eighth of it, and the rest of the budget goes to the newest whole Recent Transcript messages. The
  checkpoint *file* itself is also bounded: git state, notes, transcript and carried-forward judgment
  sections are capped.
- If the checkpoint has Remembered Notes, a sentence warning that the notes are shared by every
  session in the project directory is injected ahead of it.
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
Updated: <timestamp> | Trigger: auto|manual | Session: <session_id> | Project: <project> [| Cwd: <cwd>]

## Current Goal       # carried forward from this session's previous checkpoint; left out if none
## Current State      # carried forward from this session's previous checkpoint; left out if none
## Git State          # branch + status --short + log -5, or "(not a git repository)"
## Recent Transcript  # last ~12 messages that have text, plus folded tool-call summaries
## Remembered Notes   # remember plugin's .remember/now.md, else today-YYYY-MM-DD.md; else "(none)"
## Next Steps         # carried forward from this session's previous checkpoint; left out if none
```

`Cwd` appears only when the hook's cwd differs from `<project>`.

- Recent Transcript counts only messages with `user` / `assistant` text. Tool calls are folded into
  one line such as `[tool_use: Bash ×3, Read]` (consecutive tool-only messages merge into one entry
  and do not use up the window). Tool results, `isMeta` / `isCompactSummary` lines, line types other
  than `user` / `assistant`, and `<system-reminder>` text are skipped.
- `## ` headings inside transcript messages and notes are demoted to `###` so they cannot be read back
  as checkpoint sections.
- Remembered Notes come from `CLAUDE_PROJECT_DIR`, then the hook's cwd. When the directory is a
  linked git worktree, the main checkout's `.remember/` is tried first. When the notes exceed
  `CC_REMEMBER_MAXCHARS`, the newest part (the end of the file) is kept. `remember.md` (the manual
  `/remember` handoff) is not read.

**Honest boundary:** a hook has no model, so `Current Goal` / `Current State` / `Next Steps` are
not authored by the hook. They are **carried forward** only from the previous checkpoint of the
**same session** (so a future manual checkpoint persists); a section that is still only the
placeholder is left out of the file. On restore, the model reconstructs missing sections from the
Git State + Recent Transcript.

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
   *"Resuming from checkpoint. First use Current Goal / Current State / Next Steps as the handoff…"*
   (a pointer line instead if the checkpoint is from another session or older than
   `CC_RESTORE_MAX_AGE_HOURS`).
3. If anything misbehaves: re-run with `CONTEXT_CHECKPOINT_STRICT=1`, and check
   `~/.claude/hooks/.wrapper-error.log` and `<project>/.agent/context-checkpoint.log`.

## Configuration

All optional; sensible defaults are baked in. Override via environment variables:

| Var | Default | Meaning |
|-----|---------|---------|
| `CC_RECENT_MSGS` | 12 | # of recent messages with user/assistant text captured (tool-call summaries do not count) |
| `CC_MSG_MAXCHARS` | 1200 | per-message truncation in Recent Transcript |
| `CC_TRANSCRIPT_MAXCHARS` | 8000 | cap of the Recent Transcript section **in the file** |
| `CC_REMEMBER_MAXCHARS` | 2000 | cap of the Remembered Notes section |
| `CC_GIT_MAXCHARS` | 2000 | cap of the Git State section (large `git status` lists) |
| `CC_JUDGMENT_MAXCHARS` | 2000 | cap of each carried-forward judgment section (Goal/State/Next) |
| `CC_RESTORE_MAXCHARS` | 6000 | hard cap of the **injected** restore payload (kept under Claude's 10k hook output limit) |
| `CC_RESTORE_MAX_AGE_HOURS` | 24 | oldest checkpoint (same session) that is still injected in full |
| `CC_POINTER_MAX_AGE_HOURS` | 168 | oldest checkpoint that still gets a one-line pointer; also, with the above, how long per-session copies are kept |
| `CONTEXT_CHECKPOINT_STRICT` | unset | non-zero exit on error when `=1` |

## Known limitations

- A bare `/clear` does **not** fire `PreCompact`; it only restores whatever checkpoint already
  exists. For a clean save-then-clear, run `/compact` first.
- The judgment sections are reconstructed by the model on resume, not authored by the hook, and are
  carried forward only within one session. A different session that starts in the same project gets a
  pointer to the checkpoint, not its contents.
- Remembered Notes is the remember plugin's log for the whole project directory, so it can include
  entries from other sessions (hence the warning injected with it).
- Compaction speed is unchanged (platform limitation).

## Development

```bash
python3 -m unittest discover -s tests -v
```

Layout:

```
hooks/
  checkpoint_common.py       # shared helpers (input, config, paths, truncation, atomic write, header parsing)
  precompact_checkpoint.py   # PreCompact logic
  sessionstart_restore.py    # SessionStart logic
  precompact-checkpoint.sh   # non-blocking wrapper
  sessionstart-restore.sh    # non-blocking wrapper
  merge_settings.py          # install-time settings merge (not copied as a hook)
install.sh
tests/                       # unittest, stdlib only
  test_common.py             # shared helpers
  test_precompact.py         # git state, transcript window, carry-forward, wrapper and caps
  test_precompact_checkpoint.py   # notes, section layout, transcript folding, per-session copies, end to end
  test_sessionstart_restore.py    # full restore vs pointer, ages, per-session copy, oversize cap
  test_install.py            # install.sh with a temporary HOME
  test_merge_settings.py     # settings.json merge
docs/specs/                  # design spec
docs/plans/                  # implementation plan
```

## Roadmap

This is the **A1** (zero-cost) implementation. The checkpoint generator is isolated so later phases
drop in without changing the restore path:

- **A2** — Haiku distillation: have `PreCompact` summarize the transcript into the judgment sections.
- **A3** — a manual `context-checkpoint` skill + an optional nudge hook to checkpoint while context
  is still intact.
