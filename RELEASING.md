# Releasing aipair

aipair uses [Semantic Versioning](https://semver.org/). The **single source of truth** for the
version is `bin/aipairlib/__version__` (read by `aipair --version` and `aipair-relay --version`).
A release is a git tag **`v<version>`** plus its GitHub Release; the tag must equal `__version__`.

`tests/doc-sync.py` enforces the invariants below, so a mismatch fails CI before you can tag.

## Cutting a release

1. **Bump the version** in `bin/aipairlib/__init__.py` (`__version__ = "X.Y.Z"`), choosing the
   part per SemVer (MAJOR for breaking, MINOR for features, PATCH for fixes). Pre-1.0 (`0.y.z`)
   makes no stability promise.
2. **Move `## [Unreleased]` changes into a `## [X.Y.Z] - <YYYY-MM-DD>` section** in `CHANGELOG.md`
   (JST date), and add the `[X.Y.Z]` / `[Unreleased]` compare links at the bottom.
3. Commit both in one commit: `release: vX.Y.Z`.
4. Open a PR and let CI pass (doc-sync checks `__version__` == CHANGELOG's top released version and
   that `aipair --version` / `aipair-relay --version` report it). Merge.
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

## Notes

- The tag drives the release; nothing is published until you push `vX.Y.Z`. Do not tag until the
  version + CHANGELOG commit is on `main`.
- `corelib.TESTED_VERSIONS` (the verified claude/codex CLI versions) is **independent** of aipair's
  own version — bump it only when you re-verify against new upstream CLIs (see the README「必要環境」
  table, also doc-sync-checked).
