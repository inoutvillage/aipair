"""endless モードの終端判定（純関数・I/O なし）。

社長指示 2026-08-24（`_reference/new-task.md`）§2/§4: endless の終端は「未完了/完了」の 2 値でなく
READY / BLOCKED / ALL_DONE の 3 状態で制御する。**relay が読む task-list の分類が唯一の権威**であり、
agent が出した終端 sentinel（`[AIPAIR_ALL_DONE]` / `[AIPAIR_HUMAN_REQUIRED]`）は、分類が一致した
時だけ honor する。分類に READY が残る（着手可 `[ ]` がある）なら、sentinel が出ても拒否して継続する
（誤 sentinel で未処理の `[ ]` を残して止めない — §11 Case 6）。

no-progress（同一タスクの停滞）は分類とは独立の relay 内部経路（Phase 4）であり、ここでは扱わない。
"""
import re

# task-list 分類の state（tasklist.py と一致させる。循環 import を避けるため文字列で持つ）
READY = "READY"
BLOCKED = "BLOCKED"
ALL_DONE = "ALL_DONE"

# no-progress guard（§8）: 今回 Codex が指示したタスクの識別子が同定できない時の番兵。
UNRESOLVED = "UNRESOLVED"


def _echo_candidates(codex_text):
    """Codex 応答から、ready 行と**完全一致**で照合する候補文字列の集合を作る。

    verbatim 契約: 行内容はそのまま保持し、除去するのは Markdown の**外側バッククォート**だけ
    （内側の先頭・末尾空白＝インデントは保持）。候補は (a) 各行そのまま (b) 行全体が `` `…` `` なら
    その中身 (c) 行内のバッククォート囲みスパン `` `…` ``。"""
    cands = set()
    for ln in codex_text.splitlines():
        cands.add(ln)                              # 行そのまま（verbatim）
        s = ln.strip()                             # 行全体が `…`（外側空白は装飾なので許容）
        if len(s) >= 2 and s[0] == "`" and s[-1] == "`" and "`" not in s[1:-1]:
            cands.add(s[1:-1])                     # 外側バッククォートのみ除去・内側空白は保持
    for span in re.findall(r"`([^`\n]+)`", codex_text):
        cands.add(span)                            # 行内バッククォート span（verbatim）
    return cands


def resolve_task_identity(codex_text, ready_lines):
    """Codex の次タスク指示から、task-list の着手可行（`ready_lines`＝classify()['ready']）に
    **逐語一致する行を丁度1件**同定して返す（no-progress 判定の識別子）。

    識別子は task-list 上の verbatim `- [ ]` 行に固定（安定 ID 方式は採らない）。Codex はその行を
    逐語エコーする契約（`endless_poke_codex_next` で指定）。**fail-closed**: 抽出失敗・一致 0 件・
    一致 ≥2 件（曖昧）は `UNRESOLVED` を返し、呼び出し側は no-progress ストリークを進める
    （同一性を判定できないまま無限往復させない）。

    戻り値は一致した **verbatim の ready 行**（比較は正規化するが返り値は原文）、または `UNRESOLVED`。
    """
    cands = _echo_candidates(codex_text)
    # ready 行は verbatim で完全一致比較（正規化しない）。full-line/span 単位なので prefix 部分一致
    # （"…A" と "…A extended"）でも、同本文でインデント違いの2項目でも誤検出しない。
    matched = [ln for ln in ready_lines if ln and ln in cands]
    return matched[0] if len(matched) == 1 else UNRESOLVED


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
