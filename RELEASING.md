# Releasing aipair

aipair uses [Semantic Versioning](https://semver.org/). The **single source of truth** for the
version is `bin/aipairlib/__version__` (read by `aipair --version` and `aipair-relay --version`).
A release is a git tag **`v<version>`** plus its GitHub Release; the tag must equal `__version__`.

`tests/doc-sync.py` enforces the invariants below, so a mismatch fails CI before you can tag.

## Cutting a release

1. **Confirm the version to release.** `__version__` is already the *prepared* version and has its
   own `## [X.Y.Z] — unreleased` section in `CHANGELOG.md` (it was set when this cycle started — see
   "Starting the next version"). If the scope changed the intended number, update `__version__` **and**
   that heading together now (SemVer: MAJOR breaking / MINOR features / PATCH fixes; pre-1.0 `0.y.z`
   makes no stability promise). No bump happens here otherwise.
2. In `CHANGELOG.md`, **date the top section**: change `## [X.Y.Z] — unreleased (…)` to
   `## [X.Y.Z] - <YYYY-MM-DD>` (JST release date), add a fresh `## [Unreleased]` above it for the next
   cycle, and keep the `[X.Y.Z]` compare link at the bottom. This dated section is what the tag publishes.
3. Commit both in one commit: `release: vX.Y.Z`.
4. Open a PR and let CI pass (doc-sync checks `__version__` == the CHANGELOG top version section and
   that `aipair --version` / `aipair-relay --version` report it). Merge — this is the release commit.
5. **Tag the merge commit and push the tag**:
   ```sh
   git checkout main && git pull
   git tag -a "vX.Y.Z" -m "aipair vX.Y.Z"
   git push origin "vX.Y.Z"
   ```
6. Pushing the `v*` tag triggers `.github/workflows/release.yml`, which re-checks
   `tag == __version__`, extracts the matching `CHANGELOG.md` section as the notes, and creates the
   **GitHub Release**. (If Actions is unavailable, run `gh release create vX.Y.Z --notes-file <section>`
   by hand.)

## Starting the next version

After a release, choose the next version `NEXT_VERSION` per SemVer — e.g. `0.1.1` for a patch,
`0.2.0` for features, `1.0.0` for a stable/breaking release — set `bin/aipairlib/__init__.py`
`__version__ = "<NEXT_VERSION>"`, and **rename the `## [Unreleased]` section** (created by the
release step above) **to `## [<NEXT_VERSION>] — unreleased (…)`** in the same commit, so `__version__`
always has its own top version section (doc-sync enforces this). This is the only place `__version__`
is bumped.

## Notes

- The tag drives the release; nothing is published until you push `vX.Y.Z`. Do not tag until the
  version + CHANGELOG commit is on `main`.
- `corelib.TESTED_VERSIONS` (the verified claude/codex CLI versions) is **independent** of aipair's
  own version — bump it only when you re-verify against new upstream CLIs (see the README「必要環境」
  table, also doc-sync-checked).
