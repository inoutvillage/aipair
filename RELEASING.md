# Releasing aipair

aipair uses [Semantic Versioning](https://semver.org/). The **single source of truth** for the
version is `bin/aipairlib/__version__` (read by `aipair --version` and `aipair-relay --version`).
A release is a git tag **`v<version>`** plus its GitHub Release; the tag must equal `__version__`.

`tests/doc-sync.py` enforces the invariants below, so a mismatch fails CI before you can tag.

## Cutting a release

1. **Confirm the version to release.** Between releases `__version__` is the *prepared* version, a
   **development pre-release of the next target** `X.Y.Z-dev.N` (P2-2 — so `aipair --version` on main
   is never mistaken for a shipped `X.Y.Z`), with its own `## [X.Y.Z-dev.N] — unreleased` section in
   `CHANGELOG.md`. **Drop the `-dev.N` pre-release now**: set `__version__` to the final `X.Y.Z` **and**
   rename that heading together (SemVer: MAJOR breaking / MINOR features / PATCH fixes; pre-1.0 `0.y.z`
   makes no stability promise; NB SemVer pre-release is a hyphen `-dev.0`, not PEP 440 `.dev0`). If the
   scope also changed the intended number, pick the new `X.Y.Z` here.
2. In `CHANGELOG.md`, **date the top section**: change `## [X.Y.Z-dev.N] — unreleased (…)` to
   `## [X.Y.Z] - <YYYY-MM-DD>` (JST release date — this drops the `-dev.N` suffix), add a fresh
   `## [Unreleased]` above it for the next cycle, and fix the bottom link definitions: rename
   `[X.Y.Z-dev.N]` → `[X.Y.Z]` pointing at the **tag/release URL** (`…/releases/tag/vX.Y.Z`) and add
   `[Unreleased]` → the **compare URL** (`…/compare/vX.Y.Z...HEAD`).
   This dated section is what the tag publishes (`release.yml` refuses to publish an undated section).
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

After a release, choose the next target `NEXT_VERSION` per SemVer — e.g. `0.1.1` for a patch,
`0.2.0` for features, `1.0.0` for a stable/breaking release — and set main to its **development
pre-release**: `bin/aipairlib/__init__.py` `__version__ = "<NEXT_VERSION>-dev.0"` (P2-2 — the `-dev.0`
keeps a main checkout's `aipair --version` distinct from the shipped `<NEXT_VERSION>`), and **rename
the `## [Unreleased]` section** (created by the release step above) **to
`## [<NEXT_VERSION>-dev.0] — unreleased (…)`** — and rename its bottom link key from `[Unreleased]` to
`[<NEXT_VERSION>-dev.0]` — in the same commit, so `__version__` always has its own top version section
(doc-sync enforces this). This is where `__version__` is **normally** bumped; the only exception is
step 1, which drops the `-dev.N` (and adjusts the number if the release scope changed it) at release.
Bump `-dev.N` → `-dev.(N+1)` if you want to mark a fresh development checkpoint mid-cycle (optional).

## Notes

- The tag drives the release; nothing is published until you push `vX.Y.Z`. Do not tag until the
  version + CHANGELOG commit is on `main`.
- `corelib.TESTED_VERSIONS` (the verified claude/codex CLI versions) is **independent** of aipair's
  own version — bump it only when you re-verify against new upstream CLIs (see the README「必要環境」
  table, also doc-sync-checked).
