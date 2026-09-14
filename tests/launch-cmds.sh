#!/usr/bin/env bash
# Regression tests for the command lines `aipair` types into its panes (AIPAIR_DRY_RUN=1).
# Each printed line is fed to a real shell whose PATH holds shims for claude / codex /
# aipair-relay / peer-log / clear that print their argv — so what is checked is exactly
# what the pane's shell would run, not how the string looks.
# tmux is shimmed onto a private, never-started socket: `aipair` only reads session state.
#
# Usage: bash tests/launch-cmds.sh      (exit 0 = all passed)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"
REAL_TMUX="$(command -v tmux)" || { echo "tmux not found" >&2; exit 2; }
W="$(mktemp -d "${TMPDIR:-/tmp}/aipair-launch.XXXXXX")"; SOCKET="aipair-launch-$$-$RANDOM"
cleanup() { "$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true; rm -f "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/$SOCKET" 2>/dev/null || true; rm -rf "$W"; }; trap cleanup EXIT
mkdir -p "$W/bin" "$W/proj"
for s in claude codex aipair-relay peer-log; do
  printf '#!/usr/bin/env bash\necho "cmd=%s self=${AI_SELF:-} peer=${AI_PEER:-}"\nfor a in "$@"; do printf "[%%s]\\n" "$a"; done\n' "$s" > "$W/bin/$s"
done
printf '#!/usr/bin/env bash\nexit 0\n' > "$W/bin/clear"
# AIPAIR_TEST_TMUX_LOG (opt-in per command): the shim appends each tmux invocation there, so a test can
# prove that a rejected launch never called tmux at all.
printf '#!/usr/bin/env bash\n[ -n "${AIPAIR_TEST_TMUX_LOG:-}" ] && printf "%%s\\n" "$*" >> "$AIPAIR_TEST_TMUX_LOG"\nexec %q -L %q "$@"\n' "$REAL_TMUX" "$SOCKET" > "$W/bin/tmux"
chmod +x "$W"/bin/*; export PATH="$W/bin:$REPO/bin:$PATH"
# Preflight: prove the tmux shim provably targets the PRIVATE socket before anything runs, so a
# broken shim can never touch the user's default server (guardrail; same as the other tmux tests).
# Then kill-server so the dry-run checks below still see a pristine (server-less) private socket.
"$REAL_TMUX" -L "$SOCKET" new-session -d -s probe 2>/dev/null || true
want="$("$REAL_TMUX" -L "$SOCKET" display-message -p -t probe '#{socket_path}' 2>/dev/null || true)"
got="$(tmux display-message -p -t probe '#{socket_path}' 2>/dev/null || true)"
"$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true
if [ -z "$got" ] || [ "$got" != "$want" ] || [ "$(basename "$got")" != "$SOCKET" ]; then
  echo "tmux shim not effective (got '$got', want '$want') — refusing to touch the default server" >&2; exit 2
fi
# Hermetic: nothing from the parent environment (a live pair, a user's AIPAIR_* settings)
# may leak into the checked lines.
unset TMUX AI_SELF AI_PEER BASH_ENV ENV
while IFS= read -r v; do unset "$v"; done < <(compgen -v AIPAIR_ || true)
# Fixed peer pins so the launch lines are deterministic (aipair honours pre-set values;
# it generates a random uuid / the wall-clock epoch otherwise — covered by [1c]).
S=11111111-1111-1111-1111-111111111111   # a valid fixed UUID (aipair now validates the pin)
K=1700000000                             # a valid fixed epoch (aipair validates it is numeric)
export AIPAIR_CLAUDE_SESSION="$S" AIPAIR_CODEX_SINCE="$K"
P="AIPAIR_CLAUDE_SESSION=$S AIPAIR_CODEX_SINCE=$K"   # the two env pins on every pane line
mkdir -p "$W/zdot" "$W/xdg"     # empty rc dirs so zsh/fish run without the user's config

fail=0; n=0
chk() { n=$((n+1)); if [ "$1" = "$2" ]; then echo "ok   $3"; else echo "FAIL $3"; printf -- '--- got ---\n%s\n--- want ---\n%s\n' "$1" "$2"; fail=1; fi; }
# line MODE PANE [VAR=value ...] → the command line aipair would type into PANE
# AIPAIR_UNSAFE=1 so loop-mode launch tests get past the D1 safety gate (they exercise the
# bridge/relay args, not the gate — which has its own section below).
line() { local mode="$1" pane="$2"; shift 2; env AIPAIR_UNSAFE=1 "$@" AIPAIR_DRY_RUN=1 aipair "$mode" "$W/proj" | sed -n "s/^$pane:  *//p"; }
# run MODE PANE [VAR=value ...] → what the pane's shell does with that line (argv dump)
run() { bash -c "$(line "$@")"; }
J=$'\n'

echo "# [1] loop defaults"
chk "$(run loop bridge)" "cmd=aipair-relay self=bridge peer=${J}[--max-rounds]${J}[20]${J}[--stop]${J}[[AIPAIR_REVIEW_OK]]${J}[--stop-side]${J}[codex]" "bridge: relay with default args"
chk "$(run loop claude)" "cmd=claude self=claude peer=codex${J}[--session-id]${J}[$S]${J}[--dangerously-skip-permissions]" "claude: --session-id pin + bypass under --unsafe, AI_SELF/AI_PEER exported"
chk "$(run loop codex)"  "cmd=codex self=codex peer=claude${J}[--dangerously-bypass-approvals-and-sandbox]" "codex: bypass flag under --unsafe, AI_SELF/AI_PEER exported"
chk "$(line loop session)" "$(aipair name "$W/proj")" "session line = aipair name"

echo "# [1b] D1: safe by default, permission-bypass only with --unsafe"
# safe default (no --unsafe): agents get NO flags (their normal permission prompts)
chk "$(env AIPAIR_DRY_RUN=1 aipair "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex $P claude --session-id $S " "interactive: peer pins + --session-id, no bypass flag by default"
chk "$(env AIPAIR_DRY_RUN=1 aipair "$W/proj" | sed -n 's/^codex:  *//p')" "clear; env AI_SELF=codex AI_PEER=claude $P codex " "interactive: codex peer pins, no bypass flag by default"
# --unsafe adds the bypass flags
chk "$(env AIPAIR_UNSAFE=1 AIPAIR_DRY_RUN=1 aipair "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex $P claude --session-id $S --dangerously-skip-permissions" "AIPAIR_UNSAFE=1: bypass flag added"
# explicit flags win even in safe mode
chk "$(env AIPAIR_CLAUDE_FLAGS='--model opus' AIPAIR_DRY_RUN=1 aipair "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex $P claude --session-id $S --model opus" "explicit AIPAIR_CLAUDE_FLAGS wins in safe mode"
# `aipair loop` without --unsafe is refused (exit 2), nothing printed to stdout
n=$((n+1)); rc=0; out="$(AIPAIR_DRY_RUN=1 aipair loop "$W/proj" 2>/dev/null)" || rc=$?
if [ "$rc" = 2 ] && [ -z "$out" ]; then echo "ok   loop without --unsafe → exit 2, no launch"; else echo "FAIL loop refuse: rc=$rc out=$out"; fail=1; fi
# loop ALWAYS carries the bypass flag under --unsafe, even if the user blanks/customises flags
chk "$(env AIPAIR_UNSAFE=1 AIPAIR_CLAUDE_FLAGS= AIPAIR_CODEX_FLAGS= AIPAIR_DRY_RUN=1 aipair loop "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex $P claude --session-id $S --dangerously-skip-permissions" "loop: empty AIPAIR_CLAUDE_FLAGS still gets the bypass"
chk "$(env AIPAIR_UNSAFE=1 AIPAIR_CODEX_FLAGS= AIPAIR_DRY_RUN=1 aipair loop "$W/proj" | sed -n 's/^codex:  *//p')" "clear; env AI_SELF=codex AI_PEER=claude $P codex --dangerously-bypass-approvals-and-sandbox" "loop: empty AIPAIR_CODEX_FLAGS still gets the bypass"
chk "$(env AIPAIR_UNSAFE=1 AIPAIR_CLAUDE_FLAGS='--model opus' AIPAIR_DRY_RUN=1 aipair loop "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex $P claude --session-id $S --dangerously-skip-permissions --model opus" "loop: bypass is prepended, custom flags follow"
# the bypass is a FIXED token BEFORE the fragment, so a '#' in the fragment can't comment it out
chk "$(env AIPAIR_UNSAFE=1 AIPAIR_CLAUDE_FLAGS='--model opus # --dangerously-skip-permissions' AIPAIR_DRY_RUN=1 aipair loop "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex $P claude --session-id $S --dangerously-skip-permissions --model opus # --dangerously-skip-permissions" "loop: a '#' in the fragment cannot comment out the bypass (it is prepended)"

echo "# [1c] with no pins pre-set, aipair generates a real session id + epoch and pins to them"
GEN="$(env -u AIPAIR_CLAUDE_SESSION -u AIPAIR_CODEX_SINCE AIPAIR_DRY_RUN=1 aipair "$W/proj" | sed -n 's/^claude:  *//p')"
gsid="$(printf '%s\n' "$GEN" | sed -n 's/.*AIPAIR_CLAUDE_SESSION=\([^ ]*\).*/\1/p')"
gsess="$(printf '%s\n' "$GEN" | sed -n 's/.*--session-id \([^ ]*\).*/\1/p')"
gsince="$(printf '%s\n' "$GEN" | sed -n 's/.*AIPAIR_CODEX_SINCE=\([^ ]*\).*/\1/p')"
n=$((n+1)); if printf '%s' "$gsid" | grep -qE '^[0-9a-fA-F-]{16,}$'; then echo "ok   generated AIPAIR_CLAUDE_SESSION looks like a uuid"; else echo "FAIL generated session id: '$gsid'"; fail=1; fi
chk "$gsess" "$gsid" "claude --session-id equals the exported AIPAIR_CLAUDE_SESSION pin"
n=$((n+1)); if printf '%s' "$gsince" | grep -qE '^[0-9]+([.][0-9]+)?$'; then echo "ok   generated AIPAIR_CODEX_SINCE is an epoch"; else echo "FAIL generated since: '$gsince'"; fail=1; fi

echo "# [1d] a caller-supplied pin is validated (no shell injection, fail-closed)"
for bad in 'x; echo PIN_INJECTED #' '$(touch /tmp/aipair-pwn)' 'a b' 'notauuid'; do
  n=$((n+1)); rc=0; out="$(env AIPAIR_CLAUDE_SESSION="$bad" AIPAIR_DRY_RUN=1 aipair "$W/proj" 2>/dev/null)" || rc=$?
  if [ "$rc" = 2 ] && [ -z "$out" ] && ! printf '%s' "$out" | grep -q PIN_INJECTED; then
    echo "ok   AIPAIR_CLAUDE_SESSION='$bad' → refused (exit 2, nothing printed)"
  else echo "FAIL bad session pin not refused: rc=$rc out=$out"; fail=1; fi
done
for bad in '1; rm -rf /' '1e3' 'abc'; do
  n=$((n+1)); rc=0; out="$(env AIPAIR_CODEX_SINCE="$bad" AIPAIR_DRY_RUN=1 aipair "$W/proj" 2>/dev/null)" || rc=$?
  if [ "$rc" = 2 ] && [ -z "$out" ]; then echo "ok   AIPAIR_CODEX_SINCE='$bad' → refused (exit 2)"; else echo "FAIL bad since not refused: rc=$rc out=$out"; fail=1; fi
done
# a valid fractional epoch IS accepted
n=$((n+1)); if env AIPAIR_CODEX_SINCE=1700000000.5 AIPAIR_DRY_RUN=1 aipair "$W/proj" >/dev/null 2>&1; then echo "ok   AIPAIR_CODEX_SINCE=1700000000.5 accepted"; else echo "FAIL fractional epoch rejected"; fail=1; fi

echo "# [2] values with shell metacharacters arrive as ONE argument each"
chk "$(run loop bridge "AIPAIR_STOP=it's done" | sed -n '5p')" "[it's done]" "apostrophe in AIPAIR_STOP"
chk "$(run loop bridge 'AIPAIR_STOP=say "ok" $HOME; rm -rf /' | sed -n '5p')" '[say "ok" $HOME; rm -rf /]' "double quotes, \$VAR and ; are literal (no expansion, no injection)"
chk "$(run loop bridge 'AIPAIR_STOP=LGTM||完了です' | sed -n '5p')" "[LGTM||完了です]" "README example with || separator"
chk "$(run loop bridge 'AIPAIR_STOP=' | sed -n '5p')" "[[AIPAIR_REVIEW_OK]]" "empty AIPAIR_STOP falls back to the default sentinel"
chk "$(run loop bridge 'AIPAIR_MAX_ROUNDS=100' 'AIPAIR_STOP_SIDE=both' | sed -n '3p;7p' | paste -sd' ')" "[100] [both]" "max-rounds / stop-side pass through"

echo "# [3] endless mode"
chk "$(run loop bridge AIPAIR_ENDLESS=1 'AIPAIR_TASK_LIST=my tasks/todo list.md' 'AIPAIR_ALL_DONE=all done!' | sed -n '8,16p')" \
    "[--endless]${J}[--task-list]${J}[my tasks/todo list.md]${J}[--next-ask]${J}[[AIPAIR_NEXT]]${J}[--all-done]${J}[all done!]${J}[--human-required]${J}[[AIPAIR_HUMAN_REQUIRED]]" "endless args (incl. --human-required), spaces and ! intact"
for off in 0 false no off; do
  chk "$(run loop bridge "AIPAIR_ENDLESS=$off" | grep -c -- '--endless' || true)" "0" "AIPAIR_ENDLESS=$off means off (as README promises)"
done
chk "$(line loop title AIPAIR_ENDLESS=1 'AIPAIR_ALL_DONE=fin')" "relay ● endless / max 20 / 終端 DONE/HUMAN / Ctrl-C で停止" "endless title (2 終端: ALL_DONE / HUMAN_REQUIRED)"

echo "# [4] agent flags are shell fragments (documented, backwards compatible)"
# `start` (interactive) mode: loop always appends the bypass flag, so test the fragment
# semantics here where AIPAIR_*_FLAGS maps 1:1 to argv.
chk "$(run start claude 'AIPAIR_CLAUDE_FLAGS=')" "cmd=claude self=claude peer=codex${J}[--session-id]${J}[$S]" "empty flags → only the --session-id pin"
chk "$(run start claude 'AIPAIR_CLAUDE_FLAGS=--model opus' | sed -n '4,5p' | paste -sd' ')" "[--model] [opus]" "two words → two arguments (after the --session-id pin)"
chk "$(run start codex 'AIPAIR_CODEX_FLAGS=--append-system-prompt "a b" -q' | sed -n '2,4p' | paste -sd' ')" "[--append-system-prompt] [a b] [-q]" "quoted word stays one argument"

echo "# [5] collaborate mode (plain start)"
chk "$(run start bridge)" "cmd=peer-log self=bridge peer=${J}[both]${J}[--watch]${J}[--last]${J}[15]" "bridge: peer-log both --watch --last 15"
chk "$(line start title)" "bridge  (claude × codex — peer-log both --watch)" "bridge title"

echo "# [6] the same lines under every installed shell (sh/dash/bash/zsh/fish)"
STOPV='it'"'"'s "x" $HOME;'
WANT_BRIDGE="cmd=aipair-relay self=bridge peer=${J}[--max-rounds]${J}[20]${J}[--stop]${J}[$STOPV]${J}[--stop-side]${J}[codex]"
WANT_CLAUDE="cmd=claude self=claude peer=codex${J}[--session-id]${J}[$S]${J}[--dangerously-skip-permissions]"
LINE_BRIDGE="$(line loop bridge "AIPAIR_STOP=$STOPV")"; LINE_CLAUDE="$(line loop claude)"
for sh in sh dash bash zsh fish; do
  if ! command -v "$sh" >/dev/null 2>&1; then echo "skip $sh (not installed)"; continue; fi
  chk "$(env ZDOTDIR="$W/zdot" XDG_CONFIG_HOME="$W/xdg" "$sh" -c "$LINE_BRIDGE" 2>/dev/null || true)" "$WANT_BRIDGE" "$sh: bridge line → identical argv"
  chk "$(env ZDOTDIR="$W/zdot" XDG_CONFIG_HOME="$W/xdg" "$sh" -c "$LINE_CLAUDE" 2>/dev/null || true)" "$WANT_CLAUDE" "$sh: claude line → identical argv"
done

echo "# [7] AIPAIR_DRY_RUN is a boolean like AIPAIR_ENDLESS"
if "$REAL_TMUX" -L "$SOCKET" list-sessions >/dev/null 2>&1; then n=$((n+1)); echo "FAIL a tmux server was started by the dry runs above"; fail=1; else n=$((n+1)); echo "ok   dry runs started no tmux server on $SOCKET"; fi
for v in 1 true yes on ' On '; do
  chk "$(env "AIPAIR_DRY_RUN=$v" aipair start "$W/proj" | grep -c '^session: ' || true)" "1" "AIPAIR_DRY_RUN='$v' → dry run"
done
NAME="$(aipair name "$W/proj")"
for v in 0 false no off; do
  out="$(timeout 5 env "AIPAIR_DRY_RUN=$v" aipair start "$W/proj" </dev/null 2>/dev/null || true)"   # real start; attach fails (no tty)
  if [ -z "$out" ] && "$REAL_TMUX" -L "$SOCKET" has-session -t "=$NAME" 2>/dev/null; then n=$((n+1)); echo "ok   AIPAIR_DRY_RUN='$v' → real start (session created, nothing printed)"; else n=$((n+1)); echo "FAIL AIPAIR_DRY_RUN='$v': out='$out'"; fail=1; fi
  "$REAL_TMUX" -L "$SOCKET" kill-session -t "=$NAME" 2>/dev/null || true
done

echo "# [8] --start-side / AIPAIR_START_SIDE (Codex-first: the relay waits on Codex first — not a role swap)"
# argline PANE [VAR=value ...] aipair ARGS... → like line(), but with arbitrary aipair arguments (flags anywhere)
argline() { local pane="$1"; shift; env AIPAIR_UNSAFE=1 AIPAIR_DRY_RUN=1 "$@" | sed -n "s/^$pane:  *//p"; }
argrun() { bash -c "$(argline "$@")"; }
WANT_SS="cmd=aipair-relay self=bridge peer=${J}[--max-rounds]${J}[20]${J}[--stop]${J}[[AIPAIR_REVIEW_OK]]${J}[--stop-side]${J}[codex]${J}[--start-side]${J}[codex]"
chk "$(argrun bridge AIPAIR_START_SIDE=codex aipair loop "$W/proj")" "$WANT_SS" "AIPAIR_START_SIDE=codex → relay gets --start-side codex"
chk "$(argrun bridge aipair loop --start-side codex "$W/proj")" "$WANT_SS" "--start-side codex → relay gets --start-side codex"
chk "$(argrun bridge aipair loop --start-side=codex "$W/proj")" "$WANT_SS" "--start-side=codex → same"
chk "$(argrun bridge aipair --start-side codex loop "$W/proj")" "$WANT_SS" "flag before the subcommand → same"
chk "$(argrun bridge aipair loop "$W/proj" --start-side codex --start-side=codex)" "$WANT_SS" "the same value twice is accepted"
chk "$(argrun bridge AIPAIR_START_SIDE=claude aipair loop --start-side codex "$W/proj")" "$WANT_SS" "flag beats env (env claude + flag codex → codex)"
chk "$(argrun bridge AIPAIR_START_SIDE=codex aipair loop --start-side claude "$W/proj" | tail -2 | paste -sd' ')" "[--start-side] [claude]" "flag beats env (env codex + flag claude → claude)"
chk "$(run loop bridge 'AIPAIR_START_SIDE=' | grep -c -- '--start-side' || true)" "0" "empty AIPAIR_START_SIDE → no --start-side (the default line is unchanged)"
# The relay's own env is pinned to the EFFECTIVE value: a stale value held by an old tmux server never leaks in.
mkdir -p "$W/envbin"
printf '#!/usr/bin/env bash\necho "AIPAIR_START_SIDE=[${AIPAIR_START_SIDE-<unset>}]"\n' > "$W/envbin/aipair-relay"; chmod +x "$W/envbin/aipair-relay"
envrun() { local l; l="$(argline bridge "$@")"; env AIPAIR_START_SIDE=stale PATH="$W/envbin:$PATH" bash -c "$l"; }
chk "$(envrun aipair loop "$W/proj")" "AIPAIR_START_SIDE=[]" "unset → the relay's env is pinned empty (stale server value neutralized)"
chk "$(envrun aipair loop --start-side codex "$W/proj")" "AIPAIR_START_SIDE=[codex]" "the pin carries the flag's value"
chk "$(envrun AIPAIR_START_SIDE=claude aipair loop --start-side codex "$W/proj")" "AIPAIR_START_SIDE=[codex]" "the pin carries the effective value (flag over env)"
# Rejected: exit 2, nothing on stdout, and tmux is never called (the shim logs every call). Deliberately NOT a
# dry run — a check that failed to fire would really reach tmux (the private socket) and show up in the log.
reject() {   # reject DESC [VAR=value ...] aipair ARGS...
  local desc="$1" rc=0 out err; shift
  : > "$W/tmuxcalls"
  out="$(timeout 10 env AIPAIR_UNSAFE=1 AIPAIR_TEST_TMUX_LOG="$W/tmuxcalls" "$@" </dev/null 2>"$W/err")" || rc=$?
  err="$(cat "$W/err")"; n=$((n+1))
  if [ "$rc" = 2 ] && [ -z "$out" ] && [ ! -s "$W/tmuxcalls" ] && printf '%s' "$err" | grep -q 'start-side\|START_SIDE'; then
    echo "ok   $desc"
  else
    echo "FAIL $desc (rc=$rc stdout='$out' stderr='$err' tmux: $(paste -sd';' "$W/tmuxcalls"))"; fail=1
  fi
}
reject "trailing --start-side (no value) → exit 2, tmux untouched" aipair loop "$W/proj" --start-side
reject "--start-side= (empty) → exit 2, tmux untouched" aipair loop --start-side= "$W/proj"
reject "--start-side typo → exit 2, tmux untouched" aipair loop --start-side typo "$W/proj"
reject "--start-side <dir> (a directory is not a value) → exit 2, tmux untouched" aipair loop --start-side "$W/proj"
reject "two different values → exit 2, tmux untouched" aipair loop --start-side codex --start-side=claude "$W/proj"
reject "AIPAIR_START_SIDE=typo aipair loop → exit 2, tmux untouched" AIPAIR_START_SIDE=typo aipair loop "$W/proj"
for sub in "" start attach stop name status; do
  reject "aipair ${sub:-(no subcommand)} --start-side codex → exit 2 (loop only), tmux untouched" aipair ${sub:+"$sub"} --start-side codex "$W/proj"
done
# The env is read ONLY by loop: an invalid AIPAIR_START_SIDE cannot break the other commands.
chk "$(env AIPAIR_START_SIDE=typo aipair name "$W/proj" 2>&1)" "$(aipair name "$W/proj")" "AIPAIR_START_SIDE=typo: 'aipair name' is unaffected"
out="$(env AIPAIR_START_SIDE=typo AIPAIR_DRY_RUN=1 aipair start "$W/proj" 2>&1 || true)"
chk "$(printf '%s\n' "$out" | grep -c '^session: ' || true)/$(printf '%s\n' "$out" | grep -c 'start-side\|START_SIDE' || true)" "1/0" "AIPAIR_START_SIDE=typo: 'aipair start' still runs (dry run) and mentions no start side"

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
