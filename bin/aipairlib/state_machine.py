"""aipair state machine — relay メインループの状態要素（D3 relay 分割 / P2-1）。

P2-1 は relay の巨大な状態機械を《state 単位》へ分割し、relay.py を arg 解析・依存構築・
起動・exit に寄せる。その最初の住人がこの `ResponseGate`：poke（依頼配達）の nonce ライフ
サイクルと「完了検知 → その poke への応答か？」の帰属ゲート（P1-2/P1-3）を所有する。

従来これは main() 内の nested closure（`response_done` / `poke_noshow`＋`probe*`/`last_rejected`
の nonlocal）で、tmux/ループ状態に埋もれ**単体テスト不能**だった。SchemaGuard（schema_guard.py）
と同じ設計でクラス化する：状態を《このオブジェクトが所有》し、実ログを読む純関数
（find_poke_ts / codex_response_complete / claude_response_attributed）と出力フック（dim/warn）を
注入で受け取り、tmux/画面には一切依存しない。これで tests/relay-parsers.py が
「キュー投入で先行ターンを誤帰属しない」「turn_id 欠落で fail-closed（自律判定に使わない）」
「nonce 未着は no-show 期限で停止」を直接被覆できる。

設計方針は spec と同じく **「判定できない場合は止まる（fail-closed）」**：帰属を turn_id / 応答
チェーンで確定できない完了は《採用せず reject（待機）》し、位置フォールバックには絶対に頼らない。

入力（コンストラクタ）:
  tracked                  {"claude": path|None, "codex": path|None}（relay と共有する可変 dict）
  find_poke_ts(agent, path, nonce) -> ts|None            nonce（user アイテム）の出現 ts
  codex_response_complete(path, nonce) -> (anchor, comp)  turn_id ペアリング（fail-closed）
  claude_response_attributed(path, nonce) -> bool         parentUuid チェーン帰属
  dim(msg) / warn(msg, bell=False)                        出力フック（relay 側の配色・ベル）
  clock                    時刻ソース（既定 time.time、テストは fake clock を注入）

出力: response_done() は「採用してよい完了 ts」または None（未確定→待機）。noshow() は
「未配達を確定して停止すべきか」を bool で返す（True の直前に warn 済み）。
"""
import os
import subprocess
import time

from . import peerlog
from .logs import c, log, dim
from .tmuxlib import tmux, set_pane_title
from .loglib import claude_done_ts, codex_done_ts, turn_texts
from .log_lock import (lock_claude, lock_codex, refresh_claude_lock, refresh_codex_lock,
                       claude_matches_pane)
from .dialoglib import (detect_plan_dialog, detect_question_dialog, scrape_questions,
                        send_plan_feedback, send_question_answer, PLAN_QUESTION)
from .deliverylib import press, poke
from .gate import gate_or_message
from .corelib import hit_stop, oneline
from .review_protocol import plan_poke_codex, question_poke_codex
from .plan_flow import decide_plan_action
from .question_flow import decide_question_action

# endless BLOCKED/HUMAN_REQUIRED（社長指示 2026-08-24 / _reference/new-task.md）: max-rounds(3) と
# 区別する固有 exit code。同じ 8 でも 2 つの内部理由を reason 文字列で区別する（Phase 2/4 が設定）:
#   HUMAN_REQUIRED   = 実行可能な [ ] が尽き、人間対応の [!] のみ残存（task-list 分類==BLOCKED）
#   BLOCKED (no-progress) = 同一タスクが snapshot 不変のまま 3 回連続再選択（relay 内部検出）
EXIT_BLOCKED = 8
BLOCKED_HR_REASON = "人間対応待ち（HUMAN_REQUIRED）"
BLOCKED_NOPROGRESS_REASON = "進捗なし（BLOCKED / no-progress）"


class ResponseGate:
    POKE_NOSHOW = 1800   # nonce がログに現れないまま諦めるまでの秒数（未配達＝停止）

    def __init__(self, tracked, find_poke_ts, codex_response_complete,
                 claude_response_attributed, dim, warn, clock=time.time):
        self.tracked = tracked
        self._find_poke_ts = find_poke_ts
        self._codex_response_complete = codex_response_complete
        self._claude_response_attributed = claude_response_attributed
        self._dim = dim
        self._warn = warn                 # warn(msg, bell=False)
        self._clock = clock
        # poke ライフサイクル状態（従来 main の nonlocal）
        self.probe = None            # 未消化 poke の nonce（ログでの配達確認・応答帰属に使用）
        self.probe_ts_cache = None   # 応答判定アンカー ts（nonce 出現 ts → codex は新タスク開始 ts へ前進）
        self.probe_sent_at = 0.0     # poke 送出時刻（no-show 監視用）
        self.last_rejected = None    # 帰属棄却した完了 ts（棄却ログを完了値ごと1回に抑制）

    def arm(self, nonce):
        """poke 成功直後：nonce を仕掛け、アンカー未解決・送出時刻を now にする。"""
        self.probe, self.probe_ts_cache, self.probe_sent_at = nonce, None, self._clock()

    def clear(self):
        """ダイアログ経由の配達（nonce 無し）：帰属ゲートを無効化する。"""
        self.probe = None

    def response_done(self, agent, path, raw_done):
        """完了検知を「poke への応答」と確定できるときだけ通すゲート。
        キュー投入では nonce の user メッセージが先行ターンの完了前にログへ出るため
        「done > nonce_ts」だけでは先行ターンの完了が通ってしまう（Codex レビュー
        指摘）。codex は turn_id ペアリング（nonce アイテムの turn_id と同じ
        task_complete の ts を確定値として返す）、claude は「最終 assistant
        エントリの parentUuid チェーンが nonce エントリを祖先に持つ」ことまで
        要求する。棄却は新しい完了値のたびに1回だけ可視化する。"""
        if not raw_done or not self.probe:
            return raw_done

        def reject(reason):
            if raw_done != self.last_rejected:
                self.last_rejected = raw_done
                self._dim(f"完了を検知したが poke への応答と紐づかず棄却（{reason}）→ 継続監視")
            return None

        if self.probe_ts_cache is None:
            self.probe_ts_cache = self._find_poke_ts(agent, path, self.probe)
        if self.probe_ts_cache is None:
            return None  # nonce 未着（未配達）— noshow が期限を監視
        if agent == "codex":
            # 応答帰属ゲートでは turn_id 欠落時の位置フォールバックを《一切使わない》（P1-3 /
            # Codex レビュー）。位置推定は queue 投入で先行タスクを誤帰属し得るので、それで
            # 停止 sentinel 判定・レビュー転送・質問回答・プラン自動承認へ進むのは危険。
            # turn_id で確定できなければ compat mode でも帰属不能→reject（＝人間判断待ちで待機）。
            anchor, comp = self._codex_response_complete(path, self.probe)
            if anchor is None:
                return reject("応答帰属不能（turn_id 欠落 or nonce の user アイテム未発見）→ 自律判定に使わず待機")
            if comp is None:
                return reject("応答タスク（同 turn_id）が未完了")
            # texts 窓のアンカーを応答タスク開始時刻へ進める（先行タスク末尾の混入防止）
            self.probe_ts_cache = anchor
            # 確定した応答タスクの完了 ts を返す — raw_done（最新完了）をそのまま
            # 使うと、応答より後に完了した別タスクを応答として採用してしまう
            # （Codex レビュー指摘）
            return comp
        # claude: nonce 以後の完了 かつ 応答チェーンが nonce エントリを祖先に持つこと
        if raw_done <= self.probe_ts_cache:
            return reject("nonce 以前の完了")
        if not self._claude_response_attributed(path, self.probe):
            return reject("応答チェーン不一致")
        return raw_done

    def noshow(self, agent):
        """poke の nonce が一定時間ログに現れない = 配達失敗（キュー投入も不成立）。
        画面検証をすり抜けた未送信をここで確実に検知して停止させる。True を返す直前に
        warn 済み（fail-closed で relay は exit 4）。"""
        if not self.probe or self.probe_ts_cache is not None or not self.probe_sent_at:
            return False
        if self._clock() - self.probe_sent_at < self.POKE_NOSHOW:
            return False
        if self.tracked[agent]:
            self.probe_ts_cache = self._find_poke_ts(agent, self.tracked[agent], self.probe)
            if self.probe_ts_cache is not None:
                return False
        self._warn(f"│ ■ poke（{self.probe}）が{self.POKE_NOSHOW // 60}分経ってもログに現れません。"
                   "未配達（Enter不成立等）とみなし停止します。コンポーザを確認してください。", bell=True)
        return True
class LogWatch:
    """セッションログの「作成時点以降に追記された行」だけを見る監視。送信検証の source of truth。

    Claude Code の画面には実行中でも「esc to interrupt」が出ない（2026-08-16 実測: ツール
    実行中の下部はアイドルと同じ ❯ とステータス行だけ）。以前この検証が通っていたのは
    Claude の返答文に同語句が書かれていた偶然（偽陽性）で、別プロジェクトのペアでは
    codex→claude の配達確認が全滅した（実障害）。一方 jsonl は Enter 成立の瞬間に必ず
    書かれる — アイドル時は type=user（content=文字列）、実行中は type=queue-operation/
    enqueue（content=全文、長文ペーストでも即時・全文を実測）— ので、これを証拠にする。
    ダイアログの解決（承認/差し戻し/decline）は tool_result を持つ user 行として現れる。"""

    def __init__(self, path):
        self.path = path
        try:
            self.offset = os.path.getsize(path) if path else 0
        except OSError:
            self.offset = 0

    def reset(self):
        """チェックポイントを現在のファイル末尾へ進める（以後の追記だけを見る）。"""
        try:
            self.offset = os.path.getsize(self.path) if self.path else 0
        except OSError:
            self.offset = 0

    def new_lines(self):
        if not self.path:
            return []
        try:
            size = os.path.getsize(self.path)
            if size < self.offset:      # 切り詰め/ローテート → 先頭から
                self.offset = 0
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                return fh.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return []

    def has_raw(self, fragment):
        """追記行に fragment（ASCII nonce 等）が生で含まれるか（Codex rollout 用）。"""
        return any(fragment in l for l in self.new_lines())

    def claude_input(self, fragment=None):
        """Claude に「入力として送信された」行が追記されたか: type=user の文字列 content
        （アイドル時）または type=queue-operation/enqueue（実行中）。fragment 指定時は
        本文一致も要求。tool_result 等のブロック content はここでは対象外。"""
        for l in self.new_lines():
            if '"queue-operation"' not in l and '"user"' not in l:
                continue
            try:
                d = peerlog.json.loads(l)
            except ValueError:
                continue
            t = d.get("type")
            if t == "queue-operation":
                if d.get("operation") not in (None, "enqueue"):
                    continue
                text = d.get("content") if isinstance(d.get("content"), str) else ""
            elif t == "user":
                c = (d.get("message") or {}).get("content")
                if not isinstance(c, str):
                    continue
                text = c
            else:
                continue
            if fragment is None or fragment in text:
                return True
        return False

    def claude_resolved(self):
        """tool_result を持つ user 行が追記されたか（= ダイアログの解決: プラン承認/
        差し戻し/decline）。ダイアログ待ちの間 Claude は停止しており、これ以外の
        user 行は書かれないので、承認キー/BTab/Enter の成立証拠になる。"""
        for l in self.new_lines():
            if '"tool_result"' not in l:
                continue
            try:
                d = peerlog.json.loads(l)
            except ValueError:
                continue
            if d.get("type") != "user":
                continue
            c = (d.get("message") or {}).get("content")
            if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                           for b in c):
                return True
        return False


def approval_took_effect(pane, confirm=None):
    """プラン承認キー（数字）押下後、ダイアログ消失（画面）またはログ上の解決
    （confirm = LogWatch.claude_resolved）で成立を確認する。
    キーが吸収されると承認ダイアログが残ったまま relay が Claude 完了待ちで永久停止
    する（Codex レビュー指摘）。ダイアログ消失を成功条件にできるのは、押下直前まで
    PLAN_QUESTION が画面に出ていることが確定しているため。実行中バッジは Claude
    画面では信用できない（2026-08-16）ので使わない。
    capture 失敗・空キャプチャは「ダイアログ消失」と区別できない確認不能なので
    成功条件にせず、ポーリング継続 → timeout で False（Codex レビュー指摘）。"""
    deadline = time.time() + 7
    while time.time() < deadline:
        if confirm is not None and confirm():
            return True
        try:
            cap = tmux("capture-pane", "-p", "-t", pane, capture=True).stdout
        except subprocess.CalledProcessError:
            time.sleep(0.5)
            continue
        if not cap.strip():
            time.sleep(0.5)
            continue
        if PLAN_QUESTION not in cap:
            return True
        time.sleep(0.5)
    return False


def done_banner(rounds, who, all_done=False):
    label = "Codex" if who == "codex" else "Claude"
    what = "全タスク完了を宣言" if all_done else "停止ワードを宣言"
    print(c("ok", f"│ ■ 完了：{label} が{what}。{rounds} 往復でループ終了。"), flush=True)
    print("\a", end="", flush=True)


class StateMachine:
    """aipair relay の状態機械本体（P2-1: relay.main() から切り出し）。claude / codex /
    codex_plan / codex_question の 4 state を回し、ターン完了検知→相手ペインへの poke／ダイアログ
    自動操作（プラン承認・質問回答）／停止ワード・ゲート判定を行い、exit code を返す。ログ/画面副作用
    と密結合な統合レベルのループなので単体テストは効きにくいが、判定核（ResponseGate /
    decide_plan_action / decide_question_action）は既に純粋関数へ分離済みで、ここはそれらを配線して
    副作用を実行する薄いオーケストレータ。relay.main() は arg parse／依存構築／起動／exit のみを担い、
    このクラスを構築して run() するランチャに縮約される。"""

    def __init__(self, a, *, panes, own, cwd, tracked, claude_seen, codex_seen, baseline,
                 sg, rg, bw, poke_codex, poke_codex_next, poke_claude, poke_claude_pass,
                 poke_claude_next, stop_phrases, next_ask_phrases, all_done_phrases):
        self.a = a
        self.panes = panes
        self.own = own
        self.cwd = cwd
        self.tracked = tracked            # relay と共有する可変 dict（lock/refresh でピン更新）
        self.claude_seen = claude_seen
        self.codex_seen = codex_seen
        self.baseline = baseline
        self.sg = sg                      # SchemaGuard（fail-closed schema 監視）
        self.rg = rg                      # ResponseGate（poke nonce ライフサイクル・応答帰属）
        self.bw = bw                      # poke へ渡す idle budget
        self.poke_codex = poke_codex
        self.poke_codex_next = poke_codex_next
        self.poke_claude = poke_claude
        self.poke_claude_pass = poke_claude_pass
        self.poke_claude_next = poke_claude_next
        self.stop_phrases = stop_phrases
        self.next_ask_phrases = next_ask_phrases
        self.all_done_phrases = all_done_phrases

    def run(self):
        """状態機械のメインループを回し、exit code を返す（P2-1: relay.main() から移設）。
        依存は self から一旦ローカルへ展開する — こうすることでループ本体を relay.main() 時代の
        コードと逐語同一（字下げのみ +4）に保ち、移設の挙動差リスクを最小化する。"""
        a = self.a
        panes, own, cwd, tracked = self.panes, self.own, self.cwd, self.tracked
        claude_seen, codex_seen, baseline = self.claude_seen, self.codex_seen, self.baseline
        sg, rg, bw = self.sg, self.rg, self.bw
        poke_codex, poke_codex_next = self.poke_codex, self.poke_codex_next
        poke_claude, poke_claude_pass, poke_claude_next = (self.poke_claude, self.poke_claude_pass,
                                                           self.poke_claude_next)
        stop_phrases, next_ask_phrases, all_done_phrases = (self.stop_phrases, self.next_ask_phrases,
                                                            self.all_done_phrases)

        state = a.start_side
        gate_state = {"fails": 0}
        # endless: 直前に Codex へ何を頼んだか。"review"=コードレビュー / "next"=次タスクの指示。
        # 新しい state を足さず、codex ブロック内の配達処理（ダイアログ回避を含む）を共有するため。
        pending_kind = "review"
        since = baseline
        rounds = 0
        plan_rounds = 0
        plan_dialog = None
        question_rounds = 0        # 連続質問リレー回数（Claude のターン完了でリセット）
        q_unconfirmed_warned = False  # 「画面は質問ダイアログだがログ照合できず」の警告を1回に抑制
        last_activity = time.time()

        def wait_heartbeat(who):
            # 待機フェーズごとに1回だけ表示（None = 表示済み。状態遷移時の time.time() 再セットで再アーム）
            nonlocal last_activity
            if last_activity is not None and time.time() - last_activity > 30:
                dim(f"… {who} の応答待ち")
                last_activity = None

        def claude_watch():
            """Claude 宛配達の直前に作る LogWatch（ログ未特定なら None → 画面フォールバック）。"""
            return LogWatch(tracked["claude"]) if tracked["claude"] else None

        def codex_poke_confirm():
            w = LogWatch(tracked["codex"])
            return lambda p: w.has_raw(p)

        def claude_poke_confirm():
            w = LogWatch(tracked["claude"])
            return lambda p: w.claude_input(p)

        # 終了理由を exit code で区別する（外部 orchestrator が成否を判別できるように）:
        #   0=停止ワード検知（正常完了） 3=最大往復キャップ 4=poke配達失敗
        #   5=プラン/質問リレー上限・選択肢欠落 6=停止ゲート失敗 7=ログschema不一致(fail-closed)
        #   8=人間対応待ち/進捗なし（HUMAN_REQUIRED / no-progress。max-rounds とは別。Phase 2/4 が設定）
        code = 0
        all_done_hit = False
        blocked_reason = None          # code==8 のサブ理由（BLOCKED_HR_REASON / BLOCKED_NOPROGRESS_REASON）
        try:
            while True:
                if sg.guard():          # between-iteration drift（latch 済みは安価な no-op）
                    code = 7
                    break
                if state == "claude":
                    if tracked["claude"] is None:
                        tracked["claude"] = lock_claude(cwd, claude_seen, panes["claude"], baseline)
                    # 🔴 ロック直後・完了判定前に probe（P1-a: probe 前に1回 poke される経路を塞ぐ）
                    if tracked["claude"] and sg.guard():
                        code = 7
                        break
                    done = claude_done_ts(tracked["claude"], since) if tracked["claude"] else None
                    if done and not claude_matches_pane(tracked["claude"], panes["claude"]):
                        # 誤ピン先の end_turn を「Claude 完了」と誤認しない（2026-07-21 Codex レビュー）:
                        # ペイン照合に失敗した完了は採用せず、mtime 条件なしの強制 re-lock で
                        # ペイン一致ログへ乗り換えてから判定をやり直す（一致ログが無ければ保留）
                        dim("完了検知したが追跡ログがペイン照合に不一致（誤ピン疑い）→ 強制 re-lock")
                        relocked = refresh_claude_lock(tracked["claude"], cwd, panes["claude"], force=True)
                        done = claude_done_ts(relocked, since) if relocked != tracked["claude"] else None
                        tracked["claude"] = relocked
                        # 強制 re-lock で新 path へ乗り換えた → response_done（レビュー依頼/停止判定）の
                        # 前に必ず新ログを再 probe。malformed な新ログでは poke せず exit 7（P1-4/Codex）。
                        if sg.guard():
                            code = 7
                            break
                    done = rg.response_done("claude", tracked["claude"], done)
                    if done:
                        time.sleep(a.settle)
                        done = rg.response_done("claude", tracked["claude"],
                                             claude_done_ts(tracked["claude"], since)) or done
                        rounds += 1
                        question_rounds = 0
                        q_unconfirmed_warned = False
                        tstart = max(since, rg.probe_ts_cache) if rg.probe else since
                        texts = turn_texts("claude", tracked["claude"], tstart, done)
                        text = "\n".join(texts)
                        # endless: Claude の手持ちが尽きた宣言なら、レビューではなく
                        # 「次のタスクをリストから指示せよ」を Codex に頼む
                        ask_next = a.endless and hit_stop(texts, next_ask_phrases)
                        log(f"● round {rounds}: " + c("claude", "Claude 完了")
                            + (" → Codex に次タスクを依頼" if ask_next else " → Codex にレビュー依頼"))
                        if text:
                            dim(c("claude", "claude") + ": " + oneline(text))
                        gate_msg = None
                        if (not a.endless) and a.stop_side in ("claude", "both") and hit_stop(texts, stop_phrases):
                            ok_gate, gate_msg = gate_or_message(a, gate_state, cwd)
                            if ok_gate:
                                done_banner(rounds, "claude"); break
                            if gate_msg is None:
                                code = 6; break
                        if gate_msg:      # stop phrase seen but the gate failed → back to Claude, not to Codex
                            sent = poke(panes["claude"], gate_msg, confirm=claude_poke_confirm(),
                                        badge=tracked["claude"] is None, busy_wait=bw)
                        else:
                            sent = poke(panes["codex"], poke_codex_next if ask_next else poke_codex,
                                        confirm=codex_poke_confirm(), busy_wait=bw)
                        if not sent:
                            print(c("warn", f"│ ■ {'Claude' if gate_msg else 'Codex'} への依頼を配達できず（poke失敗）。状態遷移せず停止します。"), flush=True)
                            print("\a", end="", flush=True)
                            code = 4
                            break
                        pending_kind = "next" if ask_next else "review"
                        rg.arm(sent)
                        since = time.time(); state = "claude" if gate_msg else "codex"; last_activity = time.time()
                    elif (not a.no_plan_review) and (plan_dialog := detect_plan_dialog(panes["claude"])):
                        if plan_rounds >= a.plan_rounds:
                            print(c("warn", f"│ ■ プランレビュー上限 {a.plan_rounds} 回に到達。"
                                            f"人間の判断が必要です（ダイアログはそのまま）。停止します。"), flush=True)
                            print("\a", end="", flush=True)
                            code = 5
                            break
                        plan_rounds += 1
                        log("◆ " + c("claude", "プラン承認ダイアログ検知")
                            + f"（{plan_rounds}/{a.plan_rounds}回目）→ Codex にプランレビュー依頼")
                        dim(f"plan: {plan_dialog['plan'] or '(パス不明)'}")
                        sent = poke(panes["codex"], plan_poke_codex(plan_dialog["plan"], a.plan_ok),
                                    confirm=codex_poke_confirm(), busy_wait=bw)
                        if not sent:
                            print(c("warn", "│ ■ Codex へのプランレビュー依頼を配達できず（poke失敗）。"
                                            "状態遷移せず停止します（ダイアログはそのまま）。"), flush=True)
                            print("\a", end="", flush=True)
                            code = 4
                            break
                        rg.arm(sent)
                        since = time.time(); state = "codex_plan"; last_activity = time.time()
                    elif (not a.no_question_relay) and detect_question_dialog(panes["claude"]):
                        if question_rounds >= a.question_rounds:
                            print(c("warn", f"│ ■ 質問リレー上限 {a.question_rounds} 回に到達。"
                                            f"人間の判断が必要です（ダイアログはそのまま）。停止します。"), flush=True)
                            print("\a", end="", flush=True)
                            code = 5
                            break
                        # 全質問を画面から収集（未回答の tool_use は jsonl に無いため画面が唯一のソース）
                        qs_blocks = scrape_questions(panes["claude"])
                        if not qs_blocks:
                            if not q_unconfirmed_warned:
                                dim("質問ダイアログを検知したが質問文を抽出できず → 作用せず待機")
                                q_unconfirmed_warned = True
                            if tracked["claude"]:
                                tracked["claude"] = refresh_claude_lock(tracked["claude"], cwd, panes["claude"])
                            wait_heartbeat("Claude")
                        else:
                            question_rounds += 1
                            log("◆ " + c("claude", "質問ダイアログ検知")
                                + f"（{question_rounds}/{a.question_rounds}回目・{len(qs_blocks)}問）→ Codex に回答依頼")
                            sent = poke(panes["codex"], question_poke_codex(qs_blocks),
                                        confirm=codex_poke_confirm(), busy_wait=bw)
                            if not sent:
                                print(c("warn", "│ ■ Codex への回答依頼を配達できず（poke失敗）。"
                                                "状態遷移せず停止します（ダイアログはそのまま）。"), flush=True)
                                print("\a", end="", flush=True)
                                code = 4
                                break
                            rg.arm(sent)
                            since = time.time(); state = "codex_question"; last_activity = time.time()
                    else:
                        if tracked["claude"]:
                            tracked["claude"] = refresh_claude_lock(tracked["claude"], cwd, panes["claude"])
                        if rg.noshow("claude"):
                            code = 4
                            break
                        wait_heartbeat("Claude")
                elif state == "codex_plan":
                    if tracked["codex"] is None:
                        tracked["codex"] = lock_codex(cwd, codex_seen, panes["codex"])
                    # 🔴 ロック直後・完了判定前に probe（P1-a: 全 state 共通）
                    if tracked["codex"] and sg.guard():
                        code = 7
                        break
                    done = codex_done_ts(tracked["codex"], since) if tracked["codex"] else None
                    done = rg.response_done("codex", tracked["codex"], done)
                    if done:
                        time.sleep(a.settle)
                        done = rg.response_done("codex", tracked["codex"],
                                             codex_done_ts(tracked["codex"], since)) or done
                        tstart = max(since, rg.probe_ts_cache) if rg.probe else since
                        texts = turn_texts("codex", tracked["codex"], tstart, done)
                        text = "\n".join(texts).strip()
                        # 承認判定は停止ワードと同じく sentinel の先頭行完全一致で行う。プランの
                        # 自動承認は不可逆な自動操作なので、否定文・引用・指示文中の言及
                        # （「[AIPAIR_PLAN_APPROVED]とは判断できません」等）を絶対に承認にしない。
                        dialog = detect_plan_dialog(panes["claude"]) or plan_dialog
                        if text:
                            dim(c("codex", "codex") + ": " + oneline(text))
                        # 承認判定（sentinel 先頭行完全一致・feedback 閾値・修正 vs 承認）は
                        # state_machine.decide_plan_action の純粋関数へ切り出した（P2-1・plan_flow）。
                        # ここは「決定 → 副作用（press/send/log/code）」の実行のみ。
                        decision = decide_plan_action(texts, a.plan_ok, dialog)
                        if decision.action == "no_text":
                            log(c("warn", "◆ Codex のレビュー本文を取得できず。ダイアログ検知からやり直します。"))
                        elif decision.action == "no_dialog":
                            log(c("warn", "◆ プランダイアログが見当たりません（人間が操作した？）。通常の待機に戻ります。"))
                        elif decision.action == "approve_feedback":
                            log("◆ " + c("ok", "Codex がプラン承認（付帯コメントあり）")
                                + " → feedback付きで承認（shift+tab）")
                            if not send_plan_feedback(panes["claude"], dialog, decision.payload, approve=True,
                                                      watch=claude_watch()):
                                print(c("warn", "│ ■ feedback付き承認（Shift+Tab）の成立を確認できず。"
                                                "状態遷移せず停止します。"), flush=True)
                                print("\a", end="", flush=True)
                                code = 4
                                break
                            plan_rounds = 0
                        elif decision.action == "approve":
                            log("◆ " + c("ok", "Codex がプラン承認") + f" → 「{dialog['yes_label']}」を選択")
                            w = claude_watch()
                            press(panes["claude"], dialog["yes"])
                            if not approval_took_effect(panes["claude"],
                                                       confirm=(w.claude_resolved if w else None)):
                                print(c("warn", "│ ■ プラン承認キーの押下が効いていません（ダイアログ残存）。"
                                                "状態遷移せず停止します。"), flush=True)
                                print("\a", end="", flush=True)
                                code = 4
                                break
                            plan_rounds = 0
                        elif decision.action == "no_tell_option":
                            print(c("warn", "│ ■ 『Tell Claude what to change』の選択肢が見つからず"
                                            "修正依頼を送れません。停止します。"), flush=True)
                            print("\a", end="", flush=True)
                            code = 5
                            break
                        else:  # "changes"
                            log("◆ " + c("codex", "Codex が修正を要求")
                                + " → 「Tell Claude what to change」で送信")
                            if not send_plan_feedback(panes["claude"], dialog, decision.payload, approve=False,
                                                      watch=claude_watch()):
                                print(c("warn", "│ ■ プラン修正依頼の送信を確認できず（Enter失敗）。"
                                                "状態遷移せず停止します。"), flush=True)
                                print("\a", end="", flush=True)
                                code = 4
                                break
                        rg.clear()    # ダイアログ経由の配達に nonce は無い（帰属ゲート不使用）
                        since = time.time(); state = "claude"; last_activity = time.time()
                    else:
                        if tracked["codex"]:
                            tracked["codex"] = refresh_codex_lock(tracked["codex"], cwd, panes["codex"])
                        if rg.noshow("codex"):
                            code = 4
                            break
                        wait_heartbeat("Codex（プランレビュー）")
                elif state == "codex_question":
                    if tracked["codex"] is None:
                        tracked["codex"] = lock_codex(cwd, codex_seen, panes["codex"])
                    # 🔴 ロック直後・完了判定前に probe（P1-a: 全 state 共通）
                    if tracked["codex"] and sg.guard():
                        code = 7
                        break
                    done = codex_done_ts(tracked["codex"], since) if tracked["codex"] else None
                    done = rg.response_done("codex", tracked["codex"], done)
                    if done:
                        time.sleep(a.settle)
                        done = rg.response_done("codex", tracked["codex"],
                                             codex_done_ts(tracked["codex"], since)) or done
                        tstart = max(since, rg.probe_ts_cache) if rg.probe else since
                        texts = turn_texts("codex", tracked["codex"], tstart, done)
                        text = "\n".join(texts).strip()
                        # 配達直前に再検知する（Codex の回答中に人間が操作した可能性があるため）
                        qdlg = detect_question_dialog(panes["claude"])
                        if text:
                            dim(c("codex", "codex") + ": " + oneline(text))
                        # 判定（no_text / no_dialog / deliver）は state_machine.decide_question_action の
                        # 純粋関数へ切り出した（P2-1・question_flow）。ここは決定→副作用の実行のみ。
                        decision = decide_question_action(texts, qdlg)
                        if decision.action == "no_text":
                            log(c("warn", "◆ Codex の回答本文を取得できず。ダイアログ検知からやり直します。"))
                        elif decision.action == "no_dialog":
                            log(c("warn", "◆ 質問ダイアログが見当たりません（人間が操作した？）。通常の待機に戻ります。"))
                        else:  # "deliver"
                            log("◆ " + c("ok", "Codex が回答") + " → 「Chat about this」経由で配達")
                            if not send_question_answer(panes["claude"], qdlg, decision.payload,
                                                        watch=claude_watch()):
                                # ダイアログは chat 押下で既に閉じており、未送信のまま state を
                                # 進めると永久停止する（Codex レビュー指摘）→ 明示停止
                                print(c("warn", "│ ■ 質問回答の送信を確認できず（Enter失敗）。"
                                                "状態遷移せず停止します。回答はコンポーザに残っています。"), flush=True)
                                print("\a", end="", flush=True)
                                code = 4
                                break
                        rg.clear()    # ダイアログ経由の配達に nonce は無い（帰属ゲート不使用）
                        since = time.time(); state = "claude"; last_activity = time.time()
                    else:
                        if tracked["codex"]:
                            tracked["codex"] = refresh_codex_lock(tracked["codex"], cwd, panes["codex"])
                        if rg.noshow("codex"):
                            code = 4
                            break
                        wait_heartbeat("Codex（質問回答）")
                else:  # codex
                    if tracked["codex"] is None:
                        tracked["codex"] = lock_codex(cwd, codex_seen, panes["codex"])
                    # 🔴 ロック直後・完了判定前に probe（P1-a）
                    if tracked["codex"] and sg.guard():
                        code = 7
                        break
                    done = codex_done_ts(tracked["codex"], since) if tracked["codex"] else None
                    done = rg.response_done("codex", tracked["codex"], done)
                    if done:
                        time.sleep(a.settle)
                        done = rg.response_done("codex", tracked["codex"],
                                             codex_done_ts(tracked["codex"], since)) or done
                        tstart = max(since, rg.probe_ts_cache) if rg.probe else since
                        texts = turn_texts("codex", tracked["codex"], tstart, done)
                        text = "\n".join(texts)
                        log("● " + c("codex", "Codex 次タスク指示 完了" if pending_kind == "next"
                                              else "Codex レビュー完了"))
                        if text:
                            dim(c("codex", "codex") + ": " + oneline(text))
                        msg_claude, back_text = poke_claude, text
                        # 既定モード: 停止ワードでループ終了（従来どおり）。--gate があればその成功が条件
                        if (not a.endless) and a.stop_side in ("codex", "both") and hit_stop(texts, stop_phrases):
                            ok_gate, gate_msg = gate_or_message(a, gate_state, cwd)
                            if ok_gate:
                                done_banner(rounds, "codex"); break
                            if gate_msg is None:
                                code = 6; break
                            msg_claude = back_text = gate_msg          # review passed, gate did not → back to Claude
                        # 連続モードの終端は「全タスク完了」宣言のみ。レビュー中の宣言も尊重する
                        # （hit_stop は最終メッセージの冒頭100字判定なので、明示的に書いた時だけ効く）
                        if a.endless:
                            if hit_stop(texts, all_done_phrases):
                                all_done_hit = True
                                done_banner(rounds, "codex", all_done=True); break
                            if pending_kind == "next":
                                msg_claude = poke_claude_next
                            elif a.stop_side in ("codex", "both") and hit_stop(texts, stop_phrases):
                                ok_gate, gate_msg = gate_or_message(a, gate_state, cwd)
                                if ok_gate:
                                    log("◆ " + c("ok", "Codex がレビュー合格") + " → Claude に次のタスクを促す")
                                    msg_claude = poke_claude_pass
                                elif gate_msg is None:
                                    code = 6; break
                                else:
                                    msg_claude = back_text = gate_msg
                        if rounds >= a.max_rounds:
                            print(c("warn", f"│ ■ 最大 {a.max_rounds} 往復に到達。安全のため停止します。"), flush=True)
                            print("\a", end="", flush=True)
                            code = 3
                            break
                        # Claude がプラン承認ダイアログで停止中だと poke は物理的に届かない
                        # （ダイアログはリテラル入力をエコーしない）。その場合は Codex のレビューを
                        # 「Tell Claude what to change」経由で配達する（2026-07-20 実バグ:
                        # レビュー中に Claude がプランを提示 → poke 3連続失敗で relay 死亡）。
                        dialog = None if a.no_plan_review else detect_plan_dialog(panes["claude"])
                        # 質問ダイアログ中も同様に poke は届かず、さらに poke の nonce（16進）の数字が
                        # 選択として解釈され画面が変わると「画面変化=配達成功」フォールバックが誤爆して
                        # Enter が飛ぶ（=選択肢を誤送信）リスクがある → 「Chat about this」経由で配達
                        qdlg = None if a.no_question_relay else detect_question_dialog(panes["claude"])
                        delivered_back = True
                        new_probe = None
                        if dialog and dialog["tell"]:
                            log("◆ " + c("claude", "Claude はプラン承認待ち")
                                + " → レビューを「Tell Claude what to change」経由で配達")
                            delivered_back = send_plan_feedback(panes["claude"], dialog, back_text, approve=False,
                                                                watch=claude_watch())
                        elif qdlg:
                            log("◆ " + c("claude", "Claude は質問ダイアログ表示中")
                                + " → レビューを「Chat about this」経由で配達")
                            delivered_back = send_question_answer(panes["claude"], qdlg, back_text,
                                                                  watch=claude_watch())
                        else:
                            # Claude 宛: 画面バッジは信用しない（badge=False）。ログ未特定の
                            # 劣化時のみバッジにフォールバックし、その旨を可視化する
                            if tracked["claude"] is None:
                                dim("Claude ログ未特定 → 送信検証を画面バッジにフォールバック（信頼度低）")
                            new_probe = poke(panes["claude"], msg_claude, confirm=claude_poke_confirm(),
                                             badge=tracked["claude"] is None, busy_wait=bw)
                            if not new_probe:
                                print(c("warn", "│ ■ Claude への依頼を配達できず（poke失敗）。状態遷移せず停止します。"), flush=True)
                                print("\a", end="", flush=True)
                                code = 4
                                break
                        if not delivered_back:
                            print(c("warn", "│ ■ レビューの配達（Enter）を確認できず。状態遷移せず停止します。"
                                            "本文はコンポーザに残っています。"), flush=True)
                            print("\a", end="", flush=True)
                            code = 4
                            break
                        pending_kind = "review"
                        rg.arm(new_probe)
                        since = time.time(); state = "claude"; last_activity = time.time()
                    else:
                        if tracked["codex"]:
                            tracked["codex"] = refresh_codex_lock(tracked["codex"], cwd, panes["codex"])
                        if rg.noshow("codex"):
                            code = 4
                            break
                        wait_heartbeat("Codex")
                time.sleep(a.poll)
        except KeyboardInterrupt:
            print("\n" + c("warn", f"│ ■ 中断しました（{rounds} 往復）。"), flush=True)
            set_pane_title(own, f"relay ■ 中断 / {rounds}往復")
            return 130
        # 終了後もタイトルで結果が分かるようにする（走行中と区別がつかないと、
        # 何時間も前に終わった relay を「まだ回っている」と誤読する）
        reason = {0: "全タスク完了" if all_done_hit else "停止ワード", 3: "キャップ到達",
                  4: "配達失敗", 5: "上限到達", 6: "停止ゲート失敗", 7: "schema不一致",
                  8: blocked_reason or BLOCKED_HR_REASON}.get(code, f"exit={code}")
        set_pane_title(own, f"relay ■ 終了({reason}) / {rounds}往復")
        return code
