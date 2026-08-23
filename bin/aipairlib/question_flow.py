"""aipair question_flow — 質問リレー回答の判定核（P2-1: relay 状態機械の named module）。

Claude が AskUserQuestion の選択ダイアログで停止したとき、relay は Codex に回答させ、その本文を
『Chat about this』経由で Claude へ配達する。plan_flow と対称に、判定（回答本文をどう扱うか）を
副作用（send_question_answer / log / code / break — state_machine.StateMachine 側）から切り離した
純粋関数にする。プランと違い承認 sentinel は無く、回答はそのまま中継する。
"""
import collections

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
