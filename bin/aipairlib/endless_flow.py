"""endless モードの終端判定（純関数・I/O なし）。

社長指示 2026-08-24（`_reference/new-task.md`）§2/§4: endless の終端は「未完了/完了」の 2 値でなく
READY / BLOCKED / ALL_DONE の 3 状態で制御する。**relay が読む task-list の分類が唯一の権威**であり、
agent が出した終端 sentinel（`[AIPAIR_ALL_DONE]` / `[AIPAIR_HUMAN_REQUIRED]`）は、分類が一致した
時だけ honor する。分類に READY が残る（着手可 `[ ]` がある）なら、sentinel が出ても拒否して継続する
（誤 sentinel で未処理の `[ ]` を残して止めない — §11 Case 6）。

no-progress（同一タスクの停滞）は分類とは独立の relay 内部経路（Phase 4）であり、ここでは扱わない。
"""
# task-list 分類の state（tasklist.py と一致させる。循環 import を避けるため文字列で持つ）
READY = "READY"
BLOCKED = "BLOCKED"
ALL_DONE = "ALL_DONE"


def decide_endless_terminal(saw_all_done, saw_human_required, state):
    """終端 sentinel フラグ＋task-list 分類 state から終端の可否を決める（純関数）。

    戻り値:
      "all_done"       — [AIPAIR_ALL_DONE] を分類 ALL_DONE が支持 → exit 0 で正常終了
      "human_required" — [AIPAIR_HUMAN_REQUIRED] を分類 BLOCKED が支持 → exit 8（HUMAN_REQUIRED）
      "reject"         — 終端 sentinel は出たが分類が支持しない（READY 残・不一致）→ 無視して継続
      None             — 終端 sentinel を検出していない（通常継続）
    """
    if not (saw_all_done or saw_human_required):
        return None
    if saw_all_done and state == ALL_DONE:
        return "all_done"
    if saw_human_required and state == BLOCKED:
        return "human_required"
    return "reject"
