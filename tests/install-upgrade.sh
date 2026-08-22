#!/usr/bin/env bash
# Upgrade path: a real install into a throw-away HOME must (a) retire the aipair-queue binary the
# project no longer ships (D2), (b) install the aipairlib package (#7) and retire the OLD flat
# aipair-*lib files it supersedes, and (c) do that retirement only AFTER the package installs +
# its import verifies — so a FAILED package install leaves the previous working install intact.
# claude/codex are shimmed (only --version is needed) so this runs on CI too; a real tmux is
# required to build the private -L socket, so it skips only where tmux is absent.
#   bash tests/install-upgrade.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"
command -v tmux >/dev/null 2>&1 || { echo "skip install-upgrade (no tmux)"; exit 0; }

TH="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upg.XXXXXX")"
REAL_TMUX="$(command -v tmux)"; SOCKET="aipair-upg-$$-$RANDOM"; SHIMD="$(mktemp -d)"
trap '"$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true; rm -f "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/$SOCKET" 2>/dev/null || true; rm -rf "$TH" "$SHIMD"' EXIT
# tmux shim → PRIVATE -L socket (the installer smoke starts a real pair; never touch the default
# server — SECURITY.md「テストハーネスの tmux」guardrail). claude/codex shims answer --version.
printf '#!/usr/bin/env bash\n%q -L %q start-server 2>/dev/null || true\n%q -L %q set-option -g exit-empty off 2>/dev/null || true\nexec %q -L %q "$@"\n' \
  "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" > "$SHIMD/tmux"; chmod +x "$SHIMD/tmux"
printf '#!/bin/sh\nfor a in "$@"; do [ "$a" = --version ] && { echo "claude 2.1.240 (Claude Code)"; exit 0; }; done\nexit 0\n' > "$SHIMD/claude"
printf '#!/bin/sh\nfor a in "$@"; do [ "$a" = --version ] && { echo "codex-cli 0.149.0"; exit 0; }; done\nexit 0\n' > "$SHIMD/codex"
chmod +x "$SHIMD/claude" "$SHIMD/codex"
"$REAL_TMUX" -L "$SOCKET" new-session -d -s probe 2>/dev/null
_want="$("$REAL_TMUX" -L "$SOCKET" display-message -p -t probe '#{socket_path}')"
_got="$(PATH="$SHIMD:$PATH" tmux display-message -p -t probe '#{socket_path}')"
"$REAL_TMUX" -L "$SOCKET" kill-session -t probe 2>/dev/null || true
[ "$_got" = "$_want" ] && [ "$(basename "$_got")" = "$SOCKET" ] || { echo "tmux shim not effective — refusing to touch the default server" >&2; exit 2; }

FLATLIBS=(aipair-corelib aipair-loglib aipair-tmuxlib aipair-deliverylib aipair-dialoglib)
stale_bin() { printf '#!/bin/sh\necho stale\n' > "$1"; chmod +x "$1"; }
# Fixture: a pre-#7 install — the removed aipair-queue + the flat aipair-*lib files.
mkdir -p "$TH/.local/bin"
stale_bin "$TH/.local/bin/aipair-queue"
for lib in "${FLATLIBS[@]}"; do stale_bin "$TH/.local/bin/$lib"; done

fail=0; n=0
chk() { n=$((n+1)); if eval "$1"; then echo "ok   $2"; else echo "FAIL $2"; fail=1; fi; }

# --- normal upgrade: queue + every flat lib retired, package installed, entrypoint imports it ---
out="$(env -u TMUX PATH="$SHIMD:$PATH" HOME="$TH" bash "$REPO/aipair-install.sh" 2>&1)"; rc=$?
chk "[ $rc -eq 0 ]" "installer exits 0 (got $rc)"
chk "[ ! -e '$TH/.local/bin/aipair-queue' ]" "stale aipair-queue removed from bin"
chk "ls '$TH/.local/bin/'aipair-queue.removed-* >/dev/null 2>&1" "stale aipair-queue moved to .removed-*"
for lib in "${FLATLIBS[@]}"; do
  chk "[ ! -e '$TH/.local/bin/$lib' ]" "stale flat $lib retired"
  chk "ls '$TH/.local/bin/'$lib.removed-* >/dev/null 2>&1" "$lib moved to .removed-*"
done
chk "[ -f '$TH/.local/bin/aipairlib/corelib.py' ]" "aipairlib package installed (corelib.py)"
chk "[ -f '$TH/.local/bin/aipairlib/relay.py' ]" "aipairlib package installed (relay.py)"
echo "$out" | grep -q "retired" && retired=1 || retired=0
chk "[ $retired -eq 1 ]" "installer reports the retirement"
chk "env -u TMUX HOME='$TH' '$TH/.local/bin/aipair-relay' --help >/dev/null 2>&1" "installed relay imports the aipairlib package (--help ok)"

# --- rollback safety: a package install that FAILS must NOT retire the old flat libs ----------
# Break a package module in a repo COPY so the copied package fails its import check; the flat
# libs must survive (they are retired only AFTER the import verifies). Proves the ordering fix.
TH3="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upg3.XXXXXX")"; mkdir -p "$TH3/.local/bin"
for lib in "${FLATLIBS[@]}"; do stale_bin "$TH3/.local/bin/$lib"; done
BROKEN="$(mktemp -d "${TMPDIR:-/tmp}/aipair-broken.XXXXXX")"
cp "$REPO/aipair-install.sh" "$BROKEN/"; cp -r "$REPO/bin" "$REPO/templates" "$BROKEN/"
mkdir -p "$BROKEN/.claude"; cp -r "$REPO/.claude/skills" "$BROKEN/.claude/"
printf 'def broken(:\n' > "$BROKEN/bin/aipairlib/tmuxlib.py"   # exists (passes preflight) but won't import
rc=0; out3="$(env -u TMUX PATH="$SHIMD:$PATH" HOME="$TH3" bash "$BROKEN/aipair-install.sh" 2>&1)" || rc=$?
chk "[ $rc -ne 0 ]" "broken package → installer exits non-zero (got $rc)"
echo "$out3" | grep -q -- "--help failed" && importfail=1 || importfail=0
chk "[ $importfail -eq 1 ]" "installer fails at the entrypoint import check"
for lib in "${FLATLIBS[@]}"; do
  chk "[ -e '$TH3/.local/bin/$lib' ]" "flat $lib SURVIVES a failed package install (not retired early)"
done
rm -rf "$TH3" "$BROKEN"

# --- retire FAILURE must fail the install (a dangerous stale binary left runnable is not ok) ---
TH2="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upg2.XXXXXX")"
mkdir -p "$TH2/.local/bin"
stale_bin "$TH2/.local/bin/aipair-queue"
chmod a-w "$TH2/.local/bin"     # make the retire `mv` fail
if ( : > "$TH2/.local/bin/.wtest" ) 2>/dev/null; then
  rm -f "$TH2/.local/bin/.wtest"; chmod u+w "$TH2/.local/bin"; rm -rf "$TH2"
  echo "skip retire-failure check (bin dir still writable — root/overlay fs)"
else
  rc=0; out="$(env -u TMUX PATH="$SHIMD:$PATH" HOME="$TH2" bash "$REPO/aipair-install.sh" 2>&1)" || rc=$?
  chk "[ $rc -ne 0 ]" "retire mv failure → installer exits non-zero (got $rc)"
  chk "[ -e '$TH2/.local/bin/aipair-queue' ]" "stale aipair-queue is left in place on failure (not silently gone)"
  echo "$out" | grep -qi "could not retire" && reported=1 || reported=0
  chk "[ $reported -eq 1 ]" "installer reports the retire failure"
  chmod u+w "$TH2/.local/bin" 2>/dev/null || true; rm -rf "$TH2"
fi

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
