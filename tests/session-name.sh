#!/usr/bin/env bash
# Regression tests for aipair's tmux session naming (`aipair name` / `aipair stop` / start).
#
# Runs against a PRIVATE tmux server (unique -L socket per run) so it can never touch a
# live pair and parallel runs don't see each other. Inside a tmux pane $TMUX overrides
# TMUX_TMPDIR, so isolation is a `tmux` shim on PATH that forces -L; the shim is verified
# via #{socket_path} before anything else runs, and cleanup never calls a bare tmux.
#
# Usage: bash tests/session-name.sh      (exit 0 = all passed)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"
REAL_TMUX="$(command -v tmux)" || { echo "tmux not found" >&2; exit 2; }
W="$(mktemp -d "${TMPDIR:-/tmp}/aipair-test.XXXXXX")"
SOCKET="aipair-test-$$-$RANDOM"
cleanup() { "$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true; rm -rf "$W"; }
trap cleanup EXIT

mkdir -p "$W/bin" "$W/a/api" "$W/b/api" "$W/Case" "$W/case"
# The shim also pins `exit-empty off` on every call so the private server survives an
# empty moment (tmux 3.4 exits an empty server at once; 3.2a lingered, hiding this).
printf '#!/usr/bin/env bash\n%q -L %q start-server 2>/dev/null || true\n%q -L %q set-option -g exit-empty off 2>/dev/null || true\nexec %q -L %q "$@"\n' \
  "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" > "$W/bin/tmux"; chmod +x "$W/bin/tmux"
export PATH="$W/bin:$REPO/bin:$PATH"; unset TMUX
"$REAL_TMUX" -L "$SOCKET" new-session -d -s shim-probe            # a server with no session exits at once
want="$("$REAL_TMUX" -L "$SOCKET" display-message -p -t shim-probe '#{socket_path}')"
got="$(tmux display-message -p -t shim-probe '#{socket_path}')"     # through the shim
"$REAL_TMUX" -L "$SOCKET" kill-session -t shim-probe
if [ "$got" != "$want" ] || [ "$(basename "$got")" != "$SOCKET" ]; then
  echo "tmux shim not effective (got '$got', want '$want') — aborting before touching any server" >&2; exit 2
fi

fail=0; n=0
chk() { n=$((n+1)); if [ "$1" = "$2" ]; then echo "ok   $3"; else echo "FAIL $3: got '$1' want '$2'"; fail=1; fi; }
pass() { n=$((n+1)); echo "ok   $1"; }
flunk() { n=$((n+1)); echo "FAIL $1"; fail=1; }
alive() { tmux has-session -t "=$1" 2>/dev/null; }
pane_of() { tmux list-panes -s -t "=$1" -F '#{pane_id}' | head -1; }   # send-keys rejects "=name"
start_pair() {  # headless real start: --version makes the agent panes exit at once (no TUI)
  AIPAIR_CLAUDE_FLAGS=--version AIPAIR_CODEX_FLAGS=--version \
    timeout 3 script -qec "aipair $1" /dev/null >/dev/null 2>&1 || true
}
swapcase_last() { python3 -c 'import os,sys; d,b=os.path.split(sys.argv[1]); print(os.path.join(d,b.swapcase()))' "$1"; }

echo "# [1] distinct directories with the same basename get distinct names"
NA=$(aipair name "$W/a/api"); NB=$(aipair name "$W/b/api")
if [ "$NA" != "$NB" ]; then pass "a/api ($NA) != b/api ($NB)"; else flunk "same name for a/api and b/api"; fi
if [[ "$NA" =~ ^aipair-api-[0-9a-f]{12}$ ]]; then pass "format aipair-<base>-<12 hex>"; else flunk "format: $NA"; fi
chk "$(aipair name "$W/a/api/")" "$NA" "trailing slash is ignored"
ln -s "$W/a/api" "$W/link-api"; chk "$(aipair name "$W/link-api")" "$NA" "symlink resolves to the target's name"

echo "# [2] case: distinct on a case-sensitive fs, merged on a case-insensitive fs"
if [ "$(aipair name "$W/Case")" != "$(aipair name "$W/case")" ]; then pass "Case/case stay distinct under $W"; else flunk "Case/case merged"; fi
CI_DIR=""; SW="$(swapcase_last "$REPO")"
if [ "$SW" != "$REPO" ] && [ -d "$SW" ] && [ "$SW" -ef "$REPO" ]; then CI_DIR="$REPO"; fi
if [ -n "$CI_DIR" ]; then
  chk "$(aipair name "$SW")" "$(aipair name "$CI_DIR")" "case variants of one dir → one name (case-insensitive fs at $CI_DIR)"
else echo "skip case-insensitive fs test (repo is on a case-sensitive fs)"; fi

echo "# [3] legacy (hash-less) session of the SAME directory is adopted"
tmux new-session -d -s aipair-api -c "$W/a/api"
chk "$(aipair name "$W/a/api")" "aipair-api" "name → legacy"
chk "$(aipair name "$W/b/api")" "$NB" "other dir with same basename does not adopt it"
chk "$(aipair stop "$W/b/api")" "aipair: no running session $NB" "stop other dir leaves it alone (no prefix match)"
if alive aipair-api; then pass "legacy still alive"; else flunk "legacy killed"; fi
chk "$(aipair stop "$W/a/api")" "aipair: stopped aipair-api" "stop same dir kills legacy"
if alive aipair-api; then flunk "legacy still alive"; else pass "legacy gone"; fi

echo "# [4] legacy session of ANOTHER dir whose pane cd'ed here is NOT adopted (session_path, not pane cwd)"
tmux new-session -d -s aipair-api -c "$W/b/api"
tmux send-keys -t "$(pane_of aipair-api)" "cd '$W/a/api'" C-m
for _ in 1 2 3 4 5 6 7 8 9 10; do [ "$(tmux list-panes -s -t =aipair-api -F '#{pane_current_path}')" = "$W/a/api" ] && break; sleep 0.2; done
chk "$(tmux list-panes -s -t =aipair-api -F '#{pane_current_path}')" "$W/a/api" "(precondition) b/api's pane now sits in a/api"
chk "$(aipair name "$W/a/api")" "$NA" "a/api still resolves to its hashed name"
chk "$(aipair stop "$W/a/api")" "aipair: no running session $NA" "stop a/api does not kill b/api's legacy"
if alive aipair-api; then pass "b/api legacy survived"; else flunk "b/api legacy killed"; fi
chk "$(aipair name "$W/b/api")" "aipair-api" "b/api still owns it"
tmux kill-session -t =aipair-api

if [ -n "$CI_DIR" ]; then
  echo "# [5] legacy identity compares by inode (-ef): created under another spelling"
  LN="$(aipair name "$CI_DIR" | sed -E 's/-[0-9a-f]{12}$//')"
  tmux new-session -d -s "$LN" -c "$SW"
  chk "$(aipair name "$CI_DIR")" "$LN" "legacy created with swapped-case -c is adopted"
  tmux kill-session -t "=$LN"
fi

echo "# [6] a new-format session that belongs to ANOTHER directory (hash collision) is never attached/stopped"
tmux new-session -d -s "$NA" -c "$W/b/api"          # a/api's name, but b/api's session_path
for cmd in name stop attach; do
  rc=0; out="$(aipair "$cmd" "$W/a/api" 2>&1)" || rc=$?
  if [ "$rc" = 1 ] && [[ "$out" == *"hash collision"* ]]; then pass "aipair $cmd a/api → exit 1 with 'hash collision'"; else flunk "aipair $cmd a/api: rc=$rc out=$out"; fi
done
if alive "$NA"; then pass "the foreign session is untouched"; else flunk "the foreign session was killed"; fi
chk "$(aipair name "$W/b/api")" "$NB" "b/api itself is unaffected"
tmux kill-session -t "=$NA"

echo "# [7] a pair of directories whose 6-hex prefixes collide (the old suffix length) get distinct 12-hex names"
read -r C1 C2 < <(python3 - "$(cd "$W" && pwd -P)" <<'PY'
import hashlib, sys
base, seen = sys.argv[1], {}
for i in range(1, 200000):                      # 24-bit birthday bound: a hit is expected within a few thousand
    path = f"{base}/c{i}/api"
    h = hashlib.sha1(path.encode()).hexdigest()[:6]
    if h in seen:
        print(seen[h], path); break
    seen[h] = path
PY
)
mkdir -p "$C1" "$C2"; X1=$(aipair name "$C1"); X2=$(aipair name "$C2")
if [ "${X1:0:17}" = "${X2:0:17}" ]; then pass "(precondition) 6-hex prefixes collide: ${X1:0:17}"; else flunk "no 6-hex collision: $X1 / $X2"; fi
if [ "$X1" != "$X2" ]; then pass "12-hex names differ: $X1 / $X2"; else flunk "12-hex names still collide: $X1"; fi

if command -v script >/dev/null; then
  echo "# [8] real start: layout + options; new-format session beats a legacy one"
  start_pair "$W/a/api"
  if alive "$NA"; then pass "session $NA created"; else flunk "no session $NA"; fi
  chk "$(tmux list-panes -s -t "=$NA" | wc -l)" "3" "3 panes"
  chk "$(tmux show -t "$NA" -v mouse)" "on" "mouse on"
  chk "$(tmux show -t "$NA" -v pane-border-status)" "top" "pane-border-status top"
  chk "$(tmux list-panes -s -t "=$NA" -F '#{pane_title}' | grep -c -E '^(claude|codex|bridge)' || true)" "3" "pane titles claude/codex/bridge"
  tmux new-session -d -s aipair-api -c "$W/a/api"      # legacy for the SAME dir, coexisting
  chk "$(aipair name "$W/a/api")" "$NA" "new-format wins over legacy of the same dir"
  chk "$(aipair stop "$W/a/api")" "aipair: stopped $NA" "stop kills the new-format session"
  chk "$(aipair name "$W/a/api")" "aipair-api" "…then the legacy one is adopted"
  tmux kill-session -t =aipair-api

  echo "# [9] owner stamp (@aipair-dir) survives 'attach-session -c', which rewrites session_path"
  start_pair "$W/a/api"
  chk "$(tmux show -t "$NA" -v @aipair-dir)" "$(cd "$W/a/api" && pwd -P)" "@aipair-dir stamped at creation"
  timeout 2 script -qec "tmux attach -t '=$NA' -c '$W/b/api'" /dev/null >/dev/null 2>&1 || true
  chk "$(tmux list-sessions -F $'#{session_name}\t#{session_path}' | awk -F'\t' -v n="$NA" '$1==n{print $2}')" "$W/b/api" "(precondition) session_path now points at b/api"
  chk "$(aipair name "$W/a/api")" "$NA" "a/api still owns its session (no false 'hash collision')"
  chk "$(aipair name "$W/b/api")" "$NB" "b/api is not fooled by the rewritten session_path"
  chk "$(aipair stop "$W/a/api")" "aipair: stopped $NA" "stop a/api still works"
else echo "skip real-start tests (\`script\` not available)"; fi

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED) (socket $SOCKET)"
exit $fail
