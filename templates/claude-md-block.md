<!-- aipair:start -->
## AI Pair: Claude × Codex (tmux `aipair`)

When this session is launched by `aipair`, the env var `AI_PEER` is set to `codex`
and a sibling **Codex CLI** agent is working in the **same directory**. You can read
its live transcript to coordinate — avoid duplicate work, pick up its findings, hand off:

- `peer` — the peer agent's recent transcript (Codex's, here)
- `peer --watch` — follow it live
- `peer-log codex` — explicit form; `peer-log both` shows both merged by time
- `peer-log codex --last 60 --tools` — more history, including its tool activity

Session control (each takes an optional `[dir]`, default `$PWD`):
`aipair` start / re-attach · `aipair loop` start with the autonomous review relay ·
`aipair attach` attach only · `aipair stop` kill it · `aipair name` print the tmux session name.

`aipair loop` runs `aipair-relay --max-rounds 20` and stops when **codex** emits the stop phrase
`[AIPAIR_REVIEW_OK]` (defaults; override with `AIPAIR_MAX_ROUNDS` / `AIPAIR_STOP` / `AIPAIR_STOP_SIDE` —
precedence: CLI flag > env > built-in default; the relay itself and `aipair-relay-here` read the
same env). By default each agent keeps its normal permission prompts; permission-bypass
(`claude --dangerously-skip-permissions` / `codex --dangerously-bypass-approvals-and-sandbox`)
is opt-in via `--unsafe` / `AIPAIR_UNSAFE=1` and is required for `aipair loop`;
for interactive launch, `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS` set the flags entirely (empty = none, e.g. `AIPAIR_CODEX_FLAGS= aipair`), but under `aipair loop` the bypass flags are always present and cannot be removed — yours are appended.

**Endless mode** (`AIPAIR_ENDLESS=1` / `--endless`): the stop phrase means "this task passed review,
move on" instead of "stop". When Claude runs out of work it writes `[AIPAIR_NEXT]` at the start
of its reply, and Codex assigns the next unchecked item from `tasks/todo.md` (`AIPAIR_TASK_LIST`).
The loop ends **only** when Codex declares `[AIPAIR_ALL_DONE]` (`--max-rounds` is just a safety cap — set it
high).

After a relay has ended and the back-and-forth has stopped, a new round can be started on demand
with **`aipair-relay-here`** (from the claude or codex pane; it adopts the running pair with
`--adopt` and refuses to double-start). From Claude this is also available as the `aipair-relay`
skill. On-demand only — nothing restarts automatically.

Notes:
- Communication is **pull-based**, not push: you can't (and don't need to) type into Codex's
  pane — Codex reads *your* transcript with `peer`. In an **`aipair loop`** session a relay
  auto-prompts Codex after each of your turns. So just do the work and state your result
  plainly at the end of your turn; **never ask the user to relay/forward a message to Codex**
  ("tell Codex X" is wrong — Codex can read it itself).
- Check `peer` before starting a non-trivial task in an `aipair` session, so you and Codex
  don't both do the same thing.
- Running tmux in a **test** while inside a pair: target a private server
  (`tmux -L <name>` or `-S <sock>`). Inside a pane `$TMUX` overrides `TMUX_TMPDIR`, so a
  bare `tmux kill-server` (or a spawn-then-kill test on the default server) will kill the
  **live pair** — never run `kill-server` un-targeted; verify `#{socket_path}` first.
- Outside an `aipair` session `AI_PEER` is unset and `peer` falls back to showing both logs.

Full docs: the README of the aipair repository (https://github.com/inoutvillage/aipair).
<!-- aipair:end -->
