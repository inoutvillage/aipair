"""aipairlib — the importable Python package behind the aipair CLIs (#7: replaces the old
SourceFileLoader-by-path + runtime attribute injection with a normal package of plain imports).

The thin executables `bin/aipair-relay` and `bin/peer-log` add their own directory to sys.path
and `from aipairlib import relay` / `import aipairlib.peerlog`. Python 3.8+ (stdlib only)."""
