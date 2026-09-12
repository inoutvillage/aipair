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
# The shim also carries two TEST HOOKS used by the ignition tests below (both opt-in per test):
#   AIPAIR_TEST_TMUX_LOG   — append every tmux invocation (so a test can prove nothing was sent)
#   AIPAIR_TEST_CMD_ANSWERS — a file of canned `#{pane_current_command}` answers, one per line,
#                             consumed in order (then it falls through to the real tmux). Lets a test
#                             make the bridge "become busy" exactly between two checks. Consumption
#                             uses tail -n +2 (never `sed -i`, which needs an argument on macOS).
#                             The line `__FAIL__` makes that one query FAIL (exit 1, no output).
#   AIPAIR_TEST_FAIL_FORMAT — every `display-message` whose format contains this string fails
#                             (exit 1, no output): injects a tmux query failure for one format.
cat > "$W/tmux" <<'SHIM'
#!/usr/bin/env bash
REAL="$AIPAIR_TEST_REAL_TMUX"; SOCK="$AIPAIR_TEST_SOCKET"
"$REAL" -L "$SOCK" start-server 2>/dev/null || true
"$REAL" -L "$SOCK" set-option -g exit-empty off 2>/dev/null || true
[ -n "${AIPAIR_TEST_TMUX_LOG:-}" ] && printf '%s\n' "$*" >> "$AIPAIR_TEST_TMUX_LOG"
if [ -n "${AIPAIR_TEST_FAIL_FORMAT:-}" ] && [ "${1:-}" = display-message ]; then
  case " $* " in *"$AIPAIR_TEST_FAIL_FORMAT"*) exit 1 ;; esac
fi
if [ -n "${AIPAIR_TEST_CMD_ANSWERS:-}" ] && [ "${1:-}" = display-message ]; then
  case " $* " in
    *"#{pane_current_command}"*)
      ans="$(head -1 "$AIPAIR_TEST_CMD_ANSWERS" 2>/dev/null)"
      if [ -n "$ans" ]; then
        tail -n +2 "$AIPAIR_TEST_CMD_ANSWERS" > "$AIPAIR_TEST_CMD_ANSWERS.rest" 2>/dev/null \
          && mv "$AIPAIR_TEST_CMD_ANSWERS.rest" "$AIPAIR_TEST_CMD_ANSWERS"
        [ "$ans" = __FAIL__ ] && exit 1
        printf '%s\n' "$ans"; exit 0
      fi ;;
  esac
fi
exec "$REAL" -L "$SOCK" "$@"
SHIM
chmod +x "$W/tmux"
export AIPAIR_TEST_REAL_TMUX="$REAL_TMUX" AIPAIR_TEST_SOCKET="$SOCKET"
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

# --no-endless + --allow-untested-dialogs → launch 行に両フラグが入り --endless は入らない（🔄 非endless タスク用）
out="$( (cd "$W"; env -u TMUX AIPAIR_BIN="$FB/aipair" AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --print --no-endless --allow-untested-dialogs) 2>&1 )" || true
printf '%s' "$out" | grep '^launch' | grep -qF "'--no-endless'" && a=1 || a=0
printf '%s' "$out" | grep '^launch' | grep -qF "'--allow-untested-dialogs'" && b=1 || b=0
printf '%s' "$out" | grep '^launch' | grep -qF "'--endless'" && c=1 || c=0
chk "[ $a -eq 1 ] && [ $b -eq 1 ] && [ $c -eq 0 ]" "flags: --no-endless+--allow-untested-dialogs → launch に反映・--endless 無し"

# (2b) tmux外 + --dir=session の dir（別 cwd から）→ session 解決も監視 dir も --dir に揃う（cwd で選ばない）
out="$( (cd /tmp; env -u TMUX AIPAIR_BIN="$FB/aipair" AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --print --dir "$W") 2>&1 )" || true
printf '%s' "$out" | grep -q 'session : aipair-fake-sess' && g=1 || g=0
chk "[ $g -eq 1 ]" "auto+--dir: --dir が対象を決める（cwd=/tmp でも --dir=W の pair を解決）"
printf '%s' "$out" | grep '^dir' | grep -q "$(basename "$W")" && g=1 || g=0
chk "[ $g -eq 1 ]" "auto+--dir: 監視 dir も --dir(W) 側（cwd=/tmp を採用しない=session/監視の分離なし）"

# (2c) tmux外 + --dir=別ディレクトリ（session の @aipair-dir と不一致）→ 逆検証 die（poke X/watch Y を防ぐ）
rc=0; out="$( (cd "$W"; env -u TMUX AIPAIR_BIN="$FB/aipair" AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --print --dir /tmp/aipair-elsewhere) 2>&1 )" || rc=$?
printf '%s' "$out" | grep -q '不一致' && g=1 || g=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ]" "auto+--dir: --dir != session dir -> reverse-verify dies (分離を作らない)"

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
# --- 点火の確認（2026-09-12 実障害: bridge の打ちかけ入力で launch 行が壊れ、relay が立たないのに rc=0）---
# 偽 relay: --help に応答（relay-here の load gate 用）。mode=banner_sleep はバナーを出して走り続ける /
# banner_exit はバナーを出して即終了 / silent_exit はバナーを出さずに即終了。
mk_relay() {
  cat > "$1" <<EOF
#!/usr/bin/env bash
case "\$1" in --help) exit 0 ;; esac
case "$2" in banner*) printf '┌─ aipair-relay ──────────────\n' ;; esac
case "$2" in banner_sleep) exec sleep 30 ;; esac
exit 0
EOF
  chmod +x "$1"
}
# 偽ペア。bridge 検出は「自ペイン以外の最初のシェルペイン」に落ちるので pane 一覧の先頭が bridge になる。
# default-shell が fish/nu でもガードに掛からないよう bash を明示する。tmux は必ず PATH のシム（私設 socket）。
mkses() {
  tmux kill-session -t "$1" 2>/dev/null || true
  tmux new-session -d -s "$1" -c "$W" bash 2>/dev/null
  tmux set-option -t "$1" @aipair-dir "$W" 2>/dev/null
  tmux split-window -t "$1" -c "$W" bash 2>/dev/null
}
bridge_of() { tmux list-panes -t "$1" -F '#{pane_id}' | head -1; }
ignite() {   # $1=session $2=relay bin、以降は env 追加（KEY=VAL）
  local s=$1 relay=$2; shift 2
  (cd "$W"; env -u TMUX "$@" AIPAIR_RELAY_BIN="$relay" bash "$REPO/bin/aipair-relay-here" --session "$s") 2>&1
}

mk_relay "$W/relay_ok" banner_sleep
mk_relay "$W/relay_silent" silent_exit
mk_relay "$W/relay_diesfast" banner_exit

S=aipair-ig1; mkses "$S"
rc=0; out="$(ignite "$S" "$W/relay_ok")" || rc=$?
printf '%s' "$out" | grep -q '起動を確認' && g=1 || g=0
chk "[ $rc -eq 0 ] && [ $g -eq 1 ]" "ignite: banner appeared -> exit 0 + confirmation (rc=$rc)"
tmux kill-session -t "$S" 2>/dev/null || true

# 打ちかけの入力が残っていても点火できる（C-c で行を破棄する＝今回の実障害の回帰）
S=aipair-ig2; mkses "$S"; B="$(bridge_of "$S")"
tmux send-keys -t "$B" -l 'rbo-p'
rc=0; out="$(ignite "$S" "$W/relay_ok")" || rc=$?
# rc だけでは修正前コード（送るだけで 0 を返す）でも通ってしまう → 起動の確認まで要求する
printf '%s' "$out" | grep -q '起動を確認' && g=1 || g=0
chk "[ $rc -eq 0 ] && [ $g -eq 1 ]" "ignite: half-typed input in the bridge -> still ignites, start CONFIRMED (rc=$rc)"
tmux kill-session -t "$S" 2>/dev/null || true

# copy-mode のままでも点火できる（解除してから送る）
S=aipair-ig3; mkses "$S"; B="$(bridge_of "$S")"
tmux copy-mode -t "$B" 2>/dev/null
rc=0; out="$(ignite "$S" "$W/relay_ok")" || rc=$?
printf '%s' "$out" | grep -q '起動を確認' && g=1 || g=0
chk "[ $rc -eq 0 ] && [ $g -eq 1 ]" "ignite: copy-mode is cancelled -> still ignites, start CONFIRMED (rc=$rc)"
tmux kill-session -t "$S" 2>/dev/null || true

# バナーが出ない（launch が壊れた / relay が即死）→ 非ゼロ＋bridge 末尾
S=aipair-ig4; mkses "$S"
rc=0; out="$(ignite "$S" "$W/relay_silent" AIPAIR_IGNITE_TIMEOUT=2)" || rc=$?
printf '%s' "$out" | grep -q '起動を確認できません' && g=1 || g=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ]" "ignite: no banner -> non-zero with the bridge tail (rc=$rc)"
tmux kill-session -t "$S" 2>/dev/null || true

# 古いバナーが画面に残っているだけでは成功にしない（存在判定ではなく件数比較）
S=aipair-ig5; mkses "$S"; B="$(bridge_of "$S")"
tmux send-keys -t "$B" -l "printf '┌─ aipair-relay ─\\n'"; tmux send-keys -t "$B" Enter
sleep 0.5
rc=0; out="$(ignite "$S" "$W/relay_silent" AIPAIR_IGNITE_TIMEOUT=2)" || rc=$?
chk "[ $rc -ne 0 ]" "ignite: a STALE banner alone is not success (count, not presence) (rc=$rc)"
tmux kill-session -t "$S" 2>/dev/null || true

# バナーを出した直後に終了 → 「起動したが即終了」で非ゼロ
S=aipair-ig6; mkses "$S"
rc=0; out="$(ignite "$S" "$W/relay_diesfast" AIPAIR_IGNITE_TIMEOUT=4)" || rc=$?
printf '%s' "$out" | grep -q '既に終了' && g=1 || g=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ]" "ignite: banner then immediate exit -> non-zero (rc=$rc)"
tmux kill-session -t "$S" 2>/dev/null || true

# 送信直前に busy 化 → launch を送らずに中止（TOCTOU。シムが pane_current_command の答えを差し替える）
S=aipair-ig7; mkses "$S"
printf 'bash\npython3\n' > "$W/answers7"      # 掃除前=シェル → 送信直前=relay 走行中
: > "$W/tmuxlog"
rc=0; out="$(ignite "$S" "$W/relay_ok" AIPAIR_TEST_CMD_ANSWERS="$W/answers7" AIPAIR_TEST_TMUX_LOG="$W/tmuxlog")" || rc=$?
printf '%s' "$out" | grep -q '送信直前に busy' && g=1 || g=0
grep -F 'send-keys' "$W/tmuxlog" 2>/dev/null | grep -qF -- '--adopt' && sent=1 || sent=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ] && [ $sent -eq 0 ]" "ignite: busy right before sending -> aborts WITHOUT sending launch (rc=$rc sent=$sent)"
tmux kill-session -t "$S" 2>/dev/null || true

# AIPAIR_IGNITE_TIMEOUT の不正値・0 → 送る前に死ぬ（無限待ち/算術エラーにしない）
S=aipair-ig8; mkses "$S"
for bad in abc 0; do
  : > "$W/tmuxlog"
  rc=0; out="$(ignite "$S" "$W/relay_ok" AIPAIR_IGNITE_TIMEOUT="$bad" AIPAIR_TEST_TMUX_LOG="$W/tmuxlog")" || rc=$?
  printf '%s' "$out" | grep -q 'AIPAIR_IGNITE_TIMEOUT' && g=1 || g=0
  grep -F 'send-keys' "$W/tmuxlog" 2>/dev/null | grep -qF -- '--adopt' && sent=1 || sent=0
  chk "[ $rc -ne 0 ] && [ $g -eq 1 ] && [ $sent -eq 0 ]" "ignite: AIPAIR_IGNITE_TIMEOUT=$bad -> dies before sending (rc=$rc)"
done
tmux kill-session -t "$S" 2>/dev/null || true

# ログインシェル（-bash）の bridge を busy と誤判定しない（シェル判定の共通化）
S=aipair-ig9; mkses "$S"
printf -- '-bash\n-bash\n' > "$W/answers9"
rc=0; out="$(ignite "$S" "$W/relay_ok" AIPAIR_TEST_CMD_ANSWERS="$W/answers9")" || rc=$?
chk "[ $rc -eq 0 ]" "ignite: a login shell (-bash) bridge counts as idle, not busy (rc=$rc)"
tmux kill-session -t "$S" 2>/dev/null || true

# copy-mode 状態の取得失敗を「copy-mode でない」と扱わない（実は copy-mode なのに送ると無言の空振り）
S=aipair-ig10; mkses "$S"
: > "$W/tmuxlog"
rc=0; out="$(ignite "$S" "$W/relay_ok" AIPAIR_TEST_FAIL_FORMAT='#{pane_in_mode}' AIPAIR_TEST_TMUX_LOG="$W/tmuxlog")" || rc=$?
printf '%s' "$out" | grep -q 'copy-mode 状態を取得できません' && g=1 || g=0
grep -F 'send-keys' "$W/tmuxlog" 2>/dev/null | grep -qF -- '--adopt' && sent=1 || sent=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ] && [ $sent -eq 0 ]" "ignite: pane_in_mode query FAILS -> abort without sending (rc=$rc sent=$sent)"
tmux kill-session -t "$S" 2>/dev/null || true

# 前景コマンドの取得失敗も「busy か判断できない」として送らずに中止
S=aipair-ig11; mkses "$S"
: > "$W/tmuxlog"
rc=0; out="$(ignite "$S" "$W/relay_ok" AIPAIR_TEST_FAIL_FORMAT='#{pane_current_command}' AIPAIR_TEST_TMUX_LOG="$W/tmuxlog")" || rc=$?
printf '%s' "$out" | grep -q '前景コマンドを取得できません' && g=1 || g=0
grep -F 'send-keys' "$W/tmuxlog" 2>/dev/null | grep -qF -- '--adopt' && sent=1 || sent=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ] && [ $sent -eq 0 ]" "ignite: pane_current_command query FAILS -> abort without sending (rc=$rc sent=$sent)"
tmux kill-session -t "$S" 2>/dev/null || true

# 起動後の取得失敗を「既に終了」と誤報しない（原因の取り違えで調査が的外れになる）
S=aipair-ig12; mkses "$S"
printf 'bash\nbash\n__FAIL__\n' > "$W/answers12"   # 掃除前・送信直前は成功 → 起動後の取得だけ失敗
rc=0; out="$(ignite "$S" "$W/relay_ok" AIPAIR_TEST_CMD_ANSWERS="$W/answers12")" || rc=$?
printf '%s' "$out" | grep -q '起動状態を確認できない' && g=1 || g=0
printf '%s' "$out" | grep -q '既に終了' && bad=1 || bad=0
chk "[ $rc -ne 0 ] && [ $g -eq 1 ] && [ $bad -eq 0 ]" "ignite: post-start query failure is diagnosed as such, not '既に終了' (rc=$rc)"
tmux kill-session -t "$S" 2>/dev/null || true

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
