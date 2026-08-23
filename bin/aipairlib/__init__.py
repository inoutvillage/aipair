"""aipairlib — the importable Python package behind the aipair CLIs (#7: replaces the old
SourceFileLoader-by-path + runtime attribute injection with a normal package of plain imports).

The thin executables `bin/aipair-relay` and `bin/peer-log` add their own directory to sys.path
and `from aipairlib import relay` / `import aipairlib.peerlog`. Python 3.8+ (stdlib only)."""

# Single source of truth for the aipair release version (P2-4). `aipair --version` /
# `aipair-relay --version` read this; git tags are `v<__version__>` and CHANGELOG.md's top entry
# must match it (enforced by tests/doc-sync.py). Bump it in the same commit as the CHANGELOG entry.
__version__ = "0.1.0"
