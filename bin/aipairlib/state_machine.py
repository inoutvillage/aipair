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
import collections
import time

from .corelib import head_line
from .review_protocol import plan_extra_comment

# Codex のプランレビュー本文から決めた「取るべきアクション」。payload は approve_feedback の
# 付帯コメント / changes の修正依頼本文（他は None）。副作用（press/send/log/code）はループ側。
PlanDecision = collections.namedtuple("PlanDecision", "action payload")


def decide_plan_action(texts, plan_ok, dialog):
    """Codex のプランレビュー結果 → 取るべきアクションを《純粋に》決める（副作用なし・tmux 非依存）。
    プランの自動承認は権限バイパス下で最も高リスクな不可逆自律アクションなので、判定を副作用から
    切り離して単体テスト可能にする（P2-1・plan_flow）。返す action:
      no_text        レビュー本文を取得できず（ダイアログ検知からやり直し）
      no_dialog      プランダイアログが消えた（人間が操作した？ 通常待機へ）
      approve        承認（sentinel 先頭行完全一致・付帯コメント僅少 or feedback 不可）
      approve_feedback  承認＋付帯コメント（payload）を feedback として添える（shift+tab）
      no_tell_option 修正要求だが『Tell Claude what to change』が無い（停止）
      changes        修正要求（payload=本文）を Tell 経由で送る
    承認は停止ワードと同じく **sentinel の先頭行完全一致** のみで成立させる — 否定文・引用・
    指示文中の言及（「[AIPAIR_PLAN_APPROVED]とは判断できません」等）を絶対に承認にしない。付帯
    コメントは《最終メッセージから先頭 sentinel を除いた残り》で測る（連結全文だと先行ナレーション
    が長いだけで feedback 付き承認へ誤分岐する — Codex レビュー）。"""
    text = "\n".join(texts).strip()
    if not text:
        return PlanDecision("no_text", None)
    if dialog is None:
        return PlanDecision("no_dialog", None)
    plan_head = head_line(texts[-1]) if texts else ""
    if plan_head == plan_ok:
        extra = plan_extra_comment(texts, plan_ok)
        if len(extra) > 80 and dialog["tell"]:
            return PlanDecision("approve_feedback", extra)
        return PlanDecision("approve", None)
    if dialog["tell"] is None:
        return PlanDecision("no_tell_option", None)
    return PlanDecision("changes", text)


QuestionDecision = collections.namedtuple("QuestionDecision", "action payload")


def decide_question_action(texts, qdlg):
    """Codex の質問回答 → 取るべきアクションを《純粋に》決める（副作用なし・tmux 非依存）。
    plan_flow と同じ「判定/副作用」分離（P2-1・question_flow）。返す action:
      no_text    回答本文を取得できず（ダイアログ検知からやり直し）
      no_dialog  質問ダイアログが消えた（人間が操作した？ 通常待機へ）
      deliver    回答（payload=本文）を『Chat about this』経由で配達
    プランと違い承認 sentinel は無い — 質問回答はそのまま中継する（判定核は薄いが、no_text→
    再検知 / no_dialog→人間操作 / else→配達 の不変条件を plan と対称にテスト可能にする）。"""
    text = "\n".join(texts).strip()
    if not text:
        return QuestionDecision("no_text", None)
    if qdlg is None:
        return QuestionDecision("no_dialog", None)
    return QuestionDecision("deliver", text)


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
