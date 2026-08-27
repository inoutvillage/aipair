# Releasing aipair

aipair uses [Semantic Versioning](https://semver.org/). The **single source of truth** for the
version is `bin/aipairlib/__version__` (read by `aipair --version` and `aipair-relay --version`).
A release is a git tag **`v<version>`** plus its GitHub Release; the tag must equal `__version__`.

**Between releases, main carries a development version** — the version being prepared plus the
SemVer prerelease suffix `-dev.N`, e.g. `0.2.0-dev.0` (CEO decision 2026-08-27). A main checkout
therefore never claims to BE a release, and `X.Y.Z-dev.N` sorts before `X.Y.Z`. Only the **release
commit** carries a bare `X.Y.Z`, and that is the commit the `vX.Y.Z` tag points at. The CHANGELOG
heading always names the *release* being prepared (`## [X.Y.Z] — unreleased`) — never the `-dev.N`
form. doc-sync pins which version form may appear in which CHANGELOG state, so a half-done bump
fails CI. (PEP 440's `0.2.0.dev0` was rejected: aipair is not published to PyPI and its whole
version contract — doc-sync's `SEMVER`, `release.yml`'s tag check — is SemVer 2.0.0.)

`tests/doc-sync.py` enforces the invariants below, so a mismatch fails CI before you can tag.

## Cutting a release

1. **Confirm the version to release, and drop the `-dev.N` suffix.** Between releases `__version__`
   is `X.Y.Z-dev.N`; the release commit sets it to the bare `X.Y.Z` (this is the *only* commit that
   carries a bare version — doc-sync requires the bare form to come with the dated CHANGELOG below).
   The prepared version and
   has its own `## [X.Y.Z] — unreleased` section in `CHANGELOG.md` (right after a release the top is a
   fresh `## [Unreleased]` above the last dated version — start the next cycle first, see below) (it was set when this cycle started — see
   "Starting the next version"). If the scope changed the intended number, update `__version__` **and**
   that heading together now (SemVer: MAJOR breaking / MINOR features / PATCH fixes; pre-1.0 `0.y.z`
   makes no stability promise). No bump happens here otherwise.
2. In `CHANGELOG.md`, **date the top section**: change `## [X.Y.Z] — unreleased (…)` to
   `## [X.Y.Z] - <YYYY-MM-DD>` (JST release date), add a fresh `## [Unreleased]` above it for the next
   cycle, and fix the bottom link definitions: `[X.Y.Z]` → the **tag/release URL**
   (`…/releases/tag/vX.Y.Z`) and add `[Unreleased]` → the **compare URL** (`…/compare/vX.Y.Z...HEAD`).
   This dated section is what the tag publishes (`release.yml` refuses to publish an undated section).
   Also update the **README stable install line(s)** — the `git clone --branch vX.Y.Z …` in the Quick
   Start and the install section — to the new tag (doc-sync's
   `test_readme_stable_branch_tracks_the_latest_release` fails until the README tag matches the newest
   dated CHANGELOG release).
3. Commit these in one commit: `release: vX.Y.Z`.
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
`__version__ = "<NEXT_VERSION>-dev.0"` (the `-dev.N` suffix — bump `N` only if you ever need to
distinguish dev states within one cycle), and **rename the `## [Unreleased]` section** (created by the
release step above) **to `## [<NEXT_VERSION>] — unreleased (…)`** — and rename its bottom link key
from `[Unreleased]` to `[<NEXT_VERSION>]` (it becomes the tag URL when NEXT_VERSION ships) — in the
same commit, so `__version__` (minus its `-dev.N` suffix) always has its own top version section
(doc-sync enforces this). This is where `__version__` is
**normally** bumped; the only exception is step 1, which adjusts it together with the heading if the
release scope changed the intended number.

## Notes

- The tag drives the release; nothing is published until you push `vX.Y.Z`. Do not tag until the
  version + CHANGELOG commit is on `main`.
- `corelib.TESTED_VERSIONS` (the verified claude/codex CLI versions) is **independent** of aipair's
  own version — bump it only when you re-verify against new upstream CLIs (see the README「必要環境」
  table, also doc-sync-checked).
