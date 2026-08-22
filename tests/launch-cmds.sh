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
cleanup() { "$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true; rm -rf "$W"; }; trap cleanup EXIT
mkdir -p "$W/bin" "$W/proj"
for s in claude codex aipair-relay peer-log; do
  printf '#!/usr/bin/env bash\necho "cmd=%s self=${AI_SELF:-} peer=${AI_PEER:-}"\nfor a in "$@"; do printf "[%%s]\\n" "$a"; done\n' "$s" > "$W/bin/$s"
done
printf '#!/usr/bin/env bash\nexit 0\n' > "$W/bin/clear"
printf '#!/usr/bin/env bash\nexec %q -L %q "$@"\n' "$REAL_TMUX" "$SOCKET" > "$W/bin/tmux"
chmod +x "$W"/bin/*; export PATH="$W/bin:$REPO/bin:$PATH"
# Hermetic: nothing from the parent environment (a live pair, a user's AIPAIR_* settings)
# may leak into the checked lines.
unset TMUX AI_SELF AI_PEER BASH_ENV ENV
while IFS= read -r v; do unset "$v"; done < <(compgen -v AIPAIR_ || true)
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
chk "$(run loop bridge)" "cmd=aipair-relay self=bridge peer=${J}[--max-rounds]${J}[20]${J}[--stop]${J}[完了です]${J}[--stop-side]${J}[codex]" "bridge: relay with default args"
chk "$(run loop claude)" "cmd=claude self=claude peer=codex${J}[--dangerously-skip-permissions]" "claude: bypass flag under --unsafe, AI_SELF/AI_PEER exported"
chk "$(run loop codex)"  "cmd=codex self=codex peer=claude${J}[--dangerously-bypass-approvals-and-sandbox]" "codex: bypass flag under --unsafe, AI_SELF/AI_PEER exported"
chk "$(line loop session)" "$(aipair name "$W/proj")" "session line = aipair name"

echo "# [1b] D1: safe by default, permission-bypass only with --unsafe"
# safe default (no --unsafe): agents get NO flags (their normal permission prompts)
chk "$(env AIPAIR_DRY_RUN=1 aipair "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex claude " "interactive: no bypass flag by default"
chk "$(env AIPAIR_DRY_RUN=1 aipair "$W/proj" | sed -n 's/^codex:  *//p')" "clear; env AI_SELF=codex AI_PEER=claude codex " "interactive: codex no bypass flag by default"
# --unsafe adds the bypass flags
chk "$(env AIPAIR_UNSAFE=1 AIPAIR_DRY_RUN=1 aipair "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex claude --dangerously-skip-permissions" "AIPAIR_UNSAFE=1: bypass flag added"
# explicit flags win even in safe mode
chk "$(env AIPAIR_CLAUDE_FLAGS='--model opus' AIPAIR_DRY_RUN=1 aipair "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex claude --model opus" "explicit AIPAIR_CLAUDE_FLAGS wins in safe mode"
# `aipair loop` without --unsafe is refused (exit 2), nothing printed to stdout
n=$((n+1)); rc=0; out="$(AIPAIR_DRY_RUN=1 aipair loop "$W/proj" 2>/dev/null)" || rc=$?
if [ "$rc" = 2 ] && [ -z "$out" ]; then echo "ok   loop without --unsafe → exit 2, no launch"; else echo "FAIL loop refuse: rc=$rc out=$out"; fail=1; fi
# loop ALWAYS carries the bypass flag under --unsafe, even if the user blanks/customises flags
chk "$(env AIPAIR_UNSAFE=1 AIPAIR_CLAUDE_FLAGS= AIPAIR_CODEX_FLAGS= AIPAIR_DRY_RUN=1 aipair loop "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex claude --dangerously-skip-permissions" "loop: empty AIPAIR_CLAUDE_FLAGS still gets the bypass"
chk "$(env AIPAIR_UNSAFE=1 AIPAIR_CODEX_FLAGS= AIPAIR_DRY_RUN=1 aipair loop "$W/proj" | sed -n 's/^codex:  *//p')" "clear; env AI_SELF=codex AI_PEER=claude codex --dangerously-bypass-approvals-and-sandbox" "loop: empty AIPAIR_CODEX_FLAGS still gets the bypass"
chk "$(env AIPAIR_UNSAFE=1 AIPAIR_CLAUDE_FLAGS='--model opus' AIPAIR_DRY_RUN=1 aipair loop "$W/proj" | sed -n 's/^claude:  *//p')" "clear; env AI_SELF=claude AI_PEER=codex claude --model opus --dangerously-skip-permissions" "loop: custom flags preserved AND bypass appended"

echo "# [2] values with shell metacharacters arrive as ONE argument each"
chk "$(run loop bridge "AIPAIR_STOP=it's done" | sed -n '5p')" "[it's done]" "apostrophe in AIPAIR_STOP"
chk "$(run loop bridge 'AIPAIR_STOP=say "ok" $HOME; rm -rf /' | sed -n '5p')" '[say "ok" $HOME; rm -rf /]' "double quotes, \$VAR and ; are literal (no expansion, no injection)"
chk "$(run loop bridge 'AIPAIR_STOP=LGTM||完了です' | sed -n '5p')" "[LGTM||完了です]" "README example with || separator"
chk "$(run loop bridge 'AIPAIR_STOP=' | sed -n '5p')" "[完了です]" "empty AIPAIR_STOP falls back to the default"
chk "$(run loop bridge 'AIPAIR_MAX_ROUNDS=100' 'AIPAIR_STOP_SIDE=both' | sed -n '3p;7p' | paste -sd' ')" "[100] [both]" "max-rounds / stop-side pass through"

echo "# [3] endless mode"
chk "$(run loop bridge AIPAIR_ENDLESS=1 'AIPAIR_TASK_LIST=my tasks/todo list.md' 'AIPAIR_ALL_DONE=all done!' | sed -n '8,14p')" \
    "[--endless]${J}[--task-list]${J}[my tasks/todo list.md]${J}[--next-ask]${J}[次のタスクをください]${J}[--all-done]${J}[all done!]" "endless args, spaces and ! intact"
for off in 0 false no off; do
  chk "$(run loop bridge "AIPAIR_ENDLESS=$off" | grep -c -- '--endless' || true)" "0" "AIPAIR_ENDLESS=$off means off (as README promises)"
done
chk "$(line loop title AIPAIR_ENDLESS=1 'AIPAIR_ALL_DONE=fin')" "relay ● endless / max 20 / 終端「fin」/ Ctrl-C で停止" "endless title"

echo "# [4] agent flags are shell fragments (documented, backwards compatible)"
# `start` (interactive) mode: loop always appends the bypass flag, so test the fragment
# semantics here where AIPAIR_*_FLAGS maps 1:1 to argv.
chk "$(run start claude 'AIPAIR_CLAUDE_FLAGS=')" "cmd=claude self=claude peer=codex" "empty flags → no arguments"
chk "$(run start claude 'AIPAIR_CLAUDE_FLAGS=--model opus' | sed -n '2,3p' | paste -sd' ')" "[--model] [opus]" "two words → two arguments"
chk "$(run start codex 'AIPAIR_CODEX_FLAGS=--append-system-prompt "a b" -q' | sed -n '2,4p' | paste -sd' ')" "[--append-system-prompt] [a b] [-q]" "quoted word stays one argument"

echo "# [5] collaborate mode (plain start)"
chk "$(run start bridge)" "cmd=peer-log self=bridge peer=${J}[both]${J}[--watch]${J}[--last]${J}[15]" "bridge: peer-log both --watch --last 15"
chk "$(line start title)" "bridge  (claude × codex — peer-log both --watch)" "bridge title"

echo "# [6] the same lines under every installed shell (sh/dash/bash/zsh/fish)"
STOPV='it'"'"'s "x" $HOME;'
WANT_BRIDGE="cmd=aipair-relay self=bridge peer=${J}[--max-rounds]${J}[20]${J}[--stop]${J}[$STOPV]${J}[--stop-side]${J}[codex]"
WANT_CLAUDE="cmd=claude self=claude peer=codex${J}[--dangerously-skip-permissions]"
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

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
