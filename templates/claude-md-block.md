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
`完了です` (defaults; override with `AIPAIR_MAX_ROUNDS` / `AIPAIR_STOP` / `AIPAIR_STOP_SIDE` —
precedence: CLI flag > env > built-in default; the relay itself and `aipair-relay-here` read the
same env). Both agents launch with permission-bypass flags by default
(`claude --dangerously-skip-permissions` / `codex --dangerously-bypass-approvals-and-sandbox`);
override or disable them with `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS` (e.g. `AIPAIR_CODEX_FLAGS= aipair`).

**Endless mode** (`AIPAIR_ENDLESS=1` / `--endless`): the stop phrase means "this task passed review,
move on" instead of "stop". When Claude runs out of work it writes `次のタスクをください` at the start
of its reply, and Codex assigns the next unchecked item from `tasks/todo.md` (`AIPAIR_TASK_LIST`).
The loop ends **only** when Codex declares `全タスク完了` (`--max-rounds` is just a safety cap — set it
high). 🚫 Do not combine with `aipair-queue` (queue treats relay exit 0 as "one task done").

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
- Outside an `aipair` session `AI_PEER` is unset and `peer` falls back to showing both logs.

Full docs: the README of the aipair repository (https://github.com/inoutvillage/aipair).
<!-- aipair:end -->
