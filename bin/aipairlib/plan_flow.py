"""aipair plan_flow — プランレビュー結果の判定核（P2-1: relay 状態機械の named module）。

Claude がプラン承認ダイアログで停止したとき、relay は Codex にプランをレビューさせ、その結果を
《承認 / feedback 付き承認 / 修正要求 / 停止》へ振り分ける。この判定は権限バイパス下で最も高リスクな
不可逆自律アクション（プランの自動承認）を左右するため、tmux/画面・ループ状態から切り離した純粋関数に
して単体テスト可能にする。副作用（press / send_plan_feedback / log / code / break）は
state_machine.StateMachine 側に残り、ここは「本文 → 取るべき action」だけを返す。
"""
import collections

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
