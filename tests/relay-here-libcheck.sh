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


# tmux外 自動解決 + @aipair-dir 逆検証（CEO 指示 2026-08-25）: VS Code の専用ターミナルは tmux 外
# （$TMUX 未設定）。aipair-relay-here は session 名生成を自前で持たず、同梱 aipair name <cwd> へ委譲し、
# 解決した session の @aipair-dir と canonical(cwd) を逆検証してから点火する。AIPAIR_BIN を差し替えて固定。
FB="$W/fakebin"; mkdir -p "$FB"
printf '#!/usr/bin/env bash\n[ "$1" = name ] && { echo aipair-fake-sess; exit 0; }\nexit 1\n' > "$FB/aipair"; chmod +x "$FB/aipair"

# (1) AIPAIR_BIN が実行不能 → 「解決に必要な aipair が見つからない」で die（PATH 非依存＝$0 隣接を使う設計）
rc=0; out="$( (cd "$W"; env -u TMUX AIPAIR_BIN="$W/nope/aipair" AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --print) 2>&1 )" || rc=$?
printf '%s' "$out" | grep -q '解決に必要な aipair' && g=1 || g=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ]" "auto: AIPAIR_BIN unusable -> dies with '解決に必要な aipair'"

# (2) 委譲した session が存在しない → 'セッションが無い'（旧 'セッションの外' では死なない）
rc=0; out="$( (cd "$W"; env -u TMUX AIPAIR_BIN="$FB/aipair" AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --print) 2>&1 )" || rc=$?
printf '%s' "$out" | grep -q 'セッションが無い' && g=1 || g=0
chk "[ $g -eq 1 ]" "auto: delegates to aipair name and checks existence (セッションが無い)"
printf '%s' "$out" | grep -q 'セッションの外' && g=1 || g=0
chk "[ $g -eq 0 ]" "auto: no longer dies on 'セッションの外' (it delegates)"

# session を作り @aipair-dir を正しく設定 → 逆検証パス（--print が session を解決する）
tmux new-session -d -s aipair-fake-sess -c "$W" 2>/dev/null
tmux set-option -t aipair-fake-sess @aipair-dir "$W" 2>/dev/null
tmux split-window -t aipair-fake-sess -c "$W" 2>/dev/null
out="$( (cd "$W"; env -u TMUX AIPAIR_BIN="$FB/aipair" AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --print) 2>&1 )" || true
printf '%s' "$out" | grep -q 'session : aipair-fake-sess' && g=1 || g=0
chk "[ $g -eq 1 ]" "auto: @aipair-dir==canonical(cwd) -> resolves (reverse-verify passes)"

# (3) @aipair-dir を別 dir に → 逆検証で不一致 die（identity 破壊防止・hash 衝突対策）
tmux set-option -t aipair-fake-sess @aipair-dir "/tmp/aipair-mismatch-xyz" 2>/dev/null
rc=0; out="$( (cd "$W"; env -u TMUX AIPAIR_BIN="$FB/aipair" AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --print) 2>&1 )" || rc=$?
printf '%s' "$out" | grep -q '不一致' && g=1 || g=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ]" "auto: @aipair-dir != cwd -> reverse-verify dies (不一致)"

# (4) @aipair-dir 無し（旧形式）→ fail-closed
tmux set-option -u -t aipair-fake-sess @aipair-dir 2>/dev/null || true
rc=0; out="$( (cd "$W"; env -u TMUX AIPAIR_BIN="$FB/aipair" AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --print) 2>&1 )" || rc=$?
printf '%s' "$out" | grep -q '@aipair-dir が無い' && g=1 || g=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ]" "auto: legacy session (no @aipair-dir) -> fail-closed"
tmux kill-session -t aipair-fake-sess 2>/dev/null || true
echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
