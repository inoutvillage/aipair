"""aipair-relay — autonomous review loop between Claude Code and Codex.

Watches both agents' session logs for turn completion, and relays a short
"poke" into the other agent's tmux pane so it reacts to what the peer just said:

    (you type the first task into Claude)
    Claude implements ──end_turn──▶ poke Codex: "read with `peer`, review"
    Codex reviews   ──task_complete──▶ poke Claude: "read with `peer`, fix"
    … repeat until Codex's review leads with the stop sentinel (default [AIPAIR_REVIEW_OK]),
      or the max-round safety cap is hit.

Plan mode (auto plan review):
  If Claude stops at the plan-approval dialog ("Would you like to proceed?"),
  the relay asks Codex to review the plan file (~/.claude/plans/*.md, path
  taken from the dialog itself). Codex's verdict is injected back:
    changes requested            → "Tell Claude what to change" + review + Enter
    approved (冒頭に「プラン承認」) → the first "Yes…" option (2.1.247: "Yes, and use auto
                                   mode"; an older "Yes, and bypass permissions" wins if shown)
    approved with extra notes    → feedback + shift+tab (approve w/ feedback)
  Capped at --plan-rounds per plan (default 5); disable with --no-plan-review.

Module layout (#7 + P2-1: relay is now just the LAUNCHER in the `aipairlib` package; helpers and
the state machine are sibling package modules imported normally — `from . import corelib, …` — no
SourceFileLoader and no runtime attribute injection):
  peerlog       transcript location + parsing (also `peer-log` the CLI's implementation)
  logs          shared colour/logging (dim/c/log); colour set once via configure()
  corelib       pure helpers: stop-phrase, version gate, JSONL schema probe, gate output shaping
  loglib        turn-completion / response-attribution reading + bounded record reader
  tmuxlib       the tmux(...) runner + pane discovery/inspection
  deliverylib   poke delivery + Enter submission (imports tmuxlib/logs; poke takes busy_wait=)
  dialoglib     plan/question dialog detection + response (+ dialog constants)
  cli           argparse (build_parser) + AIPAIR_* env defaults
  review_protocol  the poke wording (7 templates) — pure
  gate          the stop-gate runner (subprocess) + gate_or_message
  log_lock      per-pane transcript locking / refresh
  schema_guard  SchemaGuard: fail-closed runtime JSONL-schema watch
  state_machine the state machine: StateMachine.run() (the loop) + LogWatch + approval_took_effect +
                done_banner, plus the pure decision cores ResponseGate / decide_plan_action /
                decide_question_action
The delivery<->dialog cycle is a plain module import used at call-time. What stays in relay: only
main() — arg parse, dependency construction, startup banner, gates, then StateMachine(...).run() —
and unqualified re-exports of the sibling helpers so tests keep calling them as relay.X. The thin
`bin/aipair-relay` entrypoint runs `aipairlib.relay.main()`.

Question dialogs (auto AskUserQuestion relay):
  If Claude stops at a multiple-choice question dialog (AskUserQuestion — the
  literal-input poke would be eaten by the selector UI), the relay scrapes ALL
  questions of the call by walking the dialog's tabs with Right-arrow (the
  pending tool_use is NOT in the session jsonl until answered — 2026-07-23
  実測 — so the screen is the only complete source), asks Codex to answer them
  in ONE round-trip, and delivers the reply via "Chat about this": pressing it
  resolves the dialog immediately as "declined" and returns to the composer,
  where the answers are pasted+submitted as the follow-up user message
  (実測: Claude reads the answers and proceeds; it may emit one short turn
  reacting to the decline before the queued answers arrive — harmless).
  Capped at --question-rounds consecutive relays without a completed Claude
  turn; disable with --no-question-relay.

Endless mode (--endless, opt-in; 既定は従来どおり停止ワードで終了):
  停止 sentinel（既定 [AIPAIR_REVIEW_OK]）を「このタスクのレビュー合格」の合図として扱い、
  ループを止めずに Claude へ「次のタスクへ進め」と伝える。Claude 側の手持ちが
  尽きたら Claude が --next-ask（既定 [AIPAIR_NEXT]）を先頭行に単独で書き、
  relay は Codex に「タスクリストから次の1件を指示せよ」と依頼する。
  終端は relay の task-list 分類が権威（READY/BLOCKED/ALL_DONE）。Codex の終端 sentinel
  --all-done（[AIPAIR_ALL_DONE]）/ --human-required（[AIPAIR_HUMAN_REQUIRED]）は分類が一致した
  時だけ honor する:
    ・ALL_DONE（[ ] も [!] も無い）＋ [AIPAIR_ALL_DONE]            → 全完了・exit 0
    ・BLOCKED（[ ] が無く人間対応の [!] のみ）＋ [AIPAIR_HUMAN_REQUIRED] → 人間対応待ち・exit 8
    ・READY（着手可 [ ] が残る）→ sentinel を無視して継続（誤 sentinel で止めない）

      Claude 実装 ──▶ Codex レビュー
        ├ 指摘あり      → Claude が修正（従来どおり）
        └[AIPAIR_REVIEW_OK] → Claude「次のタスクへ」
                            └ 手持ちなし → [AIPAIR_NEXT]
                                 └─▶ Codex「次はこれ」
                                      / 分類 ALL_DONE + [AIPAIR_ALL_DONE]       → exit 0
                                      / 分類 BLOCKED  + [AIPAIR_HUMAN_REQUIRED]  → exit 8（人間対応待ち）

  次タスクの根拠は --task-list（既定 tasks/todo.md）の未チェック項目に限定し、
  リスト外の新規提案を禁じる文面を Codex へ送る（スコープ膨張の防止）。
  task-list の記法: [ ]=着手可 / [x]=完了 / [!]=人間対応・外部依存の保留（直下に blocker: 理由）。
  認識 checkbox が 0 件（README 等の誤指定・空ファイル・見出し/散文のみ）は設定不備として exit 2（ALL_DONE にしない）。
  終端は上記2つ（ALL_DONE=exit 0 / HUMAN_REQUIRED=exit 8）。--max-rounds は安全キャップとして残る。
  --gate CMD（env AIPAIR_GATE）: 停止ワード検知後に CMD を --dir で実行し、exit 0 の時だけ停止／次タスクへ。
  失敗は出力の末尾を添えて Claude に差し戻す（--gate-rounds 回（既定 3）で exit 6）。未指定なら従来どおり。

Turn detection (verified against real logs):
  • Claude: latest `assistant` entry whose stop_reason != "tool_use"  → turn done
  • Codex : latest task_* event is `task_complete`                    → turn done

Injection uses a proven tmux incantation:
  send-keys -l "<text>" → 配達確認 → 画面静止待ち → send-keys Enter → busy確認
(one-shot "text+Enter" gets interpreted as a newline by the agent TUIs. Long
texts additionally need the stability wait: an Enter arriving while the TUI is
still ingesting the input burst is absorbed as a newline instead of submitting
— 2026-08-14 実バグ、質問リレーの長文 poke で Codex が未送信のまま停滞。
busy 確認は copy-mode 等に食われた Enter の再打鍵つき自己回復).

Run it inside the aipair tmux session (the bridge pane); it auto-detects the
session, the two panes (by title), and each agent's freshly-spawned log file.
"""
import argparse
import glob
import math
import os
import re
import subprocess
import sys
import time
import unicodedata

# --- sibling package modules (#7: normal imports; was SourceFileLoader-by-path + per-module
# attribute injection). Re-bound below so relay code and tests keep calling them unqualified /
# as relay.X. The delivery<->dialog cycle, the shared logger, and the idle budget are now real
# imports / a poke(busy_wait=...) argument, handled inside those modules — no injection here.
from . import peerlog, corelib, loglib, tmuxlib, deliverylib, dialoglib, logs, review_protocol, gate, log_lock, cli, state_machine
from .schema_guard import SchemaGuard
from .state_machine import (ResponseGate, StateMachine, decide_plan_action, decide_question_action)
from .gate import run_gate, gate_or_message
from .cli import build_parser
from .log_lock import (claude_glob, codex_all, codex_cwd_matches, claude_matches_pane, lock_claude,
                       read_codex_since, codex_fallback, lock_codex, refresh_codex_lock,
                       refresh_claude_lock)
from .review_protocol import (DEFAULT_POKE_CLAUDE, default_poke_codex, plan_poke_codex,
                              question_poke_codex, endless_poke_claude_pass, endless_poke_codex_next,
                              endless_poke_claude_next, plan_extra_comment)
from .logs import c, log, dim, configure

# corelib (pure helpers)
TESTED_VERSIONS = corelib.TESTED_VERSIONS
GATE_OUTPUT_CAP = corelib.GATE_OUTPUT_CAP
parse_version = corelib.parse_version
detect_version = corelib.detect_version
version_gate = corelib.version_gate
schema_probe = corelib.schema_probe
schema_gate = corelib.schema_gate
schema_fail_closed = corelib.schema_fail_closed
schema_latch_step = corelib.schema_latch_step
schema_should_reprobe = corelib.schema_should_reprobe
hit_stop = corelib.hit_stop
oneline = corelib.oneline
_env_str = cli._env_str
_env_int = cli._env_int
_env_bool = cli._env_bool
ENV_USED = cli.ENV_USED
head_line = corelib.head_line
scrub_output = corelib.scrub_output
gate_tail = corelib.gate_tail
gate_message = corelib.gate_message
_oneline_cap = corelib._oneline_cap
# loglib (transcript reading)
claude_done_ts = loglib.claude_done_ts
codex_done_ts = loglib.codex_done_ts
turn_texts = loglib.turn_texts
find_poke_ts = loglib.find_poke_ts
codex_response_complete = loglib.codex_response_complete
latest_compact_boundary = loglib.latest_compact_boundary
records_since_compaction = loglib.records_since_compaction
claude_response_attributed = loglib.claude_response_attributed
make_fragment = loglib.make_fragment
read_records = loglib.read_records


def probe_log_schema(agent, path):
    """(status, reason) for the agent's tracked transcript via corelib.schema_probe, or
    ('unverified', 'ログ未特定') when no log is pinned yet. Bounded read (loglib.read_records)."""
    if not path:
        return ("unverified", "ログ未特定")
    records = read_records(path)
    if agent == "claude":
        # compaction 後の新世代は《最新 compact_boundary 以降》のみを検査する。さもないと境界前の
        # malformed レコードを reset 後も拾い、即 terminal mismatch に戻る（P1-4/Codex）。
        records = records_since_compaction(records)
    return schema_probe(agent, records)


# tmuxlib (tmux runner + pane helpers)
tmux = tmuxlib.tmux
current_session = tmuxlib.current_session
find_panes = tmuxlib.find_panes
own_pane = tmuxlib.own_pane
set_pane_title = tmuxlib.set_pane_title
cancel_copy_mode = tmuxlib.cancel_copy_mode
pane_busy = tmuxlib.pane_busy
capture_pane = tmuxlib.capture_pane
# deliverylib (poke delivery + Enter submission)
press = deliverylib.press
paste_text = deliverylib.paste_text
submit_enter = deliverylib.submit_enter
poke = deliverylib.poke
# dialoglib (plan/question dialog detection + response)
newest_plan = dialoglib.newest_plan
detect_plan_dialog = dialoglib.detect_plan_dialog
detect_question_dialog = dialoglib.detect_question_dialog
scrape_questions = dialoglib.scrape_questions
send_plan_feedback = dialoglib.send_plan_feedback
send_question_answer = dialoglib.send_question_answer
PLAN_QUESTION = dialoglib.PLAN_QUESTION      # used by claude_matches_pane (still in relay)
QUESTION_FOOTER = dialoglib.QUESTION_FOOTER

# --- AIPAIR_* 環境変数を既定値として読む ------------------------------------ #
# 優先順位: CLI フラグ > 環境変数 > 組み込み既定。relay ペインのプロンプトから
# 直接起動する時（`aipair-relay --adopt …` / `aipair-relay-here`）でも
# `aipair loop` と同じ env で設定できるようにするため（2026-08-16）。
# tmux new-session は起動シェルの env を引き継ぐので、`AIPAIR_MAX_ROUNDS=100 aipair loop`
# しておけば bridge ペインにも残り、後から張り直す relay にも効く。




def main():
    ap = build_parser(__doc__)
    a = ap.parse_args()
    a.schema_mismatch = False   # set by schema_gate / schema_watch when core JSONL schema drifts
    a.schema_stop = False       # fail-closed: a runtime schema mismatch (no override) stops the loop (exit 7)

    # argparse の choices は「コマンドラインで渡された値」しか検証しない。
    # env 由来の既定値は素通りするので、ここで明示的に弾く（無言で codex 扱いにしない）。
    if a.stop_side not in ("codex", "claude", "both"):
        print(f"aipair-relay: --stop-side / AIPAIR_STOP_SIDE は codex|claude|both のいずれか"
              f"（実際の値: {a.stop_side!r}）", file=sys.stderr)
        return 2
    # argparse type=int accepts 0 / negatives on the command line; _env_int already
    # guards the env path, so validate the resolved values here for both.
    for name, val in (("--gate-timeout", a.gate_timeout), ("--gate-rounds", a.gate_rounds),
                      ("--max-rounds", a.max_rounds), ("--plan-rounds", a.plan_rounds),
                      ("--question-rounds", a.question_rounds)):
        if val < 1:
            print(f"aipair-relay: {name} は 1 以上で指定してください（実際の値: {val!r}）", file=sys.stderr)
            return 2
    if a.no_endless:
        a.endless = False

    configure((not a.no_color) and sys.stdout.isatty())
    bw = max(60, a.busy_wait)   # idle budget passed explicitly to poke()
    cwd = os.path.realpath(os.path.expanduser(a.dir))
    stop_phrases = [s for s in a.stop.split("||") if s]

    poke_codex = a.poke_codex or default_poke_codex(stop_phrases[0] if stop_phrases else "[AIPAIR_REVIEW_OK]")
    poke_claude = a.poke_claude
    next_ask_phrases = [s for s in a.next_ask.split("||") if s]
    all_done_phrases = [s for s in a.all_done.split("||") if s]
    human_required_phrases = [s for s in a.human_required.split("||") if s]
    poke_claude_pass = endless_poke_claude_pass(a.task_list, next_ask_phrases[0] if next_ask_phrases
                                                else "[AIPAIR_NEXT]")
    poke_codex_next = endless_poke_codex_next(a.task_list,
                                              all_done_phrases[0] if all_done_phrases else "[AIPAIR_ALL_DONE]",
                                              human_required_phrases[0] if human_required_phrases
                                              else "[AIPAIR_HUMAN_REQUIRED]")
    poke_claude_next = endless_poke_claude_next(a.task_list,
                                                next_ask_phrases[0] if next_ask_phrases else "[AIPAIR_NEXT]")
    # endless の 2 終端（ALL_DONE / HUMAN_REQUIRED）はどちらも sentinel が必須。空だとプロンプトは既定
    # sentinel を出すのに検出リストが空になり、その終端へ遷移しても認識できず max-rounds まで続く
    # （prompt と検出の食い違い）→ fail-closed で拒否する。
    if a.endless and not all_done_phrases:
        print(c("warn", "aipair-relay: --endless の終端 sentinel --all-done を空にできません"
                        "（分類 ALL_DONE 時の正常終了に必要）"), file=sys.stderr)
        return 2
    if a.endless and not human_required_phrases:
        print(c("warn", "aipair-relay: --endless の終端 sentinel --human-required を空にできません"
                        "（分類 BLOCKED 時の HUMAN_REQUIRED 停止に必要。空だと Codex への文面は既定 "
                        "sentinel を出すのに検出できず max-rounds まで続く）"), file=sys.stderr)
        return 2
    if a.endless and not next_ask_phrases:
        print(c("warn", "aipair-relay: --endless の合図 sentinel --next-ask を空にできません"
                        "（Claude の手詰まり合図＝次タスク選択への遷移に必要。空だと Claude への文面は既定 "
                        "sentinel を出すのに検出できず遷移できない）"), file=sys.stderr)
        return 2

    session = a.session or current_session()
    if not session:
        print(c("warn", "aipair-relay: not inside tmux and --session not given"), file=sys.stderr)
        return 2
    panes = find_panes(session)
    read_codex_since(session)   # for the non-/proc fallback: share peer's codex_since picker
    if "claude" not in panes or "codex" not in panes:
        print(c("warn", f"aipair-relay: could not find claude+codex panes in '{session}' "
                        f"(found: {panes}). Start with `aipair loop`."), file=sys.stderr)
        return 2

    # Version gate: an untested claude/codex keeps the safe relay (poke + transcripts) but
    # loses the TUI-scraping dialog automation, which its version might have changed.
    vrows, vbad = ([], [])
    if not a.no_version_gate:
        vrows, vbad = version_gate(a, {n: detect_version(n) for n in ("claude", "codex")})

    # Schema feature-probe: the version gate only knows --version strings, so also probe the
    # actual JSONL the core relay reads. At startup only EXPLICIT pins exist (an `aipair loop`
    # has no log yet → "unverified"); sg.watch() re-probes the tracked logs at runtime.
    srows, sbad = ([], [])
    if not a.no_schema_probe:
        spin = {"claude": os.path.realpath(os.path.expanduser(a.claude_log)) if a.claude_log else None,
                "codex":  os.path.realpath(os.path.expanduser(a.codex_log)) if a.codex_log else None}
        srows, sbad = schema_gate(a, {n: probe_log_schema(n, spin[n]) for n in ("claude", "codex")})

    # ペインタイトルで「今どのモードで・何往復まで」が一目で分かるようにする
    own = own_pane(session)
    set_pane_title(own, (f"relay ● endless / max {a.max_rounds} / 終端 DONE/HUMAN / Ctrl-C で停止"
                         if a.endless else
                         f"relay ● 1タスク / max {a.max_rounds} / 停止「{a.stop}」/ Ctrl-C で停止"))

    print(c("relay", "┌─ aipair-relay ───────────────────────────────────────────────"))
    log(f"session={session}  dir={cwd}")
    if ENV_USED:
        # env を読んだ事実を必ず見せる（「指定したのに効いていない/効きすぎている」を防ぐ）
        log(c("dim", "env 由来の既定値: " + "  ".join(ENV_USED)
              + ("  ※ CLI フラグが渡された項目はそちらが優先" if len(sys.argv) > 1 else "")))
    log(f"停止={'/'.join(stop_phrases)}（{a.stop_side}側）  最大={a.max_rounds}往復  panes={panes['claude']}/{panes['codex']}")
    if a.gate:
        log(f"停止ゲート={a.gate}（timeout {a.gate_timeout}s / 差し戻し上限 {a.gate_rounds} 回）")
    for name, det, tst, status in vrows:
        if status == "ok":
            log(c("dim", f"{name} 版 {det}（検証済み）"))
        elif status == "mismatch":
            log(c("warn", f"⚠ {name} 版 {det} は検証済み {tst} と異なる"))
        else:
            log(c("warn", f"⚠ {name} 版を取得できず（検証済み {tst}）"))
    if vbad:
        log((c("dim", "  → --allow-untested-dialogs によりダイアログ自動操作は継続")
             if a.allow_untested_dialogs else
             c("warn", "  → プラン承認・質問リレーの自動操作を OFF にしました"
                       "（poke/transcript は続行。--allow-untested-dialogs で無効化）")))
    for name, status, reason in srows:
        if status == "ok":
            log(c("dim", f"{name} ログschema OK（{reason}）"))
        elif status == "mismatch":
            log(c("warn", f"⚠ {name} ログschema がコア relay の依存と不一致（{reason}）"))
        # "unverified" at startup is normal (no pinned log yet) → sg.watch() checks at runtime
    if not a.no_schema_probe and not any(st != "unverified" for _n, st, _r in srows):
        log(c("dim", "ログschema=実行時に検査（起動時はピン待ち）"))
    if sbad:
        if schema_fail_closed(a, sbad):
            print(c("warn", "│ ■ ログschema がコア relay の依存と不一致 → fail-closed で停止（exit 7）。"
                            "ターン検出・応答帰属が誤動作し得るため、権限バイパス下の自律運転は中止します。"
                            "継続するなら --allow-untested-schema（AIPAIR_ALLOW_UNTESTED_SCHEMA=1）。"), flush=True)
            print("\a", end="", flush=True)
            set_pane_title(own, "relay ■ 終了(schema不一致) / 0往復")   # 走行中タイトルのまま残さない
            return 7
        log(c("dim", "  → --allow-untested-schema: fail-open で継続（ダイアログ自動操作は OFF）"))
    if a.endless:
        log(c("ok", "連続モード=on") + f"（「{stop_phrases[0] if stop_phrases else '[AIPAIR_REVIEW_OK]'}」＝レビュー合格→次のタスクへ）")
        log(f"  タスクリスト={a.task_list}  次を要求={'/'.join(next_ask_phrases)}")
        log(f"  終端（task-list 分類で判定・codex側）= ALL_DONE:{'/'.join(all_done_phrases)}"
            f" / HUMAN_REQUIRED:{'/'.join(human_required_phrases)}")
        if a.stop_side != "codex":
            log(c("warn", f"  ⚠ --stop-side {a.stop_side} は連続モードでは終了になりません"
                          "（連続モードの終端 sentinel ALL_DONE/HUMAN_REQUIRED は Codex 側）"))
        if a.max_rounds == 20:
            log(c("dim", "  ヒント: 連続モードは往復が伸びます。--max-rounds を大きめに（例 100）"))
    log("プランレビュー=" + ("off" if a.no_plan_review
        else f"on（上限{a.plan_rounds}回・承認ワード「{a.plan_ok}」）"))
    log("質問リレー=" + ("off" if a.no_question_relay
        else f"on（連続上限{a.question_rounds}回・Chat about this 経由で回答）"))
    log(c("ok", f"▶ {a.start_side} ペインに最初の依頼を入力してください。完了を検知したら自動でリレーします。"))
    log(c("dim", "  （停止: このペインで Ctrl-C ／ `aipair stop`）"))
    print(c("relay", "└──────────────────────────────────────────────────────────────"))

    baseline = time.time()
    claude_seen = set(glob.glob(claude_glob(cwd)))
    codex_seen = set(codex_all())
    tracked = {"claude": None, "codex": None}
    if a.claude_log:
        tracked["claude"] = os.path.realpath(os.path.expanduser(a.claude_log))
    if a.codex_log:
        tracked["codex"] = os.path.realpath(os.path.expanduser(a.codex_log))
    if a.adopt:
        # 既存セッションの自動採用（明示ピンがある側はそちらを優先）
        if not tracked["claude"]:
            existing = sorted(glob.glob(claude_glob(cwd)), key=os.path.getmtime, reverse=True)
            match = next((f for f in existing[:10]
                          if claude_matches_pane(f, panes["claude"])), None)
            if match:
                tracked["claude"] = match
                dim(f"adopt: claude = {os.path.basename(match)}（ペイン内容と照合一致）")
            elif existing:
                tracked["claude"] = existing[0]
                dim(f"adopt: claude = {os.path.basename(existing[0])}"
                    "（ペイン照合できず mtime 最新にフォールバック — 誤ピンの可能性あり）")
            else:
                log(c("warn", "adopt: このプロジェクトの Claude ログが見つからず → 新規セッションの出現待ちに切替"))
        if not tracked["codex"]:
            # ONE source of truth with peer-log: the rollout the pair's Codex process actually
            # holds open (codex_via_pane), not merely the newest for the cwd — so `peer` and the
            # relay never diverge onto two different same-cwd Codex sessions (2026-08-22 review).
            ident = peerlog.codex_via_pane(cwd, panes["codex"])
            if ident:
                f = ident
            elif peerlog.codex_identity_capable(panes["codex"]):
                f = None                       # capable but not resolved yet → wait, don't mis-pin
            else:
                f = codex_fallback(cwd, None)
            if f:
                tracked["codex"] = f
                dim(f"adopt: codex = {os.path.basename(f)}")
            if not tracked["codex"]:
                log(c("warn", "adopt: cwd一致の Codex rollout が見つからず → 新規セッションの出現待ちに切替"))
    # ランタイム JSONL schema 監視は SchemaGuard（bin/aipairlib/schema_guard.py）へ切り出した
    # （P2-1）。純関数＋probe を注入で受け取り、latch/identity を所有する。単体テスト可能。
    def _warn(msg, bell=False):
        print(c("warn", msg), flush=True)
        if bell:
            print("\a", end="", flush=True)
    sg = SchemaGuard(a, tracked, probe_log_schema, latest_compact_boundary, dim, _warn)
    # poke 応答帰属ゲート（response_done / poke no-show）も同型で切り出し（P2-1・state_machine.py）。
    rg = ResponseGate(tracked, find_poke_ts, codex_response_complete,
                      claude_response_attributed, dim, _warn)

    return StateMachine(
        a, panes=panes, own=own, cwd=cwd, tracked=tracked,
        claude_seen=claude_seen, codex_seen=codex_seen, baseline=baseline,
        sg=sg, rg=rg, bw=bw,
        poke_codex=poke_codex, poke_codex_next=poke_codex_next, poke_claude=poke_claude,
        poke_claude_pass=poke_claude_pass, poke_claude_next=poke_claude_next,
        stop_phrases=stop_phrases, next_ask_phrases=next_ask_phrases,
        all_done_phrases=all_done_phrases, human_required_phrases=human_required_phrases,
    ).run()


if __name__ == "__main__":
    sys.exit(main())
