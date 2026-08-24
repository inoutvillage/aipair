# Changelog

All notable changes to aipair are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and aipair uses
[Semantic Versioning](https://semver.org/). The version below is the single source of truth
`aipairlib.__version__`; a release is the git tag `v<version>` and its GitHub Release (see
[RELEASING.md](RELEASING.md)). The CHANGELOG top is in one of two lifecycle states: **while a
version is being prepared**, a `## [X.Y.Z] — unreleased` section is at the top; **right after a
release**, a `## [Unreleased]` section sits above the dated `## [X.Y.Z] - <date>` just shipped. In
both, the topmost VERSION-numbered section is `aipairlib.__version__`; its date is filled in in the
release commit, just before the `v<version>` tag is pushed. `tests/doc-sync.py` enforces the
version match and verifies both states.

## [Unreleased]

Correctness and safety of the autonomous `aipair loop --endless` relay: a task-list classification —
not an agent's say-so — is the authority on when a run is finished, and the loop stops for a human on
specific detectable conditions (the task-list classifies as `BLOCKED` with only human-dependent `[!]`
tasks left, Codex declares `[AIPAIR_HUMAN_REQUIRED]`, a question is too large to relay intact, or a
task makes no progress across rounds) instead of guessing or spinning.

### Added
- **Task-list classification is the termination authority** — in `--endless`, the relay reads the
  task-list and classifies it (`READY` / `BLOCKED` / `ALL_DONE`); an all-done / human-required
  sentinel is honored only when the classification agrees, so a misfired sentinel can neither end
  nor wrongly continue the loop. Startup classification acts immediately (`ALL_DONE` → exit 0,
  `BLOCKED` → exit 8, `READY` → run).
- **`[!]` blocked-task notation** — a `- [!]` item with a required `blocker: <reason>` child line
  marks work that needs a human or an external dependency; the agents move a task there instead of
  leaving it `- [ ]` or guessing past it.
- **`[AIPAIR_HUMAN_REQUIRED]` sentinel and exit code 8** — when only human-dependent `[!]` tasks
  remain (classification `BLOCKED`), the loop stops for a human (exit 8) instead of spinning. Exit 8
  is distinct from the max-rounds cap and carries a reason.
- **no-progress guard** — if the same task is re-selected three rounds running with an unchanged
  task-list snapshot, the loop stops (exit 8, no-progress) rather than looping forever.
- **AskUserQuestion → HUMAN_REQUIRED** — when Claude stops on a choice dialog whose answer needs
  human approval, authority, secrets, payment, or an irreversible/production action, Codex can
  decline to proxy-answer (`[AIPAIR_HUMAN_REQUIRED]` on its reply's first line); the relay stops
  (exit 8) without answering and leaves the dialog for a human. The task-list is untouched.
- **Distinct exit-8 banners** — the human-wait and no-progress stops each print their own banner
  (naming the blocking item or the question) so it is clear why the loop stopped and what to do.

### Fixed
- **Endless mode no longer marks a task done before review** — the `- [x]` is written only on the
  next turn after Codex's review passes, not at implementation time, so a relay restart mid-review
  can no longer misread the task-list as `ALL_DONE` and stop early.
- **A missing / unreadable / zero-checkbox task-list no longer reads as "all done"** — it now exits
  2 (configuration error) instead of terminating the loop as complete; endless mode requires at
  least one recognized checkbox (`- [ ]` / `- [x]` / `- [!]`).
- **An oversized question is no longer truncated and proxy-answered** — a question whose text
  exceeds the auto-relay size limit stops for a human (exit 8) instead of being cut down and
  answered from an incomplete prompt.
- **A classification-rejected sentinel is no longer forwarded to Claude** — if Codex emits a
  terminal sentinel the task-list classification does not support, the relay re-asks Codex to pick
  the next task instead of sending the rejected turn on to Claude.

## [0.1.0] - 2026-08-23

The initial aipair release. aipair runs Claude Code and Codex CLI side by side in one tmux session with
a live merged "bridge" pane, so each agent can read the other, and can drive an autonomous
mutual-review relay.

### Added
- **Launcher** `aipair` — `aipair [dir]` / `aipair loop` / `aipair attach|stop|name|status`, plus
  `peer` / `peer-log` to read the sibling agent's transcript, and `aipair-relay-here` to re-ignite
  a relay on demand.
- **`aipair --version` / `aipair-relay --version`** reporting `aipairlib.__version__`.
- **Sentinel stop protocol** — stop / next / all-done / plan-approval are dedicated sentinels
  (`[AIPAIR_REVIEW_OK]` / `[AIPAIR_NEXT]` / `[AIPAIR_ALL_DONE]` / `[AIPAIR_PLAN_APPROVED]`) that only
  fire when alone on the final message's leading line — no false stop/approve from negation,
  quotes, or inline mentions.
- **Version gate + JSONL schema gate** — an untested claude/codex keeps the safe relay but loses
  TUI dialog automation; a log-schema drift stops the loop **fail-closed (exit 7)** unless
  `--allow-untested-schema`.
- **Response-attribution gate** — a completed turn is accepted as the answer to our poke only via
  turn_id (codex) / parentUuid ancestry (claude); a turn_id-less codex turn is never position-guessed.
- **Transactional installer** — the aipairlib package, the thin entrypoints, the retired flat libs,
  the skills, the two global notice blocks, and the optional VS Code tasks install as ONE journaled,
  all-or-nothing transaction; a smoke-test failure rolls the whole install back.
- **CI** — a 3-lane matrix (python 3.8 floor / 3.13 current, tmux 3.1 floor) plus a nightly split of
  upstream-latest smoke and an authenticated end-to-end pinned to `corelib.TESTED_VERSIONS`.
- **`tests/doc-sync.py`** — pins README / SECURITY / CI to the code (tested CLI versions, sentinels,
  exit codes, schema protocol, CI matrix, release version).

### Security
- `SECURITY.md` threat model. Permission-bypass is opt-in (`--unsafe`) and required only for
  `aipair loop`; interactive `aipair` keeps each agent's normal permission prompts.

[Unreleased]: https://github.com/inoutvillage/aipair/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/inoutvillage/aipair/releases/tag/v0.1.0
