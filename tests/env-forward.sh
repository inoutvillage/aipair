#!/usr/bin/env bash
# Env forwarding into the relay: an already-running tmux server does NOT hand a new
# session this process's environment, so every AIPAIR_* the relay reads from the env must
# be expanded onto its argv by the launcher. This test runs both launchers against a
# PRE-EXISTING private tmux server and checks the relay actually received the flags.
#   bash tests/env-forward.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"
REAL_TMUX="$(command -v tmux)" || { echo "tmux not found" >&2; exit 2; }
command -v script >/dev/null || { echo "skip (no \`script\`): env-forward needs a pty"; exit 0; }
W="$(mktemp -d "${TMPDIR:-/tmp}/aipair-envfwd.XXXXXX")"; SOCKET="aipair-envfwd-$$-$RANDOM"
cleanup() { "$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true; rm -rf "$W"; }; trap cleanup EXIT
mkdir -p "$W/bin" "$W/proj"
# tmux shim forces the private socket even inside a pane ($TMUX beats TMUX_TMPDIR).
# shim pins `exit-empty off` each call so the private server survives empty moments (tmux 3.4)
printf '#!/usr/bin/env bash\n%q -L %q start-server 2>/dev/null || true\n%q -L %q set-option -g exit-empty off 2>/dev/null || true\nexec %q -L %q "$@"\n' \
  "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" > "$W/bin/tmux"
# claude/codex exit at once so their panes don't hang; the relay shim records its argv.
printf '#!/usr/bin/env bash\nexit 0\n' > "$W/bin/claude"; cp "$W/bin/claude" "$W/bin/codex"; cp "$W/bin/claude" "$W/bin/peer-log"
cat > "$W/bin/aipair-relay" <<SHIM
#!/usr/bin/env bash
{ printf 'ARGV: %s\\n' "\$*"
  printf 'GATE=[%s] NVG=[%s] AUD=[%s] TIMEOUT=[%s] ROUNDS=[%s]\\n' \\
    "\${AIPAIR_GATE:-}" "\${AIPAIR_NO_VERSION_GATE:-}" "\${AIPAIR_ALLOW_UNTESTED_DIALOGS:-}" \\
    "\${AIPAIR_GATE_TIMEOUT:-}" "\${AIPAIR_GATE_ROUNDS:-}"
} > $W/relay-argv
SHIM
chmod +x "$W"/bin/*
export PATH="$W/bin:$REPO/bin:$PATH"; unset TMUX
"$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true

fail=0; n=0
# kill-server immediately followed by new-session on the same socket can hit
# "server exited unexpectedly" (the new client connects to the still-dying server).
# reset_server waits for the socket to actually go away first.
reset_server() {
  "$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true
  for _ in $(seq 1 50); do "$REAL_TMUX" -L "$SOCKET" list-sessions >/dev/null 2>&1 || break; sleep 0.1; done
}
chk_has() { n=$((n+1)); if printf '%s' "$1" | grep -qF -- "$2"; then echo "ok   $3"; else echo "FAIL $3"; printf '     line: %s\n' "$1"; fail=1; fi; }
wait_file() { for _ in $(seq 1 50); do [ -s "$1" ] && return 0; sleep 0.1; done; return 1; }

echo "# [1] aipair loop into an EXISTING server forwards gate + version env as flags"
"$REAL_TMUX" -L "$SOCKET" new-session -d -s pre -c /tmp        # server now exists → new session won't inherit our env
rm -f "$W/relay-argv"
AIPAIR_NO_VERSION_GATE=1 AIPAIR_ALLOW_UNTESTED_DIALOGS=1 AIPAIR_GATE='npm test' \
  AIPAIR_GATE_TIMEOUT=120 AIPAIR_GATE_ROUNDS=2 AIPAIR_CLAUDE_FLAGS='' AIPAIR_CODEX_FLAGS='' \
  AIPAIR_UNSAFE=1 timeout 8 script -qec "aipair loop '$W/proj'" /dev/null >/dev/null 2>&1 || true
if wait_file "$W/relay-argv"; then
  argv="$(cat "$W/relay-argv")"
  chk_has "$argv" "--no-version-gate" "aipair loop → --no-version-gate"
  chk_has "$argv" "--allow-untested-dialogs" "aipair loop → --allow-untested-dialogs"
  chk_has "$argv" "--gate npm test" "aipair loop → --gate 'npm test'"
  chk_has "$argv" "--gate-timeout 120" "aipair loop → --gate-timeout"
  chk_has "$argv" "--gate-rounds 2" "aipair loop → --gate-rounds"
  chk_has "$argv" "GATE=[npm test]" "relay env sees AIPAIR_GATE"
else n=$((n+1)); echo "FAIL aipair loop: relay was never launched"; fail=1; fi
reset_server

echo "# [1b] a STALE AIPAIR_* in an existing server is overridden by the current (empty) value"
# Start the server carrying stale values; then launch with the vars empty.
AIPAIR_GATE='stale gate' AIPAIR_NO_VERSION_GATE=1 AIPAIR_ALLOW_UNTESTED_DIALOGS=1 \
  "$REAL_TMUX" -L "$SOCKET" new-session -d -s pre -c /tmp
rm -f "$W/relay-argv"
AIPAIR_GATE='' AIPAIR_NO_VERSION_GATE='' AIPAIR_ALLOW_UNTESTED_DIALOGS='' \
  AIPAIR_CLAUDE_FLAGS='' AIPAIR_CODEX_FLAGS='' \
  AIPAIR_UNSAFE=1 timeout 8 script -qec "aipair loop '$W/proj'" /dev/null >/dev/null 2>&1 || true
if wait_file "$W/relay-argv"; then
  argv="$(grep '^ARGV' "$W/relay-argv")"; envl="$(grep '^GATE=' "$W/relay-argv")"
  n=$((n+1)); if printf '%s' "$argv" | grep -qF -- "--gate"; then echo "FAIL stale gate leaked into argv: $argv"; fail=1; else echo "ok   no stale --gate flag"; fi
  chk_has "$envl" "GATE=[]" "relay env AIPAIR_GATE neutralized (was 'stale gate')"
  chk_has "$envl" "NVG=[]" "relay env AIPAIR_NO_VERSION_GATE neutralized (was 1)"
  chk_has "$envl" "AUD=[]" "relay env AIPAIR_ALLOW_UNTESTED_DIALOGS neutralized (was 1)"
else n=$((n+1)); echo "FAIL aipair loop (stale): relay never launched"; fail=1; fi
reset_server

echo "# [2] aipair-relay-here --print forwards the same env as flags"
STUB="$W/relay-stub"; printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB"; chmod +x "$STUB"
"$REAL_TMUX" -L "$SOCKET" new-session -d -s pair -c "$W/proj"
BR="$("$REAL_TMUX" -L "$SOCKET" split-window -t pair -P -F '#{pane_id}' -c "$W/proj")"
"$REAL_TMUX" -L "$SOCKET" select-pane -t "$BR" -T bridge
P0="$("$REAL_TMUX" -L "$SOCKET" list-panes -t pair -F '#{pane_id}' | head -1)"
"$REAL_TMUX" -L "$SOCKET" send-keys -t "$P0" \
  "AIPAIR_RELAY_BIN='$STUB' AIPAIR_NO_VERSION_GATE=1 AIPAIR_GATE='pytest -q' aipair-relay-here --session pair --print > '$W/rh.out' 2>&1" Enter
if wait_file "$W/rh.out"; then
  line="$(grep -E '^launch' "$W/rh.out" || cat "$W/rh.out")"
  chk_has "$line" "--no-version-gate" "relay-here → --no-version-gate"
  chk_has "$line" "'--gate' 'pytest -q'" "relay-here → --gate 'pytest -q'"
  chk_has "$line" "AIPAIR_NO_VERSION_GATE=" "relay-here pins env (overrides the bridge pane)"
  chk_has "$line" "AIPAIR_GATE=" "relay-here pins AIPAIR_GATE on the command"
else n=$((n+1)); echo "FAIL relay-here produced no output"; sed 's/^/     /' "$W/rh.out" 2>/dev/null || true; fail=1; fi
reset_server

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
