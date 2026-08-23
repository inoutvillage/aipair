# Changelog

All notable changes to aipair are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and aipair uses
[Semantic Versioning](https://semver.org/). The version below is the single source of truth
`aipairlib.__version__`; a release is the git tag `v<version>` and its GitHub Release (see
[RELEASING.md](RELEASING.md)). The top version section is whatever `__version__` currently is — it
gets a date **when its `v<version>` tag is pushed**; until then it is the release being prepared.
`tests/doc-sync.py` enforces that this section matches `__version__`.

## [0.1.0] — unreleased (prepared; published when the `v0.1.0` tag is pushed)

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

[0.1.0]: https://github.com/inoutvillage/aipair/releases/tag/v0.1.0
