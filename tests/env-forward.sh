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
cleanup() { "$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true; rm -f "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/$SOCKET" 2>/dev/null || true; rm -rf "$W"; }; trap cleanup EXIT
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
  printf 'GATE=[%s] NVG=[%s] AUD=[%s] TIMEOUT=[%s] ROUNDS=[%s] AUS=[%s] NSP=[%s] HR=[%s]\\n' \\
    "\${AIPAIR_GATE:-}" "\${AIPAIR_NO_VERSION_GATE:-}" "\${AIPAIR_ALLOW_UNTESTED_DIALOGS:-}" \\
    "\${AIPAIR_GATE_TIMEOUT:-}" "\${AIPAIR_GATE_ROUNDS:-}" \\
    "\${AIPAIR_ALLOW_UNTESTED_SCHEMA:-}" "\${AIPAIR_NO_SCHEMA_PROBE:-}" "\${AIPAIR_HUMAN_REQUIRED:-}"
} > $W/relay-argv
SHIM
chmod +x "$W"/bin/*
export PATH="$W/bin:$REPO/bin:$PATH"; unset TMUX
# Preflight: prove the tmux shim provably targets the PRIVATE socket before anything runs, so a
# broken shim can never touch the user's default server (guardrail; same as the other tmux tests).
"$REAL_TMUX" -L "$SOCKET" new-session -d -s probe 2>/dev/null
want="$("$REAL_TMUX" -L "$SOCKET" display-message -p -t probe '#{socket_path}')"
got="$(tmux display-message -p -t probe '#{socket_path}')"
if [ "$got" != "$want" ] || [ "$(basename "$got")" != "$SOCKET" ]; then
  echo "tmux shim not effective (got '$got', want '$want') — refusing to touch the default server" >&2; exit 2
fi
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
  AIPAIR_GATE_TIMEOUT=120 AIPAIR_GATE_ROUNDS=2 AIPAIR_ALLOW_UNTESTED_SCHEMA=1 AIPAIR_NO_SCHEMA_PROBE=1 \
  AIPAIR_CLAUDE_FLAGS='' AIPAIR_CODEX_FLAGS='' \
  AIPAIR_UNSAFE=1 timeout 8 script -qec "aipair loop '$W/proj'" /dev/null >/dev/null 2>&1 || true
if wait_file "$W/relay-argv"; then
  argv="$(cat "$W/relay-argv")"
  chk_has "$argv" "--no-version-gate" "aipair loop → --no-version-gate"
  chk_has "$argv" "--allow-untested-dialogs" "aipair loop → --allow-untested-dialogs"
  chk_has "$argv" "--gate npm test" "aipair loop → --gate 'npm test'"
  chk_has "$argv" "--gate-timeout 120" "aipair loop → --gate-timeout"
  chk_has "$argv" "--gate-rounds 2" "aipair loop → --gate-rounds"
  chk_has "$argv" "--allow-untested-schema" "aipair loop → --allow-untested-schema"
  chk_has "$argv" "--no-schema-probe" "aipair loop → --no-schema-probe"
  chk_has "$argv" "GATE=[npm test]" "relay env sees AIPAIR_GATE"
  chk_has "$argv" "AUS=[1]" "relay env sees AIPAIR_ALLOW_UNTESTED_SCHEMA"
  chk_has "$argv" "NSP=[1]" "relay env sees AIPAIR_NO_SCHEMA_PROBE"
else n=$((n+1)); echo "FAIL aipair loop: relay was never launched"; fail=1; fi
reset_server

echo "# [1b] a STALE AIPAIR_* in an existing server is overridden by the current (empty) value"
# Start the server carrying stale values; then launch with the vars empty.
AIPAIR_GATE='stale gate' AIPAIR_NO_VERSION_GATE=1 AIPAIR_ALLOW_UNTESTED_DIALOGS=1 \
  AIPAIR_ALLOW_UNTESTED_SCHEMA=1 AIPAIR_NO_SCHEMA_PROBE=1 \
  "$REAL_TMUX" -L "$SOCKET" new-session -d -s pre -c /tmp
rm -f "$W/relay-argv"
AIPAIR_GATE='' AIPAIR_NO_VERSION_GATE='' AIPAIR_ALLOW_UNTESTED_DIALOGS='' \
  AIPAIR_ALLOW_UNTESTED_SCHEMA='' AIPAIR_NO_SCHEMA_PROBE='' \
  AIPAIR_CLAUDE_FLAGS='' AIPAIR_CODEX_FLAGS='' \
  AIPAIR_UNSAFE=1 timeout 8 script -qec "aipair loop '$W/proj'" /dev/null >/dev/null 2>&1 || true
if wait_file "$W/relay-argv"; then
  argv="$(grep '^ARGV' "$W/relay-argv")"; envl="$(grep '^GATE=' "$W/relay-argv")"
  n=$((n+1)); if printf '%s' "$argv" | grep -qF -- "--gate"; then echo "FAIL stale gate leaked into argv: $argv"; fail=1; else echo "ok   no stale --gate flag"; fi
  n=$((n+1)); if printf '%s' "$argv" | grep -qF -- "--no-schema-probe"; then echo "FAIL stale --no-schema-probe leaked into argv: $argv"; fail=1; else echo "ok   no stale --no-schema-probe flag"; fi
  chk_has "$envl" "GATE=[]" "relay env AIPAIR_GATE neutralized (was 'stale gate')"
  chk_has "$envl" "NVG=[]" "relay env AIPAIR_NO_VERSION_GATE neutralized (was 1)"
  chk_has "$envl" "AUD=[]" "relay env AIPAIR_ALLOW_UNTESTED_DIALOGS neutralized (was 1)"
  chk_has "$envl" "AUS=[]" "relay env AIPAIR_ALLOW_UNTESTED_SCHEMA neutralized (was 1)"
  chk_has "$envl" "NSP=[]" "relay env AIPAIR_NO_SCHEMA_PROBE neutralized (was 1)"
else n=$((n+1)); echo "FAIL aipair loop (stale): relay never launched"; fail=1; fi
reset_server

echo "# [1c] endless AIPAIR_HUMAN_REQUIRED: stale server value is neutralized; custom value reaches argv"
# a STALE value baked into the server; caller launches endless with it EMPTY. The --human-required
# flag must be bin/aipair's DEFAULT (from this process's empty env), never the server's stale value,
# and the pinned AIPAIR_HUMAN_REQUIRED= must be empty (RELAY_ENV_VARS neutralization).
AIPAIR_HUMAN_REQUIRED='[STALE_HR]' "$REAL_TMUX" -L "$SOCKET" new-session -d -s preh -c /tmp
rm -f "$W/relay-argv"
AIPAIR_ENDLESS=1 AIPAIR_HUMAN_REQUIRED='' AIPAIR_CLAUDE_FLAGS='' AIPAIR_CODEX_FLAGS='' \
  AIPAIR_UNSAFE=1 timeout 8 script -qec "aipair loop '$W/proj'" /dev/null >/dev/null 2>&1 || true
if wait_file "$W/relay-argv"; then
  argv="$(cat "$W/relay-argv")"
  n=$((n+1)); if printf '%s' "$argv" | grep -qF -- "[STALE_HR]"; then echo "FAIL stale AIPAIR_HUMAN_REQUIRED leaked into argv: $argv"; fail=1; else echo "ok   stale AIPAIR_HUMAN_REQUIRED did not leak into --human-required"; fi
  chk_has "$argv" "--human-required [AIPAIR_HUMAN_REQUIRED]" "empty caller → default HR sentinel (not the server's stale value)"
  # the relay's ENV for AIPAIR_HUMAN_REQUIRED must be neutralized by RELAY_ENV_VARS — HR=[] proves
  # the env pin (not just the flag default) overrode the server's stale value.
  chk_has "$argv" "HR=[]" "relay env AIPAIR_HUMAN_REQUIRED neutralized (server held '[STALE_HR]')"
else n=$((n+1)); echo "FAIL aipair loop (endless stale HR): relay never launched"; fail=1; fi
reset_server
# a CUSTOM value on the aipair loop path reaches argv (proves bin/aipair's bridge wiring, not only relay-here)
"$REAL_TMUX" -L "$SOCKET" new-session -d -s prehc -c /tmp
rm -f "$W/relay-argv"
AIPAIR_ENDLESS=1 AIPAIR_HUMAN_REQUIRED='[CUSTOM_HR]' AIPAIR_CLAUDE_FLAGS='' AIPAIR_CODEX_FLAGS='' \
  AIPAIR_UNSAFE=1 timeout 8 script -qec "aipair loop '$W/proj'" /dev/null >/dev/null 2>&1 || true
if wait_file "$W/relay-argv"; then
  argv="$(cat "$W/relay-argv")"
  chk_has "$argv" "--human-required [CUSTOM_HR]" "aipair loop forwards a custom AIPAIR_HUMAN_REQUIRED to argv"
  chk_has "$argv" "HR=[[CUSTOM_HR]]" "relay env AIPAIR_HUMAN_REQUIRED pinned to the custom value"
else n=$((n+1)); echo "FAIL aipair loop (endless custom HR): relay never launched"; fail=1; fi
reset_server

echo "# [2] aipair-relay-here --print forwards the same env as flags"
STUB="$W/relay-stub"; printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB"; chmod +x "$STUB"
"$REAL_TMUX" -L "$SOCKET" new-session -d -s pair -c "$W/proj"
BR="$("$REAL_TMUX" -L "$SOCKET" split-window -t pair -P -F '#{pane_id}' -c "$W/proj")"
"$REAL_TMUX" -L "$SOCKET" select-pane -t "$BR" -T bridge
P0="$("$REAL_TMUX" -L "$SOCKET" list-panes -t pair -F '#{pane_id}' | head -1)"
"$REAL_TMUX" -L "$SOCKET" send-keys -t "$P0" \
  "AIPAIR_RELAY_BIN='$STUB' AIPAIR_NO_VERSION_GATE=1 AIPAIR_GATE='pytest -q' AIPAIR_HUMAN_REQUIRED='[HR_TEST]' aipair-relay-here --session pair --print > '$W/rh.out' 2>&1" Enter
if wait_file "$W/rh.out"; then
  line="$(grep -E '^launch' "$W/rh.out" || cat "$W/rh.out")"
  chk_has "$line" "--no-version-gate" "relay-here → --no-version-gate"
  chk_has "$line" "'--gate' 'pytest -q'" "relay-here → --gate 'pytest -q'"
  chk_has "$line" "'--human-required' '[HR_TEST]'" "relay-here → --human-required (endless BLOCKED sentinel)"
  chk_has "$line" "AIPAIR_NO_VERSION_GATE=" "relay-here pins env (overrides the bridge pane)"
  chk_has "$line" "AIPAIR_GATE=" "relay-here pins AIPAIR_GATE on the command"
  chk_has "$line" "AIPAIR_HUMAN_REQUIRED=" "relay-here pins AIPAIR_HUMAN_REQUIRED on the command"
else n=$((n+1)); echo "FAIL relay-here produced no output"; sed 's/^/     /' "$W/rh.out" 2>/dev/null || true; fail=1; fi
reset_server

echo "# [2b] relay-here derives --dir from the session's @aipair-dir, not the caller's PWD"
"$REAL_TMUX" -L "$SOCKET" new-session -d -s pair2 -c "$W/proj"
OWNERDIR="$W/owner"; mkdir -p "$OWNERDIR"
"$REAL_TMUX" -L "$SOCKET" set -t pair2 @aipair-dir "$OWNERDIR"
BR2="$("$REAL_TMUX" -L "$SOCKET" split-window -t pair2 -P -F '#{pane_id}' -c "$W/proj")"
"$REAL_TMUX" -L "$SOCKET" select-pane -t "$BR2" -T bridge
Q0="$("$REAL_TMUX" -L "$SOCKET" list-panes -t pair2 -F '#{pane_id}' | head -1)"
# run from a DIFFERENT cwd (/) so PWD can never coincide with @aipair-dir
"$REAL_TMUX" -L "$SOCKET" send-keys -t "$Q0" "cd / && AIPAIR_RELAY_BIN='$STUB' aipair-relay-here --session pair2 --print > '$W/rh2.out' 2>&1" Enter
if wait_file "$W/rh2.out"; then
  out2="$(cat "$W/rh2.out")"
  chk_has "$out2" "dir     : $OWNERDIR" "relay-here --dir = @aipair-dir (not the caller's cwd)"
  chk_has "$out2" "'--dir' '$OWNERDIR'" "relay launch carries --dir <@aipair-dir>"
  n=$((n+1)); if printf '%s' "$out2" | grep -E '^(dir|launch)' | grep -qE "(: |')/($| |')"; then echo "FAIL relay-here used the caller's / cwd for --dir"; fail=1; else echo "ok   caller cwd (/) ignored for --dir"; fi
else n=$((n+1)); echo "FAIL relay-here (dir) produced no output"; sed 's/^/     /' "$W/rh2.out" 2>/dev/null || true; fail=1; fi
reset_server

echo "# [2c] an explicit --dir overrides @aipair-dir"
"$REAL_TMUX" -L "$SOCKET" new-session -d -s pair3 -c "$W/proj"
"$REAL_TMUX" -L "$SOCKET" set -t pair3 @aipair-dir "$W/owner"
CUSTOM="$W/custom"; mkdir -p "$CUSTOM"
BR3="$("$REAL_TMUX" -L "$SOCKET" split-window -t pair3 -P -F '#{pane_id}' -c "$W/proj")"
"$REAL_TMUX" -L "$SOCKET" select-pane -t "$BR3" -T bridge
Q1="$("$REAL_TMUX" -L "$SOCKET" list-panes -t pair3 -F '#{pane_id}' | head -1)"
"$REAL_TMUX" -L "$SOCKET" send-keys -t "$Q1" "AIPAIR_RELAY_BIN='$STUB' aipair-relay-here --session pair3 --dir '$CUSTOM' --print > '$W/rh3.out' 2>&1" Enter
if wait_file "$W/rh3.out"; then
  chk_has "$(cat "$W/rh3.out")" "dir     : $CUSTOM" "explicit --dir wins over @aipair-dir"
else n=$((n+1)); echo "FAIL relay-here (--dir override) no output"; fail=1; fi
reset_server

echo "# [2d] relay-here --session uses EXACT match (a prefix must not hit a longer session)"
"$REAL_TMUX" -L "$SOCKET" new-session -d -s prefixtest -c "$W/proj"
"$REAL_TMUX" -L "$SOCKET" select-pane -t prefixtest -T bridge
P4="$("$REAL_TMUX" -L "$SOCKET" list-panes -t prefixtest -F '#{pane_id}' | head -1)"
"$REAL_TMUX" -L "$SOCKET" send-keys -t "$P4" "AIPAIR_RELAY_BIN='$STUB' aipair-relay-here --session prefix --print > '$W/rh4.out' 2>&1; echo rc=\$? >> '$W/rh4.out'" Enter
if wait_file "$W/rh4.out"; then
  out4="$(cat "$W/rh4.out")"
  n=$((n+1)); if printf '%s' "$out4" | grep -q 'rc=0'; then echo "FAIL relay-here matched a prefix session ('prefix' → 'prefixtest')"; fail=1; else echo "ok   prefix '--session prefix' rejected (no exact match)"; fi
  chk_has "$out4" "完全一致" "relay-here reports the exact-match requirement"
else n=$((n+1)); echo "FAIL relay-here (prefix) produced no output"; fail=1; fi
reset_server

echo "# [2e] a relative --dir is resolved to an absolute canonical path (caller/bridge agree)"
"$REAL_TMUX" -L "$SOCKET" new-session -d -s pair4 -c "$W/proj"
mkdir -p "$W/proj/sub"
BR5="$("$REAL_TMUX" -L "$SOCKET" split-window -t pair4 -P -F '#{pane_id}' -c "$W/proj")"
"$REAL_TMUX" -L "$SOCKET" select-pane -t "$BR5" -T bridge
Q5="$("$REAL_TMUX" -L "$SOCKET" list-panes -t pair4 -F '#{pane_id}' | head -1)"
ABS="$(cd "$W/proj/sub" && pwd -P)"
"$REAL_TMUX" -L "$SOCKET" send-keys -t "$Q5" "cd '$W/proj' && AIPAIR_RELAY_BIN='$STUB' aipair-relay-here --session pair4 --dir sub --print > '$W/rh5.out' 2>&1" Enter
if wait_file "$W/rh5.out"; then
  chk_has "$(cat "$W/rh5.out")" "dir     : $ABS" "relative --dir sub → absolute canonical ($ABS)"
else n=$((n+1)); echo "FAIL relay-here (relative --dir) no output"; fail=1; fi
reset_server

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
