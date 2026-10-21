#!/usr/bin/env python3
"""Regression tests for picking the conversation `aipair` resumes (aipairlib/resume.py).

"Latest" = the newest (by last update) interactive conversation of the directory that no running
process holds. Fixtures live in a temp dir wired in as CLAUDE_PROJECTS / CLAUDE_SESSIONS /
CODEX_SESSIONS; nothing under ~/.claude or ~/.codex is read.
    python3 tests/resume-latest.py
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import aipairlib.peerlog as pl   # noqa: E402
from aipairlib import resume     # noqa: E402

U = ["%08x-0000-4000-8000-%012x" % (i, i) for i in range(1, 9)]   # valid UUIDs


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aipair-resume.")
        root = self.tmp.name
        self.proj = os.path.join(root, "proj")
        self.other = os.path.join(root, "other")
        os.makedirs(self.proj)
        os.makedirs(self.other)
        self.cp = os.path.join(root, "claude-projects")
        self.cs = os.path.join(root, "claude-sessions")
        self.xs = os.path.join(root, "codex-sessions")
        for d in (self.cp, self.cs, os.path.join(self.xs, "2026", "10", "09")):
            os.makedirs(d)
        patches = [mock.patch.object(pl, "CLAUDE_PROJECTS", self.cp),
                   mock.patch.object(pl, "CLAUDE_SESSIONS", self.cs),
                   mock.patch.object(pl, "CODEX_SESSIONS", self.xs)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        pl._CODEX_START_CACHE.clear()
        pl._CODEX_CWD_CACHE.clear()
        self.addCleanup(self.tmp.cleanup)

    def claude(self, sid, cwd, mtime, entry="cli", folder="-proj"):
        d = os.path.join(self.cp, folder)
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, sid + ".jsonl")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "mode"}) + "\n")
            rec = {"type": "user", "cwd": cwd, "sessionId": sid}
            if entry is not None:
                rec["entrypoint"] = entry
            fh.write(json.dumps(rec) + "\n")
        os.utime(f, (mtime, mtime))
        return f

    def codex(self, sid, cwd, mtime, source="cli", ts="2026-10-09T01:00:00.000Z"):
        f = os.path.join(self.xs, "2026", "10", "09", "rollout-2026-10-09T00-00-00-%s.jsonl" % sid)
        meta = {"timestamp": ts, "type": "session_meta",
                "payload": {"id": sid, "cwd": cwd, "source": source, "timestamp": ts}}
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta) + "\n")
        os.utime(f, (mtime, mtime))
        return f


class ClaudeLatest(Base):
    def test_newest_interactive_of_this_dir(self):
        self.claude(U[0], self.proj, 1000)
        self.claude(U[1], self.proj, 2000)
        self.claude(U[2], self.proj, 3000, entry="sdk-cli")      # `claude -p`
        self.claude(U[3], self.proj, 3500, entry="sdk-ts")       # Agent SDK
        self.claude(U[4], self.other, 4000, folder="-proj")      # same folder name, another dir
        self.assertEqual(resume.latest_claude(self.proj), (U[1], 2000))

    def test_record_without_entrypoint_counts(self):
        self.claude(U[0], self.proj, 1000, entry=None)
        self.assertEqual(resume.latest_claude(self.proj)[0], U[0])

    def test_non_uuid_file_and_missing_cwd_are_skipped(self):
        self.claude(U[0], self.proj, 1000)
        d = os.path.join(self.cp, "-proj")
        with open(os.path.join(d, "notes.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "cwd": self.proj}) + "\n")
        bare = os.path.join(d, U[5] + ".jsonl")
        with open(bare, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "summary"}) + "\n")
        os.utime(bare, (5000, 5000))
        self.assertEqual(resume.latest_claude(self.proj)[0], U[0])

    def test_same_dir_by_another_spelling(self):
        self.claude(U[0], self.proj + "/", 1000)                 # trailing slash
        self.assertEqual(resume.latest_claude(self.proj)[0], U[0])

    def test_a_conversation_in_use_is_skipped(self):
        """A running claude records its session in <pid>.json (pid + procStart must match the live process)."""
        if not pl.proc_available():
            self.skipTest("no /proc")
        self.claude(U[0], self.proj, 1000)
        self.claude(U[1], self.proj, 2000)
        pid = os.getpid()
        with open(os.path.join(self.cs, "%d.json" % pid), "w", encoding="utf-8") as fh:
            json.dump({"pid": pid, "procStart": str(pl._proc_stat(pid)[1]), "sessionId": U[1]}, fh)
        self.assertEqual(resume.latest_claude(self.proj)[0], U[0], "the live one is left alone")

    def test_a_stale_record_does_not_hide_a_conversation(self):
        """<pid>.json of a process that is gone (wrong procStart) must not exclude anything."""
        self.claude(U[1], self.proj, 2000)
        pid = os.getpid()
        with open(os.path.join(self.cs, "%d.json" % pid), "w", encoding="utf-8") as fh:
            json.dump({"pid": pid, "procStart": "1", "sessionId": U[1]}, fh)
        self.assertEqual(resume.latest_claude(self.proj)[0], U[1])

    def test_none(self):
        self.claude(U[0], self.other, 1000)
        self.assertIsNone(resume.latest_claude(self.proj))


class CodexLatest(Base):
    def test_newest_interactive_of_this_dir(self):
        self.codex(U[0], self.proj, 1000)
        self.codex(U[1], self.proj, 2000, ts="2026-10-09T02:30:00.250Z")
        self.codex(U[2], self.proj, 3000, source="exec")         # `codex exec`
        self.codex(U[3], self.other, 4000)
        sid, start, mtime = resume.latest_codex(self.proj)
        self.assertEqual((sid, mtime), (U[1], 2000))
        self.assertEqual(start, datetime.fromisoformat("2026-10-09T02:30:00.250+00:00").timestamp())
        self.assertEqual(start, pl.codex_start(os.path.join(
            self.xs, "2026", "10", "09", "rollout-2026-10-09T00-00-00-%s.jsonl" % U[1])),
            "the same epoch peerlog.codex_since compares, so the non-/proc pick lands on it")

    def test_other_sources_count(self):
        self.codex(U[0], self.proj, 1000, source="vscode")
        self.assertEqual(resume.latest_codex(self.proj)[0], U[0])

    def test_bad_meta_is_skipped(self):
        self.codex(U[0], self.proj, 1000)
        bad = os.path.join(self.xs, "2026", "10", "09", "rollout-2026-10-09T00-00-01-x.jsonl")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "session_meta", "payload": {"id": "not-a-uuid", "cwd": self.proj}}) + "\n")
        os.utime(bad, (5000, 5000))
        self.assertEqual(resume.latest_codex(self.proj)[0], U[0])

    def test_a_session_in_use_is_skipped(self):
        """A rollout some running codex holds open is left alone."""
        if not pl.proc_available():
            self.skipTest("no /proc")
        self.codex(U[0], self.proj, 1000)
        f = self.codex(U[1], self.proj, 2000)
        with open(f, "r", encoding="utf-8"):                     # this process "is" a codex holding it
            with mock.patch.object(pl, "_proc_comm", lambda pid: "codex" if pid == os.getpid() else None):
                self.assertEqual(resume.latest_codex(self.proj)[0], U[0])
            with mock.patch.object(pl, "_proc_comm", lambda pid: "python3"):
                self.assertEqual(resume.latest_codex(self.proj)[0], U[1], "only a codex process counts")

    def test_none(self):
        self.codex(U[0], self.proj, 1000, source="exec")
        self.assertIsNone(resume.latest_codex(self.proj))


class Main(Base):
    def run_main(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = resume.main(list(argv))
        return rc, buf.getvalue()

    def test_output_lines(self):
        self.claude(U[0], self.proj, 1000)
        self.codex(U[1], self.proj, 2000, ts="2026-10-09T02:30:00.250Z")
        rc, out = self.run_main("claude", self.proj)
        self.assertEqual(rc, 0)
        sid, start, label = out.rstrip("\n").split("\t")
        self.assertEqual((sid, start), (U[0], "0"))
        self.assertRegex(label, r"^\d\d/\d\d \d\d:\d\d$")
        rc, out = self.run_main("codex", self.proj)
        sid, start, _ = out.rstrip("\n").split("\t")
        self.assertEqual(sid, U[1])
        self.assertRegex(start, r"^[0-9]+(\.[0-9]+)?$", "bin/aipair accepts only this form for AIPAIR_CODEX_SINCE")
        self.assertEqual(float(start), datetime.fromisoformat("2026-10-09T02:30:00.250+00:00").timestamp())

    def test_nothing_found_prints_nothing(self):
        self.assertEqual(self.run_main("claude", self.proj), (0, ""))
        self.assertEqual(self.run_main("codex", self.proj), (0, ""))

    def test_usage(self):
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(self.run_main("gpt", self.proj)[0], 2)
            self.assertEqual(self.run_main("claude")[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
