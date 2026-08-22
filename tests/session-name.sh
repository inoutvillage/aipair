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
  # @aipair-codex-pane must be recorded (peer resolves the pair's Codex by this pane's process)
  CODEX_PANE="$(tmux list-panes -s -t "=$NA" -F '#{pane_id} #{pane_title}' | awk '$2=="codex"{print $1}')"
  chk "$(tmux show -t "$NA" -v @aipair-codex-pane)" "$CODEX_PANE" "@aipair-codex-pane points at the codex pane (real tmux set)"
  tmux new-session -d -s aipair-api -c "$W/a/api"      # legacy for the SAME dir, coexisting
  chk "$(aipair name "$W/a/api")" "$NA" "new-format wins over legacy of the same dir"
  chk "$(aipair stop "$W/a/api")" "aipair: stopped $NA" "stop kills the new-format session"
  chk "$(aipair name "$W/a/api")" "aipair-api" "…then the legacy one is adopted"
  tmux kill-session -t =aipair-api

  echo "# [9] owner stamp (@aipair-dir) survives 'attach-session -c', which rewrites session_path"
  start_pair "$W/a/api"
  chk "$(tmux show -t "$NA" -v @aipair-dir)" "$(cd "$W/a/api" && pwd -P)" "@aipair-dir stamped at creation"
  timeout 2 script -qec "tmux attach -t '=$NA' -c '$W/b/api'" /dev/null >/dev/null 2>&1 || true
  chk "$(tmux display-message -p -t "$NA" '#{session_path}')" "$W/b/api" "(precondition) session_path now points at b/api"
  chk "$(aipair name "$W/a/api")" "$NA" "a/api still owns its session (no false 'hash collision')"
  chk "$(aipair name "$W/b/api")" "$NB" "b/api is not fooled by the rewritten session_path"
  chk "$(aipair stop "$W/a/api")" "aipair: stopped $NA" "stop a/api still works"
else echo "skip real-start tests (\`script\` not available)"; fi

# [10] tmux 3.1 has neither `list-sessions -f` nor #{session_path} (both are 3.2+).
# session_dir_of must still hold the owner/collision guard there (via @aipair-dir), and a
# legacy session — whose only dir clue on 3.1 would be session_path — must degrade to a
# safe non-adopt, never a false one. A shim reproduces 3.1 on top of whatever tmux runs
# the suite: it reports "tmux 3.1", rejects `list-sessions -f`, and blanks #{session_path}
# out of any display-message format. (@aipair-dir is untouched, as on real 3.1.)
echo "# [10] tmux 3.1 (no -f / no session_path): owner+collision guard hold, legacy safe-misses"
mkdir -p "$W/bin31"
cat > "$W/bin31/tmux" <<SH31
#!/usr/bin/env bash
[ "\$1" = -V ] && { echo "tmux 3.1"; exit 0; }
"$REAL_TMUX" -L "$SOCKET" start-server 2>/dev/null || true
"$REAL_TMUX" -L "$SOCKET" set-option -g exit-empty off 2>/dev/null || true
if [ "\$1" = list-sessions ] || [ "\$1" = ls ]; then
  for a in "\$@"; do [ "\$a" = -f ] && { echo "usage: list-sessions [-F format]" >&2; exit 1; }; done
fi
if [ "\$1" = display-message ]; then
  args=(); for a in "\$@"; do [ "\$a" = '#{session_path}' ] && a=''; args+=("\$a"); done   # 3.1 lacks #{session_path}
  exec "$REAL_TMUX" -L "$SOCKET" "\${args[@]}"
fi
exec "$REAL_TMUX" -L "$SOCKET" "\$@"
SH31
chmod +x "$W/bin31/tmux"
OLD_PATH="$PATH"; export PATH="$W/bin31:$PATH"
chk "$(tmux -V)" "tmux 3.1" "(precondition) shim reports tmux 3.1"
n=$((n+1)); if tmux list-sessions -f x >/dev/null 2>&1; then echo "FAIL shim still accepts -f"; fail=1; else echo "ok   (precondition) shim rejects list-sessions -f"; fi
n=$((n+1)); if [ -z "$(tmux display-message -p '#{session_path}' 2>/dev/null)" ]; then echo "ok   (precondition) shim blanks #{session_path}"; else echo "FAIL shim still yields session_path"; fail=1; fi
A_DIR="$(cd "$W/a/api" && pwd -P)"; B_DIR="$(cd "$W/b/api" && pwd -P)"
# owner match: a new-format session (stamped @aipair-dir) for a/api is recognized as ours
tmux new-session -d -s "$NA" -c "$W/a/api"; tmux set -t "$NA" @aipair-dir "$A_DIR"
chk "$(aipair name "$W/a/api")" "$NA" "owner match via @aipair-dir (no -f)"
# hash collision: same session name, but it belongs to b/api → refuse, do not touch it
tmux set -t "$NA" @aipair-dir "$B_DIR"
n=$((n+1)); if out="$(aipair name "$W/a/api" 2>/dev/null)"; then echo "FAIL collision not refused (got '$out')"; fail=1; else echo "ok   hash collision refused (exit!=0)"; fi
n=$((n+1)); aipair stop "$W/a/api" >/dev/null 2>&1 || true; if alive "$NA"; then echo "ok   foreign session left untouched by stop"; else echo "FAIL stop killed a foreign session"; fail=1; fi
tmux kill-session -t "=$NA" 2>/dev/null || true
# legacy: hash-less session, no @aipair-dir; with session_path absent (3.1) its dir is
# unknowable → safe miss (returns the hashed name), and the session is left alive.
tmux new-session -d -s aipair-api -c "$W/a/api"
chk "$(aipair name "$W/a/api")" "$NA" "legacy safely NOT adopted without session_path (3.1)"
if alive aipair-api; then pass "legacy session survives the non-adopt"; else flunk "legacy session vanished"; fi
tmux kill-session -t "=aipair-api" 2>/dev/null || true
export PATH="$OLD_PATH"

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED) (socket $SOCKET)"
exit $fail
