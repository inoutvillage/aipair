<!-- aipair:start -->
# AI Pair: Codex × Claude (tmux `aipair`)

When this session is launched by `aipair`, the env var `AI_PEER` is set to `claude`
and a sibling **Claude Code** agent is working in the **same directory**. You can read
its live transcript to coordinate — avoid duplicate work, pick up its findings, hand off:

- `peer` — the peer agent's recent transcript (Claude's, here)
- `peer --watch` — follow it live
- `peer-log claude` — explicit form; `peer-log both` shows both merged by time
- `peer-log claude --last 60 --tools` — more history, including its tool activity

After a relay has ended, a new review round can be started on demand with `aipair-relay-here`
(works from this pane too; it adopts the running pair and refuses to double-start).

Notes:
- Communication is **pull-based**, not push: you can't (and don't need to) type into Claude's
  pane — Claude reads *your* transcript with `peer`. In an **`aipair loop`** session a relay
  auto-prompts Claude after each of your turns. So just do the work and state your result
  plainly at the end of your turn; **never ask the user to relay/forward a message to Claude**
  ("tell Claude X" is wrong — Claude can read it itself).
- Check `peer` before starting a non-trivial task in an `aipair` session, so you and
  Claude don't both do the same thing.
- Outside an `aipair` session `AI_PEER` is unset and `peer` falls back to showing both logs.

Full docs: the README of the aipair repository (https://github.com/inoutvillage/aipair).
<!-- aipair:end -->
