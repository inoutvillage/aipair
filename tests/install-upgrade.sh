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

# --- retire FAILURE must fail the install (a dangerous stale binary left runnable is not ok) ---
TH2="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upg2.XXXXXX")"
mkdir -p "$TH2/.local/bin"
printf '#!/bin/sh\necho stale\n' > "$TH2/.local/bin/aipair-queue"; chmod +x "$TH2/.local/bin/aipair-queue"
chmod a-w "$TH2/.local/bin"     # make the retire `mv` fail
# only meaningful if the current user actually can't write now (root/overlay may ignore perms)
if ( : > "$TH2/.local/bin/.wtest" ) 2>/dev/null; then
  rm -f "$TH2/.local/bin/.wtest"; chmod u+w "$TH2/.local/bin"; rm -rf "$TH2"
  echo "skip retire-failure check (bin dir still writable — root/overlay fs)"
else
  rc=0; out="$(env -u TMUX HOME="$TH2" bash "$REPO/aipair-install.sh" 2>&1)" || rc=$?
  chk "[ $rc -ne 0 ]" "retire mv failure → installer exits non-zero (got $rc)"
  chk "[ -e '$TH2/.local/bin/aipair-queue' ]" "stale aipair-queue is left in place on failure (not silently gone)"
  chk "printf '%s' \"\$out\" | grep -qi 'could not retire'" "installer reports the retire failure"
  chmod u+w "$TH2/.local/bin" 2>/dev/null || true; rm -rf "$TH2"
fi

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
