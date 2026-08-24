"""endless モードの応答判定 / controller（判定は純粋。分類 I/O だけ注入 callback 経由）。

社長指示 2026-08-24（`_reference/new-task.md`）§2/§4/§8: endless の終端は「未完了/完了」の 2 値でなく
READY / BLOCKED / ALL_DONE の 3 状態で制御する。**relay が読む task-list の分類が唯一の権威**であり、
agent が出した終端 sentinel（`[AIPAIR_ALL_DONE]` / `[AIPAIR_HUMAN_REQUIRED]`）は、分類が一致した
時だけ honor する。分類に READY が残る（着手可 `[ ]` がある）なら、sentinel が出ても拒否して継続する
（誤 sentinel で未処理の `[ ]` を残して止めない — §11 Case 6）。

このモジュールが所有するもの（P2-5 で state_machine から集約）:
- 終端判定 `decide_endless_terminal`（sentinel × 分類 → all_done / human_required / reject）
- no-progress guard（§8）: 停滞ストリーク `advance_no_progress` ／ 逐語識別子の同定 `resolve_task_identity`
- 停止 banner の文言ビルダ `human_required_banner_lines` / `no_progress_banner_lines`（(level,text) 行データ）
- Codex 応答 → run() が適用する `EndlessOutcome` を生成する `handle_endless_response`

**個々の判定 helper は純粋**（`decide_endless_terminal` / `advance_no_progress` / `resolve_task_identity` /
banner ビルダは同じ入力 → 同じ出力）。**controller `handle_endless_response` 自体は直接 I/O を持たない**が、
task-list 分類だけは**可変な外部状態**を読む副作用なので **注入 callback `classify` 経由**で行う
（＝同じ引数・同じ callback オブジェクトでも、その時点の task-list 次第で outcome は変わり得る＝参照透過ではない）。
classify は元の実装と同じ条件でのみ呼ぶ（終端 sentinel 検出時／`pending_kind == "next"` 時。レビュー継続時は
呼ばない）。print / poke / state 遷移・exit code などの副作用は run()（state_machine）側で outcome を適用して行う。
"""
import collections
import re

from .corelib import hit_stop

# task-list 分類の state（tasklist.py と一致させる。循環 import を避けるため文字列で持つ）
READY = "READY"
BLOCKED = "BLOCKED"
ALL_DONE = "ALL_DONE"

# no-progress guard（§8）: 今回 Codex が指示したタスクの識別子が同定できない時の番兵。
UNRESOLVED = "UNRESOLVED"

# reject / UNRESOLVED 時の警告文言（P2-5: state_machine から controller へ集約）
REJECT_LOG = ("終端 sentinel を task-list 分類が支持しない（着手可 [ ] が残存）"
              "→ Codex に具体的な着手可タスクの選択を再要求")
UNRESOLVED_LOG = ("next タスクの識別子を task-list 内で一意に同定できません"
                  "（Codex の逐語エコー欠落 or 曖昧な複数一致）→ UNRESOLVED として no-progress ストリークを進めます")


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


NO_PROGRESS_LIMIT = 3        # 同一 signature を連続 N 回選択で停止（初版は定数固定・env 調整は導入しない）


def advance_no_progress(prev, cur_id, cur_hash, limit=NO_PROGRESS_LIMIT):
    """no-progress ストリークを 1 手進める純関数（§8・簡易版）。

    prev = (prev_id, prev_hash, streak) or None（初回）。今回の (cur_id, cur_hash) が「進捗なし」
    ——**(同一識別子の再選択 OR `UNRESOLVED`) AND snapshot hash 不変**——なら streak を増やし、そうで
    なければ 1 に戻す（新しい識別子の解決・snapshot hash 変化でリセット）。streak が limit に達したら停止。

    戻り値: ((cur_id, cur_hash, streak), should_stop)。Case 5: 同一タスクを 2回目までは継続・3回目で停止
    （streak: 1→2→3、limit=3 で 3回目に stop）。
    """
    if prev is None:
        return ((cur_id, cur_hash, 1), False)
    prev_id, prev_hash, streak = prev
    stalled = (cur_hash == prev_hash) and (cur_id == UNRESOLVED or cur_id == prev_id)
    streak = streak + 1 if stalled else 1
    return ((cur_id, cur_hash, streak), streak >= limit)


def human_required_banner_lines(cls, rounds, exit_code):
    """HUMAN_REQUIRED（分類 BLOCKED）停止 banner を (level|None, text) 行列で返す（§6・純粋）。
    残 `[!]` 項目名＋blocker 理由を列挙。level=None の行は無着色。print は run() 側（P2-5）。"""
    lines = [(None, ""),
             ("warn", "│ ■ 自動処理を停止しました"),
             ("warn", "│   理由: 人間対応が必要なタスク（`[!]`）のみ残っています"
                      f"（HUMAN_REQUIRED・exit {exit_code}・{rounds} 往復）"),
             ("warn", "│   残タスク:")]
    for b in cls.get("blocked", []):
        lines.append(("warn", f"│     {b['item']}"))
        lines.append(("dim", f"│       blocker: {b['blocker']}"))
    lines.append(("warn", "│   人間対応後、再度 endless を開始してください。"))
    return lines


def no_progress_banner_lines(np_state, rounds, exit_code):
    """no-progress（同一タスク停滞）停止 banner を (level|None, text) 行列で返す（§6・純粋）。
    繰り返された項目・ストリーク数・snapshot hash を表示。`[!]` 一覧に依存しない。print は run() 側。"""
    ident, hashv, streak = np_state
    what = "UNRESOLVED（識別子を task-list 内で一意に同定できず）" if ident == UNRESOLVED else ident
    return [(None, ""),
            ("warn", "│ ■ 自動処理を停止しました"),
            ("warn", "│   理由: 進捗がないまま同じタスクが再選択されています"
                     f"（no-progress・exit {exit_code}・{rounds} 往復）"),
            ("warn", f"│   繰り返された項目: {what}"),
            ("dim", f"│   連続回数: {streak} / task-list snapshot hash: {hashv}"),
            ("warn", "│   人間確認が必要な可能性があります。")]


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


# run() が適用する endless 応答アウトカム（P2-5: 判定分岐を state_machine から controller へ集約）。
#   kind:
#     "all_done"       全タスク完了（分類 ALL_DONE が sentinel を支持）→ done_banner・exit 0
#     "human_required" 人間対応のみ残（分類 BLOCKED が支持）→ banner・reason・exit 8
#     "no_progress"    同一タスク停滞 3 連続 → banner・reason・exit 8
#     "reject_repoke"  誤 sentinel（分類 READY）→ Claude へ送らず Codex に選択を再要求（continue）
#     "advance_next"   次タスク指示を Claude へ配達（msg_claude=poke_claude_next）
#     "review"         レビュー継続（pending_kind!=next・終端 sentinel 無し）→ run() が stop-word/gate 処理
#   log    : run() が warn 色で出す 1 行（無ければ None）
#   banner : 停止 banner の (level,text) 行列（stop 系のみ・他 None）
#   reason : blocked_reason（stop 系のみ・他 None）
#   np_state: 更新後の no-progress 状態（呼び出し側が持ち回す）
EndlessOutcome = collections.namedtuple("EndlessOutcome", "kind log banner reason np_state")


def handle_endless_response(texts, all_done_phrases, human_required_phrases,
                            pending_kind, np_state, rounds, exit_code,
                            hr_reason, noprogress_reason, classify):
    """endless モードの Codex 応答 → run() が適用する `EndlessOutcome` を生成する（社長指示 §2/§4/§8）。

    判定は純粋（終端 sentinel 検出・分類による honor/reject・no-progress ストリーク・UNRESOLVED 同定）。
    task-list 分類だけは副作用なので `classify()`（run() の classify_tasklist）を注入で受け取り、
    **元の実装と同じ条件でのみ**呼ぶ（終端 sentinel 検出時／pending_kind==next 時。レビュー継続時は呼ばない）。
    run() 側はこの outcome を《表示・exit code・poke・state 遷移》へ適用するだけにする（P2-5）。
    """
    saw_done = hit_stop(texts, all_done_phrases)
    saw_hr = hit_stop(texts, human_required_phrases)
    if saw_done or saw_hr:
        cls = classify()
        term = decide_endless_terminal(saw_done, saw_hr, cls["state"])
        if term == "all_done":
            return EndlessOutcome("all_done", None, None, None, np_state)
        if term == "human_required":
            return EndlessOutcome("human_required", None,
                                  human_required_banner_lines(cls, rounds, exit_code), hr_reason, np_state)
        # term == "reject": 誤 sentinel（分類 READY）。Claude へ送らず Codex に選択を再要求。
        # UNRESOLVED として no-progress を進め、誤 sentinel の連発は 3 回で停止する。
        np2, stop = advance_no_progress(np_state, UNRESOLVED, cls["hash"])
        if stop:
            return EndlessOutcome("no_progress", REJECT_LOG,
                                  no_progress_banner_lines(np2, rounds, exit_code), noprogress_reason, np2)
        return EndlessOutcome("reject_repoke", REJECT_LOG, None, None, np2)
    if pending_kind == "next":
        cls = classify()
        ident = resolve_task_identity("\n".join(texts), cls["ready"])
        log = UNRESOLVED_LOG if ident == UNRESOLVED else None
        np2, stop = advance_no_progress(np_state, ident, cls["hash"])
        if stop:
            return EndlessOutcome("no_progress", log,
                                  no_progress_banner_lines(np2, rounds, exit_code), noprogress_reason, np2)
        return EndlessOutcome("advance_next", log, None, None, np2)
    # pending_kind != next かつ終端 sentinel 無し = レビュー継続（stop-word/gate は I/O なので run() 側）
    return EndlessOutcome("review", None, None, None, np_state)
