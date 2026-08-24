"""aipair question_flow — 質問リレー回答の判定核（P2-1: relay 状態機械の named module）。

Claude が AskUserQuestion の選択ダイアログで停止したとき、relay は Codex に回答させ、その本文を
『Chat about this』経由で Claude へ配達する。plan_flow と対称に、判定（回答本文をどう扱うか）を
副作用（send_question_answer / log / code / break — state_machine.StateMachine 側）から切り離した
純粋関数にする。プランと違い承認 sentinel は無く、回答はそのまま中継する。
"""
import collections

from .corelib import hit_stop

QuestionDecision = collections.namedtuple("QuestionDecision", "action payload")


def decide_question_action(texts, qdlg, human_required_phrases=()):
    """Codex の質問回答 → 取るべきアクションを《純粋に》決める（副作用なし・tmux 非依存）。
    plan_flow と同じ「判定/副作用」分離（P2-1・question_flow）。返す action:
      no_text        回答本文を取得できず（ダイアログ検知からやり直し）
      human_required 人間判断が必要（承認/権限/課金/不可逆等）→ Claude へ送らず exit 8 で停止（P1-2）
      no_dialog      質問ダイアログが消えた（人間が操作した？ 通常待機へ）
      deliver        回答（payload=本文）を『Chat about this』経由で配達

    P1-2（社長指示 2026-08-24）: Codex が最終回答の先頭行に `[AIPAIR_HUMAN_REQUIRED]` を単独で出したら、
    その質問は AI が代理判断すべきでない（承認・権限・意思決定・秘密・課金・不可逆・本番操作）。
    Claude へ回答を送らず HUMAN_REQUIRED で停止する。task-list の `[!]` とは別経路（todo は変更しない）。"""
    text = "\n".join(texts).strip()
    if not text:
        return QuestionDecision("no_text", None)
    if human_required_phrases and hit_stop(texts, human_required_phrases):
        return QuestionDecision("human_required", None)
    if qdlg is None:
        return QuestionDecision("no_dialog", None)
    return QuestionDecision("deliver", text)
