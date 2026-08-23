#!/usr/bin/env python3
"""Unit tests for the endless-mode task-list classifier (Phase 1 / `_reference/new-task.md`).

    python3 tests/tasklist.py

分類器は純関数（本文テキスト → 分類結果 or TaskListError）。ここでは READY/BLOCKED/ALL_DONE、
ネスト、`[X]`、未知記法の fail-closed、blocker 必須、コードフェンス無視、hash の安定性/感度を被覆する。
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "bin"))
import aipairlib.tasklist as tl          # noqa: E402


class State(unittest.TestCase):
    def test_ready_when_an_open_item_exists(self):
        r = tl.classify("- [ ] task A\n- [x] task B\n")
        self.assertEqual(r["state"], tl.READY)
        self.assertEqual(r["ready"], ["- [ ] task A"])
        self.assertEqual(r["blocked"], [])

    def test_all_done_when_only_done_items(self):
        r = tl.classify("- [x] a\n- [X] b\n")            # [X] 大文字も完了扱い
        self.assertEqual(r["state"], tl.ALL_DONE)
        self.assertEqual(r["ready"], [])
        self.assertEqual(r["blocked"], [])

    def test_all_done_when_no_checkboxes(self):
        self.assertEqual(tl.classify("# heading\n\nprose only\n")["state"], tl.ALL_DONE)

    def test_blocked_when_only_blocked_remains(self):
        r = tl.classify("- [x] done\n- [!] set GitHub Secrets\n  - blocker: repo admin required\n")
        self.assertEqual(r["state"], tl.BLOCKED)
        self.assertEqual(r["ready"], [])
        self.assertEqual(r["blocked"],
                         [{"item": "- [!] set GitHub Secrets", "blocker": "repo admin required"}])

    def test_ready_wins_over_blocked(self):                # §11 Case 6
        r = tl.classify("- [ ] do A\n- [!] human task\n  - blocker: needs approval\n")
        self.assertEqual(r["state"], tl.READY)
        self.assertEqual(r["ready"], ["- [ ] do A"])
        self.assertEqual(len(r["blocked"]), 1)

    def test_nested_open_item_counts(self):
        r = tl.classify("- [x] parent\n  - [ ] nested open\n")
        self.assertEqual(r["state"], tl.READY)
        self.assertEqual(r["ready"], ["  - [ ] nested open"])

    def test_blocker_may_be_a_plain_indented_line(self):   # `- ` list marker is optional
        r = tl.classify("- [!] X\n    blocker: because reasons\n")
        self.assertEqual(r["blocked"], [{"item": "- [!] X", "blocker": "because reasons"}])

    def test_ready_item_is_verbatim_including_trailing_whitespace(self):
        # 厳密一致タスク ID 契約（Phase 4）のため、行は逐語（末尾空白保持・rstrip しない）。
        r = tl.classify("- [ ] task A  \n")               # 末尾に空白2つ
        self.assertEqual(r["ready"], ["- [ ] task A  "])   # verbatim（rstrip されない）
        b = tl.classify("- [!] Y \n  - blocker: why\n")   # blocked.item も逐語
        self.assertEqual(b["blocked"][0]["item"], "- [!] Y ")


class FailClosed(unittest.TestCase):
    def test_unknown_marker_raises(self):
        for body in ("- [?] mystery\n", "- [-] dash\n", "- [o] circle\n"):
            with self.assertRaises(tl.TaskListError):
                tl.classify(body)

    def test_blocked_without_blocker_raises(self):
        with self.assertRaises(tl.TaskListError):
            tl.classify("- [!] no reason given\n")

    def test_blocker_at_same_indent_is_not_a_child(self):
        with self.assertRaises(tl.TaskListError):
            tl.classify("- [!] X\nblocker: same indent, not a child\n")

    def test_multi_char_bracket_is_not_a_checkbox(self):   # `- [foo](url)` は checkbox 扱いしない
        r = tl.classify("- [link](http://x) see this\n- [ ] real\n")
        self.assertEqual(r["state"], tl.READY)
        self.assertEqual(r["ready"], ["- [ ] real"])       # 未知記法エラーにもならない


class CodeFences(unittest.TestCase):
    def test_pseudo_checkbox_inside_backtick_fence_ignored(self):
        body = "- [x] real done\n```\n- [ ] not a real task\n```\n"
        r = tl.classify(body)
        self.assertEqual(r["state"], tl.ALL_DONE)          # フェンス内の [ ] は READY にしない
        self.assertEqual(r["ready"], [])

    def test_tilde_and_arbitrary_length_fences_ignored(self):
        body = ("~~~\n- [ ] fenced tilde\n~~~\n"
                "````\n- [!] fenced long (no blocker, but ignored)\n````\n"
                "- [x] done\n")
        self.assertEqual(tl.classify(body)["state"], tl.ALL_DONE)

    def test_info_string_fence_still_ignored(self):
        body = "```python\n- [ ] code sample\n```\n- [x] done\n"
        self.assertEqual(tl.classify(body)["state"], tl.ALL_DONE)

    def test_info_string_line_inside_fence_is_not_a_close(self):
        # 開始フェンス内の ``` + info string 行を「終了」と誤認すると、以降のコード例が本文として
        # 解析され未知記法 [?] で TaskListError になっていた（Codex relay-id:c6136219）。
        body = "```\n```python\n- [?] example inside fence\n```\n- [x] done\n"
        r = tl.classify(body)                              # 例外を投げない
        self.assertEqual(r["state"], tl.ALL_DONE)          # フェンス内は全て無視
        self.assertEqual(r["ready"], [])


class SnapshotHash(unittest.TestCase):
    def test_deterministic(self):
        body = "- [ ] task A\n- [x] task B\n"
        self.assertEqual(tl.classify(body)["hash"], tl.classify(body)["hash"])

    def test_state_change_changes_hash(self):
        a = tl.classify("- [ ] task A\n- [x] task B\n")["hash"]
        b = tl.classify("- [x] task A\n- [x] task B\n")["hash"]   # A completed
        self.assertNotEqual(a, b)

    def test_decorative_edits_do_not_change_hash(self):
        a = tl.classify("- [ ] task A\n")["hash"]
        b = tl.classify("# new heading\n\n- [ ] task A\n\nmore prose\n")["hash"]
        self.assertEqual(a, b)      # 非 checkbox の装飾編集では snapshot が変わらない


class Loader(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for root, _dirs, files in os.walk(self.dir, topdown=False):
            for f in files:
                os.remove(os.path.join(root, f))
            os.rmdir(root)

    def _write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_resolve_relative_against_base_dir(self):
        self.assertEqual(tl.resolve_path("tasks/todo.md", "/base"),
                         os.path.join("/base", "tasks/todo.md"))

    def test_resolve_absolute_is_unchanged(self):
        self.assertEqual(tl.resolve_path("/abs/todo.md", "/base"), "/abs/todo.md")

    def test_load_classifies_a_relative_path(self):
        self._write("todo.md", "- [ ] task A\n- [x] task B\n")
        r = tl.load("todo.md", self.dir)          # relative, resolved against base_dir
        self.assertEqual(r["state"], tl.READY)
        self.assertEqual(r["ready"], ["- [ ] task A"])

    def test_missing_file_is_fail_closed_not_all_done(self):
        with self.assertRaises(tl.TaskListError):
            tl.load("does-not-exist.md", self.dir)

    def test_directory_is_fail_closed(self):
        os.mkdir(os.path.join(self.dir, "adir"))
        with self.assertRaises(tl.TaskListError):
            tl.load("adir", self.dir)

    def test_unparseable_content_propagates_fail_closed(self):
        self._write("bad.md", "- [?] unknown marker\n")
        with self.assertRaises(tl.TaskListError):
            tl.load("bad.md", self.dir)

    def test_load_or_exit_returns_result_on_success(self):
        self._write("todo.md", "- [x] done\n")
        self.assertEqual(tl.load_or_exit("todo.md", self.dir)["state"], tl.ALL_DONE)

    def test_load_or_exit_exits_2_and_emits_on_failure(self):
        emitted = []
        with self.assertRaises(SystemExit) as cm:
            tl.load_or_exit("missing.md", self.dir, emit=emitted.append)
        self.assertEqual(cm.exception.code, 2)     # fail-closed startup error
        self.assertTrue(emitted and "fail-closed" in emitted[0])


if __name__ == "__main__":
    unittest.main()
