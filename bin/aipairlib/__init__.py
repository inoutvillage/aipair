"""aipairlib — the importable Python package behind the aipair CLIs (#7: replaces the old
SourceFileLoader-by-path + runtime attribute injection with a normal package of plain imports).

The thin executables `bin/aipair-relay` and `bin/peer-log` add their own directory to sys.path
and `from aipairlib import relay` / `import aipairlib.peerlog`. Python 3.8+ (stdlib only)."""

# Single source of truth for the aipair version (P2-4). `aipair --version` / `aipair-relay --version`
# read this; git tags are `v<__version__>` and CHANGELOG.md's top entry must match it (enforced by
# tests/doc-sync.py). Bump it in the same commit as the CHANGELOG entry.
#
# Between releases main carries a **development version**: the version being prepared plus the SemVer
# prerelease suffix `-dev.N` (P2-2, CEO decision 2026-08-27, 案B). So a main checkout never claims to
# BE a release: `0.2.0-dev.0` sorts BEFORE `0.2.0` in SemVer, and the release commit drops the suffix
# (the only commit that ever carries a bare `X.Y.Z`) right before the `vX.Y.Z` tag. PEP 440's
# `0.2.0.dev0` was considered and rejected: aipair is not published to PyPI, and its whole version
# contract (doc-sync `SEMVER`, `release.yml`'s tag check) is SemVer 2.0.0. See RELEASING.md.
__version__ = "0.2.0-dev.0"
