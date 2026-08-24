"""aipairlib — the importable Python package behind the aipair CLIs (#7: replaces the old
SourceFileLoader-by-path + runtime attribute injection with a normal package of plain imports).

The thin executables `bin/aipair-relay` and `bin/peer-log` add their own directory to sys.path
and `from aipairlib import relay` / `import aipairlib.peerlog`. Python 3.8+ (stdlib only)."""

# Single source of truth for the aipair release version (P2-4). `aipair --version` /
# `aipair-relay --version` read this; git tags are `v<__version__>` and CHANGELOG.md's top entry
# must match it (enforced by tests/doc-sync.py). Bump it in the same commit as the CHANGELOG entry.
#
# P2-2: between releases, main carries a **development** version — a SemVer pre-release of the NEXT
# target (`X.Y.Z-dev.N`), so `aipair --version` on a main checkout is never mistaken for the shipped
# `X.Y.Z`. It has its own `## [X.Y.Z-dev.N] — unreleased` section in CHANGELOG.md (the "prepared"
# lifecycle shape). At release, drop the `-dev.N` pre-release to the final `X.Y.Z` and date the
# section (see RELEASING.md). NB SemVer pre-release uses a hyphen (`-dev.0`), not PEP 440's `.dev0`.
__version__ = "0.2.0-dev.0"
