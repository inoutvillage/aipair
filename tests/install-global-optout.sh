#!/usr/bin/env bash
# The --no-global-instructions opt-out, exercised on CI (which does NOT ship claude/codex, so
# tests/install-upgrade.sh skips entirely). Here we stand up minimal claude/codex shims — enough
# for the installer's dep check and its `--version` smoke start — so the global-instructions branch
# actually runs. Needs a real tmux (the installer smoke-starts a pair); skips only if tmux is absent.
#   bash tests/install-global-optout.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"
command -v tmux    >/dev/null 2>&1 || { echo "skip install-global-optout (no tmux)"; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo "skip install-global-optout (no python3)"; exit 0; }

SH="$(mktemp -d "${TMPDIR:-/tmp}/aipair-optout-shims.XXXXXX")"
mkdir -p "$SH/bin"
# Respond to --version anywhere on the argv (dep check calls `claude --version`; the smoke start
# runs `claude --session-id <uuid> --version`). Otherwise exit 0 so the pane just returns to a shell.
cat > "$SH/bin/claude" <<'SHIM'
#!/bin/sh
for a in "$@"; do [ "$a" = --version ] && { echo "claude 2.1.239 (Claude Code)"; exit 0; }; done
exit 0
SHIM
cat > "$SH/bin/codex" <<'SHIM'
#!/bin/sh
for a in "$@"; do [ "$a" = --version ] && { echo "codex-cli 0.149.0"; exit 0; }; done
exit 0
SHIM
chmod +x "$SH/bin/claude" "$SH/bin/codex"

# The installer smoke-starts a real pair, which talks to tmux. Force EVERY tmux call (the
# installer's and the aipair it launches) onto a PRIVATE -L socket so we NEVER create/kill
# sessions on the user's default/production server (SECURITY.md「テストハーネスの tmux」guardrail). exit-empty off
# keeps the private server alive between calls (tmux 3.4 exits an empty server at once).
REAL_TMUX="$(command -v tmux)"; SOCKET="aipair-optout-$$-$RANDOM"
printf '#!/usr/bin/env bash\n%q -L %q start-server 2>/dev/null || true\n%q -L %q set-option -g exit-empty off 2>/dev/null || true\nexec %q -L %q "$@"\n' \
  "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" > "$SH/bin/tmux"; chmod +x "$SH/bin/tmux"
trap '"$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true; rm -f "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/$SOCKET" 2>/dev/null || true; rm -rf "$SH"' EXIT   # replaces the earlier rm-only trap
# Refuse to run unless the shim provably targets the private socket (never the default server).
"$REAL_TMUX" -L "$SOCKET" new-session -d -s probe 2>/dev/null
want="$("$REAL_TMUX" -L "$SOCKET" display-message -p -t probe '#{socket_path}')"
got="$(PATH="$SH/bin:$PATH" tmux display-message -p -t probe '#{socket_path}')"
"$REAL_TMUX" -L "$SOCKET" kill-session -t probe 2>/dev/null || true
if [ "$got" != "$want" ] || [ "$(basename "$got")" != "$SOCKET" ]; then
  echo "tmux shim not effective (got '$got', want '$want') — refusing to touch the default server" >&2; exit 2
fi

fail=0; n=0
chk() { n=$((n+1)); if eval "$1"; then echo "ok   $2"; else echo "FAIL $2"; fail=1; fi; }
has_block()  { grep -q 'aipair:start' "$1/.claude/CLAUDE.md" 2>/dev/null && grep -q 'aipair:start' "$1/.codex/AGENTS.md" 2>/dev/null; }
# install ENV_ASSIGNMENTS... -- FLAGS...  → runs the installer into a fresh $H with the shims on PATH.
# Splits on `--`: everything before is `env` assignments, everything after is the installer's flags.
install() {
  H="$(mktemp -d "${TMPDIR:-/tmp}/aipair-optout.XXXXXX")"; mkdir -p "$H/.local/bin"
  local envs=() flags=() seen_dd=0
  for a in "$@"; do
    if [ "$a" = -- ]; then seen_dd=1; continue; fi
    if [ "$seen_dd" -eq 1 ]; then flags+=("$a"); else envs+=("$a"); fi
  done
  out="$(env -u TMUX PATH="$SH/bin:$PATH" HOME="$H" "${envs[@]}" bash "$REPO/aipair-install.sh" "${flags[@]}" 2>&1)"; rc=$?
}

# --- default install: the global blocks ARE written -----------------------------
install
chk "[ $rc -eq 0 ]" "default install exits 0 (got $rc)"
chk "has_block '$H'" "default install writes the global CLAUDE.md + AGENTS.md blocks"
chk "[ -f '$H/.local/bin/aipair' ]" "default install places the bins"
rm -rf "$H"

# --- --no-global-instructions flag: the blocks are NOT written ------------------
install -- --no-global-instructions
chk "[ $rc -eq 0 ]" "--no-global-instructions exits 0 (got $rc)"
chk "! has_block '$H'" "--no-global-instructions writes NO global block"
chk "[ -f '$H/.local/bin/aipair' ]" "--no-global-instructions still places the bins"
printf '%s' "$out" | grep -q 'スキップ' && sk=1 || sk=0
chk "[ $sk -eq 1 ]" "--no-global-instructions reports the skip"
rm -rf "$H"

# --- AIPAIR_NO_GLOBAL_INSTRUCTIONS=1 opts out -----------------------------------
install AIPAIR_NO_GLOBAL_INSTRUCTIONS=1
chk "[ $rc -eq 0 ]" "AIPAIR_NO_GLOBAL_INSTRUCTIONS=1 exits 0 (got $rc)"
chk "! has_block '$H'" "AIPAIR_NO_GLOBAL_INSTRUCTIONS=1 writes NO global block"
chk "[ -f '$H/.local/bin/aipair' ]" "env opt-out still places the bins"
rm -rf "$H"

# --- falsey values (case / whitespace normalised) must NOT opt out --------------
for falsey in False OFF ' 0 ' no; do
  install "AIPAIR_NO_GLOBAL_INSTRUCTIONS=$falsey"
  chk "[ $rc -eq 0 ]" "AIPAIR_NO_GLOBAL_INSTRUCTIONS='$falsey' exits 0 (got $rc)"
  chk "has_block '$H'" "AIPAIR_NO_GLOBAL_INSTRUCTIONS='$falsey' does NOT opt out (block written)"
  rm -rf "$H"
done

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
