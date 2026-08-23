#!/usr/bin/env python3
"""Unit tests for the endless-mode task-list classifier (Phase 1 / `_reference/new-task.md`).

    python3 tests/tasklist.py

分類器は純関数（本文テキスト → 分類結果 or TaskListError）。ここでは READY/BLOCKED/ALL_DONE、
ネスト、`[X]`、未知記法の fail-closed、blocker 必須、コードフェンス無視、hash の安定性/感度を被覆する。
"""
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
