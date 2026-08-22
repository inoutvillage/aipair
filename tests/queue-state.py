#!/usr/bin/env python3
"""Fixture tests for aipair-queue's checklist state machine:
- [ ] pending → - [>] running → - [x] done / - [!] needs a human. No git/gh/tmux involved.
    python3 tests/queue-state.py
"""
import importlib.machinery, importlib.util, os, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
loader = importlib.machinery.SourceFileLoader("queue_under_test", os.path.join(BIN, "aipair-queue"))
spec = importlib.util.spec_from_loader("queue_under_test", loader)
queue = importlib.util.module_from_spec(spec)
loader.exec_module(queue)


class QueueFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aipair-queue.")
        self.path = os.path.join(self.tmp.name, "queue.md")
        self.write("# queue\n\n- [x] done already — PR #1 merged\n- [ ] first task\n  - [ ] nested note is not a task\n- [!] 要人間: broken\n- [ ] second task\n- [>] running elsewhere\n")
        queue.warn = lambda *a, **k: None

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self):
        return open(self.path, encoding="utf-8").read()

    def test_next_task_is_the_first_unchecked_top_level_item(self):
        line, task = queue.next_task(self.path)
        self.assertEqual((line, task), ("- [ ] first task", "first task"))
        self.assertEqual(queue.remaining(self.path), 2)

    def test_pending_running_done_lifecycle(self):
        line, task = queue.next_task(self.path)
        self.assertTrue(queue.rewrite_line(self.path, line, f"- [>] {task}"))
        self.assertEqual(queue.next_task(self.path), ("- [ ] second task", "second task"), "running items are skipped")
        self.assertTrue(queue.rewrite_line(self.path, f"- [>] {task}", f"- [x] {task} — PR #7 merged"))
        self.assertIn("- [x] first task — PR #7 merged", self.read())
        self.assertEqual(queue.remaining(self.path), 1)

    def test_held_for_a_human_and_resumed(self):
        line, task = queue.next_task(self.path)
        self.assertTrue(queue.rewrite_line(self.path, line, f"- [!] 要人間: CI red: {task}"))
        self.assertEqual(queue.next_task(self.path)[1], "second task")
        self.assertTrue(queue.rewrite_line(self.path, f"- [!] 要人間: CI red: {task}", f"- [ ] {task}"))
        self.assertEqual(queue.next_task(self.path)[1], "first task", "a human putting it back re-queues it")

    def test_rewrite_survives_concurrent_edits_and_refuses_unknown_lines(self):
        line, task = queue.next_task(self.path)
        self.write("- [ ] inserted above by the user\n" + self.read())      # line numbers shift
        self.assertTrue(queue.rewrite_line(self.path, line, f"- [>] {task}"))
        self.assertEqual(self.read().count(f"- [>] {task}"), 1)
        self.assertFalse(queue.rewrite_line(self.path, "- [ ] never existed", "- [>] x"))
        self.assertTrue(self.read().endswith("\n"))

    def test_missing_or_empty_queue(self):
        self.assertEqual(queue.next_task(os.path.join(self.tmp.name, "nope.md")), (None, None))
        self.assertEqual(queue.remaining(os.path.join(self.tmp.name, "nope.md")), 0)
        self.write("")
        self.assertEqual(queue.next_task(self.path), (None, None))

    def test_task_prompt_carries_the_line_verbatim(self):
        p = queue.task_prompt("add `--foo` flag; it's urgent", 2, 5)
        self.assertIn("add `--foo` flag; it's urgent", p)
        self.assertIn("2/5", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
