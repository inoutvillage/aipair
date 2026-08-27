"""aipair endless-mode task-list classifier + loader.

endless モードの task-list を READY / BLOCKED / ALL_DONE に分類する。
`classify()` は純関数（本文テキスト → 分類結果 or TaskListError）。ファイル I/O・
相対パス解決・exit 2 写像は `resolve_path()` / `load()` / `load_or_exit()` に分離する。

state（社長指示 2026-08-24 / `_reference/new-task.md` §2）:
  READY    = 実行可能な `[ ]` が1件以上 → endless 継続
  BLOCKED  = `[ ]` は0件で `[!]` が1件以上 → 人間対応・外部依存で AI 単独では進行不能
  ALL_DONE = **認識 checkbox が1件以上あり、全件 `[x]`/`[X]`**（`[ ]` も `[!]` も無い）→ 全完了
  （認識 checkbox が **0 件**のファイルは ALL_DONE でなく TaskListError＝設定不備 → exit 2。P1-1）

契約（Codex レビュー relay-id:d2ece9ce〜01f52208 で確定）:
- 認識する checkbox 記法は**厳密に** `[ ]` / `[x]` / `[X]` / `[!]` のみ。
- 認識 checkbox が **0 件**（通常 Markdown の誤指定・空ファイル・見出し/散文のみ）は ALL_DONE にせず
  TaskListError（fail-closed → exit 2）。endless 開始時は checkbox を 1 件以上要求（P1-1・社長指示 2026-08-24）。
- 未知の1文字マーカー（`[?]`・`[-]` 等）は**無視して ALL_DONE にせず** TaskListError（fail-closed）。
- `[!]`（blocked）は**直下の子 `blocker:` 行が必須**。無ければ TaskListError。
- Markdown コードフェンス（``` / ~~~、任意長）内の疑似 checkbox は無視。
- ネストした（インデント付き）項目も対象。
- `hash` は順序付き正規化タプル列 `(indent, state, text, blocker)` から生成（装飾的編集では変わらない）。
"""
import hashlib
import os
import re
import sys

READY = "READY"
BLOCKED = "BLOCKED"
ALL_DONE = "ALL_DONE"

# マーカー1文字 → 内部状態。ここに無い1文字は「未知記法」として fail-closed。
_MARK_STATE = {" ": "open", "x": "done", "X": "done", "!": "blocked"}

# 単一文字マーカーの checkbox 行のみ対象（`- [foo](url)` 等の複数文字括弧は checkbox 扱いしない）。
# 例: `- [ ] task` / `* [x] done` / `  - [!] blocked`。マーカー後は行末 or 空白+本文。
_ITEM = re.compile(r"^(?P<indent>[ \t]*)[-*+] \[(?P<mark>.)\](?:[ \t]+(?P<text>.*))?$")
# コードフェンス開始/終了: ``` 以上のバッククォート or ~~~ 以上のチルダ（任意長・行頭インデント許容）。
_FENCE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})(?P<tail>.*)$")
# blocker 行: 任意インデント＋（任意の list マーカー）＋ `blocker:` ＋非空理由。
_BLOCKER = re.compile(r"^[ \t]*(?:[-*+][ \t]+)?blocker:[ \t]*(?P<why>.*\S.*)$", re.IGNORECASE)


class TaskListError(ValueError):
    """task-list が解析不能（未知記法・blocker 欠落等）。呼び出し側は fail-closed（exit 2）にする。"""


def _indent_width(line):
    return len(re.match(r"^[ \t]*", line).group().expandtabs())


def _content_lines(text):
    """コードフェンス内（開始/終了行を含む）を除いた本文行を返す。

    フェンスは同種（`/~）かつ開始長以上の run で閉じる（GFM 準拠の近似）。閉じない
    フェンスは以降を飲み込む（Markdown の通常挙動）。"""
    out, open_char, open_len = [], None, 0
    for line in text.splitlines():
        m = _FENCE.match(line)
        if open_char is None:
            if m:
                open_char, open_len = m.group("fence")[0], len(m.group("fence"))
            else:
                out.append(line)
        elif (m and m.group("fence")[0] == open_char
              and len(m.group("fence")) >= open_len
              and m.group("tail").strip() == ""):
            # 終了フェンスは同種・開始長以上で、かつ run の後が空白のみ（info string 付き
            # `` ```python `` は開始専用でありフェンス内では終了と見なさない）。
            open_char, open_len = None, 0
        # フェンス内（開始・終了行含む・info string 行含む）は checkbox 対象外なので out に入れない
    return out


def _blocker_for(lines, i):
    """lines[i] の `[!]` 項目の**直下の最初の非空行**が子 `blocker:` 行ならその理由、無ければ None。"""
    item_indent = _indent_width(lines[i])
    for line in lines[i + 1:]:
        if not line.strip():
            continue                      # 空行はまたぐ
        bm = _BLOCKER.match(line)
        if bm and _indent_width(line) > item_indent:
            return bm.group("why").strip()
        return None                       # 最初の非空行が「子の blocker:」でなければ欠落扱い
    return None


def _snapshot_hash(items):
    """順序付き正規化タプル `(indent, state, text, blocker)` の SHA-256。

    checkbox 項目の状態・本文・インデントのみを織り込む（見出し追加・散文編集などの
    装飾的変更ではハッシュが変わらない → no-progress 判定を誤リセットしない）。"""
    norm = "\n".join("%d\x1f%s\x1f%s\x1f%s" % (ind, st, txt, bl or "")
                     for ind, st, _line, txt, bl in items)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def classify(text):
    """task-list 本文を分類して dict を返す。解析不能なら TaskListError（fail-closed）。

    戻り値: {"state", "ready": [行テキスト...], "blocked": [{"item", "blocker"}...], "hash"}
    """
    lines = _content_lines(text)
    items = []                            # (indent, state, verbatim_line, text, blocker)
    for i, line in enumerate(lines):
        m = _ITEM.match(line)
        if not m:
            continue
        state = _MARK_STATE.get(m.group("mark"))
        if state is None:                 # 未知の1文字マーカー → fail-closed
            raise TaskListError(
                "unknown checkbox marker [%s]: %r (recognized: [ ] [x] [X] [!])"
                % (m.group("mark"), line.strip()))
        blocker = None
        if state == "blocked":
            blocker = _blocker_for(lines, i)
            if not blocker:               # blocker: 欠落 → fail-closed
                raise TaskListError(
                    "blocked item [!] without a child 'blocker:' line: %r" % line.strip())
        # verbatim 行（splitlines で改行のみ除去済み・末尾空白は保持）を ready/blocked.item に
        # 使う。banner 表示と Codex への逐語エコー契約が原文を要求するため rstrip しない。
        # no-progress の同一性照合は endless_flow.canonical_task_key を通す（P2-1・案B）ので、
        # ここで空白を保持しても「見えない差」でのエコー取り逃がしは起きない。
        items.append((_indent_width(line), state, line,
                      (m.group("text") or "").strip(), blocker))

    if not items:
        # P1-1（社長指示 2026-08-24）: 認識できる checkbox が 1 件も無いファイル（通常 Markdown の誤指定・
        # 空ファイル・見出し/散文のみ）は ALL_DONE にせず fail-closed（→ load_or_exit で exit 2）。
        # 「読めない＝完了」で誤停止しないのと同じ理由。空を正式許可するなら将来 marker を導入する。
        raise TaskListError(
            "no recognized checkbox (`- [ ]` / `- [x]` / `- [!]`) found — task-list に見えない"
            "（README 等の誤指定・空ファイル・見出し/散文のみ？）。endless は checkbox を 1 件以上要求する")
    ready = [it[2] for it in items if it[1] == "open"]
    blocked = [{"item": it[2], "blocker": it[4]} for it in items if it[1] == "blocked"]
    state = READY if ready else (BLOCKED if blocked else ALL_DONE)
    return {"state": state, "ready": ready, "blocked": blocked, "hash": _snapshot_hash(items)}


# ── loader（I/O・fail-closed）: 相対パスは --dir 基準で解決し、欠損・読取不能・解析不能は
#    ALL_DONE にせず TaskListError（呼び出し側は exit 2）。 ────────────────────────────────
def resolve_path(task_list, base_dir):
    """相対 task-list パスを base_dir（--dir）基準で解決した絶対/連結パスを返す（純粋）。"""
    if os.path.isabs(task_list):
        return task_list
    return os.path.join(base_dir, task_list)


def load(task_list, base_dir):
    """task-list を読み込み classify した結果を返す。失敗は全て TaskListError（fail-closed）。

    欠損・ディレクトリ・読取不能・decode 不能・解析不能を **ALL_DONE にせず** 例外にする
    （「読めない＝完了」で endless を誤停止させない）。呼び出し側は exit 2 に写像する。
    """
    path = resolve_path(task_list, base_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        raise TaskListError("task-list not found: %s (resolved from --dir=%s)" % (path, base_dir))
    except IsADirectoryError:
        raise TaskListError("task-list is a directory, not a file: %s" % path)
    except (OSError, UnicodeDecodeError) as e:
        raise TaskListError("task-list unreadable: %s (%s)" % (path, e))
    return classify(text)          # 解析不能なら classify が TaskListError を投げる


def load_or_exit(task_list, base_dir, emit=None):
    """load() し、失敗時は理由を出して sys.exit(2)（fail-closed の起動エラー）。成功時は分類結果。

    emit は 1 引数の出力関数（既定は stderr）。exit 2 は cli.py の引数エラーと同じ「設定不備で
    黙って進めない」姿勢。"""
    try:
        return load(task_list, base_dir)
    except TaskListError as e:
        (emit or (lambda m: print(m, file=sys.stderr)))(
            "aipair-relay: task-list を読めません（fail-closed・exit 2）: %s" % e)
        sys.exit(2)
