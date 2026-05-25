# context-checkpoint — Design Spec (A1)

**Date:** 2026-05-25
**Status:** Approved for planning
**Scope:** A1 (zero-cost deterministic hook pair). A2 (Haiku distillation) and A3 (manual skill + nudge) are explicitly deferred but designed for.

---

## 1. Problem

Claude Code compacts the conversation when the context window fills. Two pains:

1. **Context loss** — the built-in summarizer drops detail; work-in-progress state gets lost.
2. **No clean resume** — after a compaction or a `/clear`, there's no durable, structured record of "what we were doing and what's next."

The user also wanted compaction to be *faster*. **That is not achievable** — no hook can alter or speed up Claude's built-in summarizer. This design instead makes compaction **lossless** (save our own durable file, re-inject it afterwards) and enables a fast **clear-and-resume** path that sidesteps the summarizer.

A prior idea was "make it a skill." Rejected as the primary mechanism: a skill is inert until invoked, and the model does not reliably invoke it on its own at the compaction moment. The reliable trigger is a **hook**.

## 2. Goals

- Auto-save a durable, **bounded** session checkpoint at the compaction moment (both auto-compact and manual `/compact`).
- Auto-restore that checkpoint into context after `compact` / `resume` / `clear`.
- Zero API cost; works offline; works in non-git directories.
- Never trap or block the user during compaction.
- Structured so A2/A3 drop in without touching the restore path.

## 3. Non-Goals (A1)

- Speeding up Claude's built-in compaction (impossible via hooks).
- Haiku/LLM distillation of the transcript (A2).
- A manually-invoked `context-checkpoint` skill (A3).
- A "context getting full" proactive nudge (A3).
- Rolling checkpoint history, deep `.remember` merge.

## 4. Verified Platform Facts (Claude Code hooks)

Confirmed against Claude Code hook docs before designing:

- `PreCompact` fires on **both** automatic compaction (context full) and manual `/compact`. Distinguishes via a trigger field (`trigger`, value `auto`/`manual`; some versions expose `matcher_value`). Stdin JSON includes `session_id`, `transcript_path`, `cwd`.
- A `PreCompact` hook **cannot** influence or speed up the compaction summary. It can only run a side-effect script (or block compaction). → We must save our **own** file.
- There is **no** token-budget / "80% full" hook and **no** token count exposed to hooks. `PreCompact` is the earliest reliable "context full" signal.
- `SessionStart` fires with `source` ∈ {`startup`, `resume`, `clear`, `compact`} and can inject content into the model's context via `hookSpecificOutput.additionalContext`. This is how the checkpoint is re-fed after a compaction or clear.
- Command hooks have full system access (git, python, file IO), 600s timeout, JSON output capped at 10,000 chars.
- Multiple `SessionStart` hooks from plugins + settings **aggregate** and all run — so our restore hook coexists with the already-installed `remember` plugin's `SessionStart` and `PostToolUse` hooks.

## 5. Architecture

Two global command hooks registered in `~/.claude/settings.json`. Scripts live globally; checkpoints are written **per-project** into the project's `.agent/` directory.

| Hook | Script | Fires on | Responsibility |
|------|--------|----------|----------------|
| `PreCompact` | `$HOME/.claude/hooks/precompact-checkpoint.sh` | auto-compact + manual `/compact` | Generate + atomically write `<project>/.agent/session-checkpoint.md` |
| `SessionStart` | `$HOME/.claude/hooks/sessionstart-restore.sh` | `compact` / `resume` / `clear` / `startup` | Read checkpoint, inject a **bounded** payload via `additionalContext` |

```
PreCompact (context full or /compact)
        │  stdin JSON: cwd, transcript_path, trigger
        ▼
precompact-checkpoint.sh
        │  build sections → write .tmp → mv (atomic)
        ▼
<project>/.agent/session-checkpoint.md
        ▲
        │  read + cap to restore budget
SessionStart (source = compact|resume|clear|startup)
        │  emit hookSpecificOutput.additionalContext
        ▼
model context on resume  →  model reconstructs Goal/State/Next
```

**Project root resolution (both scripts):** stdin JSON `cwd` → fallback `$CLAUDE_PROJECT_DIR` → fallback `$PWD`.

Scripts are `bash` entrypoints that call `python3` (confirmed available) for JSON/JSONL parsing. Registered command uses an absolute/`$HOME` path, not a bare `~`, to avoid expansion issues.

## 6. Checkpoint File Format

Written to `<project>/.agent/session-checkpoint.md`:

```
# Session Checkpoint
Updated: <ISO-8601 timestamp> | Trigger: auto|manual | Project: <cwd>

## Current Goal
<carried forward from prior checkpoint, else: "(reconstruct from Recent Transcript on resume)">

## Current State
<carried forward from prior checkpoint, else placeholder>

## Git State
<branch + `git status --short` + `git log --oneline -5`, OR "(not a git repository)">

## Recent Transcript
<last N user/assistant messages, each trimmed; tool blocks collapsed to markers; section capped>

## Remembered Notes
<contents of <project>/.remember/remember.md if present, trimmed; else "(none)">

## Next Steps
<carried forward from prior checkpoint, else placeholder>
```

**Judgment sections (`Current Goal` / `Current State` / `Next Steps`) — honest A1 boundary:** the hook has no model, so it cannot author these. The script **carries them forward** verbatim from the previous checkpoint (so a future A3 manual-skill run persists across compactions), or writes a placeholder. On restore, the model is instructed to **reconstruct** them from `Git State` + `Recent Transcript`.

## 7. PreCompact Script Logic

1. Read stdin JSON; extract `cwd`, `transcript_path`, trigger (`trigger` ?? `matcher_value` ?? "unknown").
2. Resolve `project_root`; `checkpoint_dir = project_root/.agent`; `mkdir -p`.
3. If a prior `session-checkpoint.md` exists, parse out the `Current Goal` / `Current State` / `Next Steps` section bodies to carry forward.
4. **Git State:** if `git -C <project_root> rev-parse --is-inside-work-tree` succeeds → branch + `status --short` + `log --oneline -5`; else `(not a git repository)`.
5. **Recent Transcript:** parse `transcript_path` (JSONL). Take the last `CC_RECENT_MSGS` user/assistant text messages. Per message: strip/collapse tool_use & tool_result blocks to short markers (e.g. `[tool: Bash]`, `[tool result: 1.2KB]`); truncate to `CC_MSG_MAXCHARS`. Enforce total section cap `CC_TRANSCRIPT_MAXCHARS` (oldest-first drop, with a `(…earlier trimmed…)` marker).
6. **Remembered Notes:** read `<project_root>/.remember/remember.md` if present, truncate to `CC_REMEMBER_MAXCHARS`.
7. Assemble markdown (Section 6).
8. **Atomic write (tweak 1):** write to `session-checkpoint.md.tmp`, then `mv` over `session-checkpoint.md`. `SessionStart` never sees a partial file.
9. Log one line to `<project_root>/.agent/context-checkpoint.log`. `exit 0`.

## 8. SessionStart Restore Script Logic

1. Read stdin JSON; extract `source`, `cwd`. Resolve `project_root`.
2. `checkpoint = project_root/.agent/session-checkpoint.md`. If missing → emit nothing, `exit 0`.
3. If `source == "startup"` → inject a **one-line pointer** only: `A session checkpoint exists at .agent/session-checkpoint.md (updated <ts>). Read it if continuing prior work.` (Avoids stale noise on fresh starts.)
4. Else (`compact` / `resume` / `clear`) → build injection:
   - Prepend reconstruct instruction: `Resuming from checkpoint. Reconstruct Current Goal / Current State / Next Steps from the Git State and Recent Transcript below before continuing.`
   - Append checkpoint content, **hard-capped at `CC_RESTORE_MAXCHARS` (tweak 3)**. If over budget, keep header + Goal/State/Git State/Next Steps + instruction, and **truncate the Recent Transcript tail** with `(truncated — full checkpoint at .agent/session-checkpoint.md)`. The checkpoint *file* may be fuller; the **injected** payload stays bounded.
5. Emit:
   ```json
   {"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"<payload>"}}
   ```
6. Errors → log + `exit 0`.

## 9. Robustness (tweak 2)

- **Non-blocking by default:** both scripts wrap all work; on any error they log to `<project>/.agent/context-checkpoint.log` and **`exit 0`**. The checkpoint system must never trap the user during compaction or block a session start.
- **Strict/debug mode:** if `CONTEXT_CHECKPOINT_STRICT=1`, errors `exit` non-zero (for debugging). If `CONTEXT_CHECKPOINT_DEBUG=1`, verbose logging.
- Guard every external dependency: missing `python3`, missing/malformed transcript, unreadable `.remember`, non-git dir, missing `.agent` (create it). None may crash the hook.

## 10. Configuration (env vars, with baked-in defaults)

| Var | Default | Meaning |
|-----|---------|---------|
| `CC_RECENT_MSGS` | 12 | # of recent user/assistant messages captured |
| `CC_MSG_MAXCHARS` | 1200 | per-message truncation in Recent Transcript |
| `CC_TRANSCRIPT_MAXCHARS` | 8000 | cap of Recent Transcript section **in the file** |
| `CC_REMEMBER_MAXCHARS` | 2000 | cap of Remembered Notes section |
| `CC_RESTORE_MAXCHARS` | 6000 | hard cap of **injected** additionalContext payload |
| `CONTEXT_CHECKPOINT_STRICT` | unset | non-zero exit on error when `=1` |
| `CONTEXT_CHECKPOINT_DEBUG` | unset | verbose logging when `=1` |

## 11. settings.json Wiring

In `~/.claude/settings.json` (coexists with the `remember` plugin's own hooks):

```json
{
  "hooks": {
    "PreCompact": [
      { "hooks": [ { "type": "command", "command": "bash \"$HOME/.claude/hooks/precompact-checkpoint.sh\"" } ] }
    ],
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "bash \"$HOME/.claude/hooks/sessionstart-restore.sh\"" } ] }
    ]
  }
}
```

## 12. Known Limitations (fail-loud)

- **Bare `/clear` does not fire `PreCompact`.** A clean "save-then-clear" requires `/compact` first (which saves), or the A3 manual skill (later). A bare `/clear` still restores whatever checkpoint already exists.
- Judgment sections are reconstructed by the model on resume, not authored by the hook (A2 improves this).
- `additionalContext` is bounded, so very long sessions surface a pointer to the full file rather than the whole transcript.
- Compaction speed is unchanged (platform limitation).

## 13. Extensibility (A2 / A3)

- The "generate judgment sections" step is an **isolated function** in the PreCompact script. **A2** swaps in a Haiku call there (the installed `remember` plugin already proves an API path works in this environment). **A3** adds a manual `context-checkpoint` skill (writes the same file) + an optional `Stop`/`UserPromptSubmit` nudge hook.
- **The restore path (`sessionstart-restore.sh`) and the checkpoint file format never change** across A1→A2→A3.

## 14. Testing Strategy

**Unit (feed sample JSON via stdin):**
- PreCompact: creates `.agent/session-checkpoint.md`; **no `.tmp` left behind** (atomicity); all sections present; git fallback in non-repo; transcript/message/restore caps respected; prior Goal/State/Next carried forward across two consecutive runs; trigger value recorded.
- SessionStart: correct `additionalContext` JSON shape; restore cap respected with truncation marker; `startup` → pointer only; missing checkpoint → no output + exit 0; each `source` value handled.

**Failure injection:** malformed JSON, missing `transcript_path`, missing `python3` → exit 0 + logged; `CONTEXT_CHECKPOINT_STRICT=1` → non-zero exit.

**Integration (manual):** real `/compact` → inspect file; then `/clear` → confirm injected restore + reconstruct instruction appear; confirm `remember` plugin hooks still run.

## 15. Success Criteria

1. `PreCompact` writes a valid, bounded, **atomically-replaced** checkpoint on both triggers; **never blocks** compaction.
2. `SessionStart` injects a **bounded** payload on `compact`/`resume`/`clear`, a pointer on `startup`, and is silent when no checkpoint exists.
3. Zero API cost; functions in non-git directories; coexists with the `remember` plugin.
4. On resume, the model can reconstruct Goal/State/Next from the injected Git State + Recent Transcript.
