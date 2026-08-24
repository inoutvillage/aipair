"""aipair question_flow — 質問リレー回答の判定核＋ハンドラ（P2-1 named module / P2-5 分離）。

Claude が AskUserQuestion の選択ダイアログで停止したとき、relay は Codex に回答させ、その本文を
『Chat about this』経由で Claude へ配達する。plan_flow と対称に、判定（回答本文をどう扱うか）と
「run() が適用すべき結果（outcome）」を副作用（send_question_answer / print / code / break —
state_machine.StateMachine 側）から切り離した純粋関数にする。

P2-5（社長指示 2026-08-24）: state_machine の再肥大化を避けるため、質問リレーの停止判定・banner・
終了結果はこの module に集約し、`StateMachine.run()` は `handle_question_answer()` の返す
`QuestionOutcome` を**適用するだけ**にする（分岐・banner を state_machine へ直書きしない）。
tmux/ログには一切依存しない（banner は (level, text) の行データで返し、着色/print は run() 側）。
"""
import collections

from .corelib import hit_stop
from .review_protocol import question_payload_text, QUESTION_RELAY_LIMIT

QuestionDecision = collections.namedtuple("QuestionDecision", "action payload")
# P1-3: 質問を自動中継してよいか（kind ∈ {relay, human_required}）。human_required は上限超過で
# truncate せず停止する（level/log/banner は run() が出す）。relay は level/log/banner=None。
QuestionRelay = collections.namedtuple("QuestionRelay", "kind level log banner")
# run() が適用する結果。kind ∈ {retry_detect, human_required, back_to_wait, deliver}。
#   level/log : 1 行ログ（未着色・run() が c(level, log) で出す）
#   banner    : HUMAN_REQUIRED 停止時の (level|None, text) 行列（他は None）
#   payload   : deliver 時の配達本文（他は None）
QuestionOutcome = collections.namedtuple("QuestionOutcome", "kind level log banner payload")


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


def question_oversize_banner_lines(blocks, limit, actual, preview=200):
    """P1-3: 質問が自動中継上限を超えたときの停止 banner を (level|None, text) 行列で返す（純粋）。
    質問全文は載せず（長大なため）先頭のみ preview 文字だけ示す。"""
    head = blocks[0] if blocks else ""
    if len(head) > preview:
        head = head[:preview] + "…"
    return [(None, ""),
            ("warn", "│ ■ 自動処理を停止しました"),
            ("warn", f"│   理由: 質問内容が自動中継上限（{limit}字）を超えています（実 {actual}字）。"),
            ("warn", "│         truncate すると Codex が不完全な質問に回答してしまうため、"
                     "人間確認待ちで停止します。"),
            ("warn", "│   質問（先頭のみ）:"),
            ("dim", f"│     {head}"),
            ("warn", "│   人間が Claude 側で回答した後、relay を再開してください。")]


def decide_question_relay(blocks, limit=QUESTION_RELAY_LIMIT):
    """Claude の質問を Codex へ自動中継してよいか判定する（P1-3・純粋・副作用なし）。

    質問本文（`review_protocol.question_payload_text` と同一の組み立て）が limit を超えるなら、
    **truncate せず** human_required（run() 側で exit 8）へ倒す — 不完全な質問を Codex に渡して
    推測回答させない（社長指示: 完全な質問内容を取得できない場合は推測して進めない）。上限内は relay。"""
    actual = len(question_payload_text(blocks))
    if actual > limit:
        return QuestionRelay("human_required", "warn",
                             "◆ 質問内容が自動中継上限を超えたため人間確認待ちで停止します。",
                             question_oversize_banner_lines(blocks, limit, actual))
    return QuestionRelay("relay", None, None, None)


def human_required_banner_lines(qs_blocks, rounds, exit_code):
    """質問 HUMAN_REQUIRED 停止 banner を (level|None, text) の行列で返す（純粋・print は run() 側）。
    level=None の行はそのまま（無着色）出力する。qs_blocks は検知した質問ブロック列。"""
    lines = [(None, ""),
             ("warn", "│ ■ 自動処理を停止しました"),
             ("warn", "│   理由: Claude の質問に人間の判断が必要です"
                      f"（HUMAN_REQUIRED・exit {exit_code}・{rounds} 往復）"),
             ("warn", "│   質問:")]
    lines += [("dim", f"│     {b}") for b in (qs_blocks or [])]
    lines.append(("warn", "│   人間が Claude 側で回答した後、relay を再開してください。"))
    return lines


def handle_question_answer(texts, qdlg, human_required_phrases, qs_blocks, rounds, exit_code):
    """Codex の質問回答 → `StateMachine.run()` が適用すべき `QuestionOutcome` を返す（純粋・P2-5）。

    run() 側は kind に応じて:
      retry_detect  : ログのみ → claude state へ戻す（ダイアログ再検知でやり直し）
      human_required: ログ + banner を出し、exit `exit_code`（HUMAN_REQUIRED）で停止（回答は送らない）
      back_to_wait  : ログのみ → claude state へ戻す（人間がダイアログ操作した想定）
      deliver       : ログ後、payload を send_question_answer で配達（失敗時は run() が exit 4）
    """
    decision = decide_question_action(texts, qdlg, human_required_phrases)
    if decision.action == "no_text":
        return QuestionOutcome("retry_detect", "warn",
                               "◆ Codex の回答本文を取得できず。ダイアログ検知からやり直します。",
                               None, None)
    if decision.action == "human_required":
        return QuestionOutcome("human_required", "warn",
                               "◆ Codex が回答を保留（HUMAN_REQUIRED）→ 人間判断が必要として停止します。",
                               human_required_banner_lines(qs_blocks, rounds, exit_code), None)
    if decision.action == "no_dialog":
        return QuestionOutcome("back_to_wait", "warn",
                               "◆ 質問ダイアログが見当たりません（人間が操作した？）。通常の待機に戻ります。",
                               None, None)
    return QuestionOutcome("deliver", "ok",
                           "◆ Codex が回答 → 「Chat about this」経由で配達", None, decision.payload)
