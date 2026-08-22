#!/usr/bin/env bash
# Upgrade path: a real install into a throw-away HOME must retire binaries this project no
# longer ships (D2: aipair-queue) and install the new sibling lib (aipair-corelib). Needs
# the real deps (claude/codex/tmux/script) + a pty, so it SKIPS where they are absent (CI).
#   bash tests/install-upgrade.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"
for dep in claude codex tmux script; do
  command -v "$dep" >/dev/null 2>&1 || { echo "skip install-upgrade (no $dep)"; exit 0; }
done
TH="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upg.XXXXXX")"
trap 'rm -rf "$TH"' EXIT
mkdir -p "$TH/.local/bin"
printf '#!/bin/sh\necho stale\n' > "$TH/.local/bin/aipair-queue"; chmod +x "$TH/.local/bin/aipair-queue"

fail=0; n=0
chk() { n=$((n+1)); if eval "$1"; then echo "ok   $2"; else echo "FAIL $2"; fail=1; fi; }

out="$(env -u TMUX HOME="$TH" bash "$REPO/aipair-install.sh" 2>&1)"; rc=$?
chk "[ $rc -eq 0 ]" "installer exits 0 (got $rc)"
chk "[ ! -e '$TH/.local/bin/aipair-queue' ]" "stale aipair-queue removed from bin"
chk "ls '$TH/.local/bin/'aipair-queue.removed-* >/dev/null 2>&1" "stale aipair-queue moved to .removed-*"
chk "[ -f '$TH/.local/bin/aipair-corelib' ]" "aipair-corelib installed"
chk "printf '%s' \"\$out\" | grep -q 'retired'" "installer reports the retirement"
chk "env -u TMUX HOME='$TH' '$TH/.local/bin/aipair-relay' --help >/dev/null 2>&1" "installed relay loads corelib (--help ok)"

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
