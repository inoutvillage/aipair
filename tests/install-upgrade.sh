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

# --- rollback safety: a package install that FAILS must leave the PREVIOUS working install intact
# (old entrypoints still runnable + flat libs not retired). Break a package module in a repo COPY
# so the staged package fails its import verify; the two-phase installer must stop BEFORE it
# switches the thin entrypoints, so the old (legacy) aipair-relay/peer-log keep working.
TH3="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upg3.XXXXXX")"; mkdir -p "$TH3/.local/bin"
for lib in "${FLATLIBS[@]}"; do stale_bin "$TH3/.local/bin/$lib"; done
# a WORKING legacy entrypoint (pre-#7): responds to --help with exit 0
for ep in aipair-relay peer-log; do
  printf '#!/bin/sh
echo "legacy %s usage"
exit 0
' "$ep" > "$TH3/.local/bin/$ep"; chmod +x "$TH3/.local/bin/$ep"
done
BROKEN="$(mktemp -d "${TMPDIR:-/tmp}/aipair-broken.XXXXXX")"
cp "$REPO/aipair-install.sh" "$BROKEN/"; cp -r "$REPO/bin" "$REPO/templates" "$BROKEN/"
mkdir -p "$BROKEN/.claude"; cp -r "$REPO/.claude/skills" "$BROKEN/.claude/"
printf 'def broken(:\n' > "$BROKEN/bin/aipairlib/tmuxlib.py"   # exists (passes preflight) but won't import
rc=0; out3="$(env -u TMUX PATH="$SHIMD:$PATH" HOME="$TH3" bash "$BROKEN/aipair-install.sh" 2>&1)" || rc=$?
chk "[ $rc -ne 0 ]" "broken package → installer exits non-zero (got $rc)"
echo "$out3" | grep -q "new aipairlib package failed to import" && importfail=1 || importfail=0
chk "[ $importfail -eq 1 ]" "installer stops at the staged-package import verify (before switching entrypoints)"
for lib in "${FLATLIBS[@]}"; do
  chk "[ -e '$TH3/.local/bin/$lib' ]" "flat $lib SURVIVES a failed package install (not retired early)"
done
# the crux: the OLD entrypoints are untouched, so the previous install still works
chk "env -u TMUX HOME='$TH3' '$TH3/.local/bin/aipair-relay' --help >/dev/null 2>&1" "legacy aipair-relay STILL runs after the failed upgrade"
chk "env -u TMUX HOME='$TH3' '$TH3/.local/bin/peer-log' --help >/dev/null 2>&1" "legacy peer-log STILL runs after the failed upgrade"
chk "grep -q legacy '$TH3/.local/bin/aipair-relay'" "aipair-relay was NOT replaced by the thin entrypoint"
rm -rf "$TH3" "$BROKEN"

# --- rollback when a #7 package is ALREADY installed: a broken upgrade must NOT overwrite the
# live package in place (else the existing thin entrypoints would read the broken package). Install
# the good version, then upgrade the SAME HOME with the broken one, and confirm the previous
# install still runs and its package content is preserved.
TH4="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upg4.XXXXXX")"
env -u TMUX PATH="$SHIMD:$PATH" HOME="$TH4" bash "$REPO/aipair-install.sh" >/dev/null 2>&1
chk "[ -f '$TH4/.local/bin/aipairlib/relay.py' ]" "(precondition) good #7 install placed the package"
chk "env -u TMUX HOME='$TH4' '$TH4/.local/bin/aipair-relay' --help >/dev/null 2>&1" "(precondition) installed #7 relay runs"
BROKEN2="$(mktemp -d "${TMPDIR:-/tmp}/aipair-broken2.XXXXXX")"
cp "$REPO/aipair-install.sh" "$BROKEN2/"; cp -r "$REPO/bin" "$REPO/templates" "$BROKEN2/"
mkdir -p "$BROKEN2/.claude"; cp -r "$REPO/.claude/skills" "$BROKEN2/.claude/"
printf 'def broken(:\n' > "$BROKEN2/bin/aipairlib/tmuxlib.py"
rc=0; env -u TMUX PATH="$SHIMD:$PATH" HOME="$TH4" bash "$BROKEN2/aipair-install.sh" >/dev/null 2>&1 || rc=$?
chk "[ $rc -ne 0 ]" "broken UPGRADE over an existing #7 install exits non-zero (got $rc)"
chk "env -u TMUX HOME='$TH4' '$TH4/.local/bin/aipair-relay' --help >/dev/null 2>&1" "existing aipair-relay STILL runs after the broken upgrade"
chk "env -u TMUX HOME='$TH4' '$TH4/.local/bin/peer-log' --help >/dev/null 2>&1" "existing peer-log STILL runs after the broken upgrade"
h_live="$(sha256sum < "$TH4/.local/bin/aipairlib/relay.py")"; h_repo="$(sha256sum < "$REPO/bin/aipairlib/relay.py")"
chk "[ '$h_live' = '$h_repo' ]" "live relay.py content preserved (not overwritten)"
chk "! grep -q 'def broken' '$TH4/.local/bin/aipairlib/tmuxlib.py'" "live tmuxlib.py NOT overwritten by the broken module"
rm -rf "$TH4" "$BROKEN2"

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

# --- Phase 2 (entrypoint) transactional rollback (P2-2): a mid-switch failure that cannot complete
# must restore EVERY entrypoint already switched, so an upgrade never ends half-old/half-new ------
TH5="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upg5.XXXXXX")"
mkdir -p "$TH5/.local/bin"
env -u TMUX PATH="$SHIMD:$PATH" HOME="$TH5" bash "$REPO/aipair-install.sh" >/dev/null 2>&1
if [ -x "$TH5/.local/bin/aipair-relay" ]; then
  # make the installed entrypoints "stale" (differ from repo → they WILL be switched) and tag them
  # so we can prove a failed upgrade rolled them back to THIS content, not the new repo copy.
  for f in aipair aipair-relay aipair-relay-here peer-log; do printf '\n# STALE-P2-%s\n' "$$" >> "$TH5/.local/bin/$f"; done
  # sabotage `peer` (4th of 5): replace it with a directory so its backup `cp -p` fails mid-switch,
  # AFTER aipair / aipair-relay / aipair-relay-here have already been switched.
  rm -f "$TH5/.local/bin/peer"; mkdir "$TH5/.local/bin/peer"; : > "$TH5/.local/bin/peer/keep"
  rc5=0; env -u TMUX PATH="$SHIMD:$PATH" HOME="$TH5" bash "$REPO/aipair-install.sh" >/dev/null 2>&1 || rc5=$?
  chk "[ $rc5 -ne 0 ]" "entrypoint switch that cannot complete → installer exits non-zero (got $rc5)"
  for f in aipair aipair-relay aipair-relay-here; do
    chk "grep -q 'STALE-P2-$$' '$TH5/.local/bin/$f'" "$f rolled back to pre-upgrade content (Phase 2 all-or-nothing)"
  done
  chk "! ls -d '$TH5/.local/bin/.aipair-stage-'* >/dev/null 2>&1" "staging dir cleaned up after the failed upgrade"
else
  echo "skip Phase 2 rollback check (initial install did not complete — no tmux/py?)"
fi
rm -rf "$TH5"

# --- P2-2 unified transaction: templates share the binary transaction ----------------------------
# (A) a BROKEN 2nd notice template (codex-agents-block.md) is caught while STAGING — before ANY
#     commit — so the binaries are NOT installed and the 1st notice file (CLAUDE.md) is untouched.
# (B) a commit-phase failure (a sabotaged --vscode-tasks, which runs AFTER binaries + both notice
#     blocks are committed) rolls the WHOLE batch back: binaries AND both notice files return to
#     their pre-upgrade content. Together these prove templates are in the same all-or-nothing txn.
mk_stale_notices() {   # $1=HOME — drop plain (block-less) notice files so a re-run must re-commit them
  printf 'CLAUDE-OLD-%s\n' "$$" > "$1/.claude/CLAUDE.md"
  printf 'AGENTS-OLD-%s\n' "$$" > "$1/.codex/AGENTS.md"
}
mk_stale_bins() { for f in aipair aipair-relay aipair-relay-here peer peer-log; do printf '\n# STALE-%s\n' "$$" >> "$1/.local/bin/$f"; done; }

# (A) broken codex template → nothing committed
THA="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upgA.XXXXXX")"; mkdir -p "$THA/.local/bin"
env -u TMUX PATH="$SHIMD:$PATH" HOME="$THA" bash "$REPO/aipair-install.sh" >/dev/null 2>&1
if [ -x "$THA/.local/bin/aipair-relay" ] && [ -f "$THA/.claude/CLAUDE.md" ]; then
  BROK="$(mktemp -d)"; mkdir -p "$BROK"; cp -a "$REPO/aipair-install.sh" "$REPO/bin" "$REPO/templates" "$REPO/.claude" "$BROK/"
  # break the SECOND notice template: delete its end marker → stage_block rejects it (exit 4)
  grep -v 'aipair:end' "$BROK/templates/codex-agents-block.md" > "$BROK/templates/codex-agents-block.md.x" && mv "$BROK/templates/codex-agents-block.md.x" "$BROK/templates/codex-agents-block.md"
  mk_stale_bins "$THA"; mk_stale_notices "$THA"
  rcA=0; env -u TMUX PATH="$SHIMD:$PATH" HOME="$THA" bash "$BROK/aipair-install.sh" >/dev/null 2>&1 || rcA=$?
  chk "[ $rcA -ne 0 ]" "broken 2nd notice template → installer exits non-zero (got $rcA)"
  chk "grep -q 'STALE-$$' '$THA/.local/bin/aipair-relay'" "broken template caught at STAGE → binaries NOT committed"
  chk "grep -q 'CLAUDE-OLD-$$' '$THA/.claude/CLAUDE.md' && ! grep -q 'aipair:start' '$THA/.claude/CLAUDE.md'" "1st notice (CLAUDE.md) NOT committed when the 2nd template is broken"
  chk "! ls -d '$THA/.local/bin/.aipair-stage-'* >/dev/null 2>&1" "no staging dir left after the aborted install (A)"
  chk "! ls '$THA/.claude/'CLAUDE.md.aipair-new-* >/dev/null 2>&1" "no staged notice temp left after the aborted install (A)"
  rm -rf "$BROK"
else
  echo "skip P2-2 txn test A (baseline install did not complete)"
fi
rm -rf "$THA"

# (B) commit-phase failure rolls back binaries + both notice files
THB="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upgB.XXXXXX")"; mkdir -p "$THB/.local/bin"
env -u TMUX PATH="$SHIMD:$PATH" HOME="$THB" bash "$REPO/aipair-install.sh" >/dev/null 2>&1
if [ -x "$THB/.local/bin/aipair-relay" ] && [ -f "$THB/.claude/CLAUDE.md" ]; then
  mk_stale_bins "$THB"; mk_stale_notices "$THB"
  VDIR="$(mktemp -d)"; : > "$VDIR/.vscode"   # sabotage: .vscode is a FILE → mkdir fails in the commit phase
  rcB=0; env -u TMUX PATH="$SHIMD:$PATH" HOME="$THB" bash "$REPO/aipair-install.sh" --vscode-tasks "$VDIR" >/dev/null 2>&1 || rcB=$?
  chk "[ $rcB -ne 0 ]" "commit-phase (vscode) failure → installer exits non-zero (got $rcB)"
  chk "grep -q 'STALE-$$' '$THB/.local/bin/aipair-relay'" "binaries rolled back on a commit-phase failure (unified journal)"
  chk "grep -q 'CLAUDE-OLD-$$' '$THB/.claude/CLAUDE.md' && ! grep -q 'aipair:start' '$THB/.claude/CLAUDE.md'" "1st notice (CLAUDE.md) rolled back with the binaries"
  chk "grep -q 'AGENTS-OLD-$$' '$THB/.codex/AGENTS.md' && ! grep -q 'aipair:start' '$THB/.codex/AGENTS.md'" "2nd notice (AGENTS.md) rolled back with the binaries"
  rm -rf "$VDIR"
else
  echo "skip P2-2 txn test B (baseline install did not complete)"
fi
rm -rf "$THB"

# (C) smoke_test failure rolls the WHOLE install back. A new `aipair` can pass `bash -n` staging yet
# fail to launch a pair (Codex: broken-but-parseable). The commit is only final after smoke passes.
THC="$(mktemp -d "${TMPDIR:-/tmp}/aipair-upgC.XXXXXX")"; mkdir -p "$THC/.local/bin"
env -u TMUX PATH="$SHIMD:$PATH" HOME="$THC" bash "$REPO/aipair-install.sh" >/dev/null 2>&1
if [ -x "$THC/.local/bin/aipair-relay" ] && [ -f "$THC/.claude/CLAUDE.md" ]; then
  BROKC="$(mktemp -d)"; cp -a "$REPO/aipair-install.sh" "$REPO/bin" "$REPO/templates" "$REPO/.claude" "$BROKC/"
  printf '#!/usr/bin/env bash\nexit 1\n' > "$BROKC/bin/aipair"; chmod +x "$BROKC/bin/aipair"   # valid syntax, fails at runtime
  mk_stale_bins "$THC"; mk_stale_notices "$THC"
  rcC=0; env -u TMUX PATH="$SHIMD:$PATH" HOME="$THC" bash "$BROKC/aipair-install.sh" >/dev/null 2>&1 || rcC=$?
  chk "[ $rcC -ne 0 ]" "smoke failure (broken-but-parseable aipair) → installer exits non-zero (got $rcC)"
  chk "grep -q 'STALE-$$' '$THC/.local/bin/aipair-relay'" "binaries rolled back after a smoke failure (not left committed)"
  chk "! grep -q '^exit 1' '$THC/.local/bin/aipair'" "the broken aipair was rolled back (previous copy restored)"
  chk "grep -q 'CLAUDE-OLD-$$' '$THC/.claude/CLAUDE.md' && ! grep -q 'aipair:start' '$THC/.claude/CLAUDE.md'" "notice blocks rolled back after a smoke failure"
  chk "! ls -d '$THC/.local/bin/.aipair-stage-'* >/dev/null 2>&1" "staging dir cleaned up after the smoke-failed upgrade"
  rm -rf "$BROKC"
else
  echo "skip P2-2 txn test C (baseline install did not complete)"
fi
rm -rf "$THC"

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
