#!/usr/bin/env bash
# aipair-relay-here must refuse to fire a relay whose sibling libs are not all present
# (D3 A6): --help imports them, so a missing lib fails the load check before anything else.
#   bash tests/relay-here-libcheck.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"
command -v tmux >/dev/null 2>&1 || { echo "skip (no tmux)"; exit 0; }
W="$(mktemp -d "${TMPDIR:-/tmp}/aipair-lc.XXXXXX")"
# aipair-relay-here does a bare `tmux has-session` for session resolution, which would hit the
# user's DEFAULT server. Force every tmux call onto a PRIVATE -L socket so this test never
# touches the production server (guardrail; same isolation as the other tmux tests).
REAL_TMUX="$(command -v tmux)"; SOCKET="aipair-lc-$$-$RANDOM"
printf '#!/usr/bin/env bash\n%q -L %q start-server 2>/dev/null || true\n%q -L %q set-option -g exit-empty off 2>/dev/null || true\nexec %q -L %q "$@"\n' \
  "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" "$REAL_TMUX" "$SOCKET" > "$W/tmux"; chmod +x "$W/tmux"
trap '"$REAL_TMUX" -L "$SOCKET" kill-server 2>/dev/null || true; rm -f "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/$SOCKET" 2>/dev/null || true; rm -rf "$W"' EXIT
# refuse to run unless the shim provably targets the private socket (never the default server)
"$REAL_TMUX" -L "$SOCKET" new-session -d -s probe 2>/dev/null
want="$("$REAL_TMUX" -L "$SOCKET" display-message -p -t probe '#{socket_path}')"
got="$(PATH="$W:$PATH" tmux display-message -p -t probe '#{socket_path}')"
"$REAL_TMUX" -L "$SOCKET" kill-session -t probe 2>/dev/null || true
if [ "$got" != "$want" ] || [ "$(basename "$got")" != "$SOCKET" ]; then
  echo "tmux shim not effective (got '$got', want '$want') — refusing to touch the default server" >&2; exit 2
fi
export PATH="$W:$PATH"
fail=0; n=0
chk() { n=$((n+1)); if eval "$1"; then echo "ok   $2"; else echo "FAIL $2"; fail=1; fi; }

# The relay/peer-log are thin entrypoints that import the aipairlib package sitting next to
# them (#7). complete set → import succeeds → the load gate passes (relay-here then dies later
# on 'no session', a DIFFERENT failure, proving it got past the gate).
mkdir -p "$W/full/aipairlib"
cp "$REPO/bin/aipair-relay" "$REPO/bin/peer-log" "$W/full/"; chmod +x "$W/full/aipair-relay" "$W/full/peer-log"
cp "$REPO/bin/aipairlib/"*.py "$W/full/aipairlib/"
out="$(env -u TMUX AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --session none 2>&1)" || true
echo "$out" | grep -q "ロードできない" && loaderr=1 || loaderr=0
chk "[ $loaderr -eq 0 ]" "complete install passes the lib-load gate"

# missing one package module (tmuxlib.py) → the relay import fails → relay-here dies at the gate
mkdir -p "$W/partial/aipairlib"
cp "$REPO/bin/aipair-relay" "$REPO/bin/peer-log" "$W/partial/"; chmod +x "$W/partial/aipair-relay" "$W/partial/peer-log"
for f in "$REPO/bin/aipairlib/"*.py; do [ "$(basename "$f")" = tmuxlib.py ] || cp "$f" "$W/partial/aipairlib/"; done
rc=0; out="$(env -u TMUX AIPAIR_RELAY_BIN="$W/partial/aipair-relay" bash "$REPO/bin/aipair-relay-here" --session none 2>&1)" || rc=$?
chk "[ $rc -ne 0 ]" "missing lib → relay-here exits non-zero (got $rc)"
echo "$out" | grep -q "ロードできない" && loaderr2=1 || loaderr2=0
chk "[ $loaderr2 -eq 1 ]" "missing lib → reports the load failure, not a generic error"


# VS Code 実行経路の回帰（Codex relay-id:4047abba）: VS Code の専用ターミナルは tmux 外で $TMUX 未設定。
# その条件で --session なしの aipair-relay-here は「aipair セッションの外」で死ぬ（＝強制再点火タスクが
# --session "$(aipair name)" を渡す理由）。--session を渡せばその tmux-outside ゲートは越える。
rc=0; out="$(env -u TMUX AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --endless --max-rounds 100 --allow-untested-dialogs 2>&1)" || rc=$?
chk "[ $rc -ne 0 ]" "TMUX unset + no --session -> exits non-zero (VS Code path without --session fails)"
echo "$out" | grep -q "セッションの外" && outside=1 || outside=0
chk "[ $outside -eq 1 ]" "TMUX unset + no --session -> reports aipair セッションの外 (why --session is required)"
out2="$(env -u TMUX AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --session none --endless --max-rounds 100 --allow-untested-dialogs 2>&1)" || true
echo "$out2" | grep -q "セッションの外" && still=1 || still=0
chk "[ $still -eq 0 ]" "TMUX unset + --session -> clears the tmux-outside gate (fails later, not on outside)"
echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
