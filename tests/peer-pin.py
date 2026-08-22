#!/usr/bin/env python3
"""Regression tests for pinning `peer` to THIS pair's sessions (peer-log).

The core guarantee of aipair is that `peer` reads the *paired* agent, not merely the
newest session that happens to share the working directory. aipair stamps two env pins at
launch — AIPAIR_CLAUDE_SESSION (Claude's fixed --session-id, which is its log's basename)
and AIPAIR_CODEX_SINCE (the launch epoch) — and peer-log honours them here.

Fixtures live in a temp dir wired in as CLAUDE_PROJECTS / CODEX_SESSIONS; nothing under
~/.claude or ~/.codex is read.
    python3 tests/peer-pin.py
"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


pl = load_module("peerlog_pin_under_test", os.path.join(BIN, "peer-log"))


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aipair-peer-pin.")
        root = self.tmp.name
        pl.CLAUDE_PROJECTS = os.path.join(root, ".claude", "projects")
        pl.CODEX_SESSIONS = os.path.join(root, ".codex", "sessions")
        pl._CODEX_CWD_CACHE.clear()
        pl._CODEX_START_CACHE.clear()
        pl.CODEX_INDEX = pl.CodexIndex()
        pl.CODEX_INDEX.FULL_RESCAN_SECS = 10 ** 9
        # keep the process env clean between tests
        for k in ("AIPAIR_CLAUDE_SESSION", "AIPAIR_CODEX_SINCE"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.tmp.cleanup()

    # -- fixture builders ----------------------------------------------------------
    def claude(self, cwd, session_id, mtime):
        """A Claude .jsonl named <session_id>.jsonl under cwd's project dir."""
        enc = "".join("-" if not ch.isalnum() else ch for ch in cwd)
        d = os.path.join(pl.CLAUDE_PROJECTS, enc)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, session_id + ".jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "summary", "sessionId": session_id}) + "\n")
        os.utime(p, (mtime, mtime))
        return p

    def rollout(self, name, cwd, start_iso, mtime, day=("2026", "08", "21")):
        d = os.path.join(pl.CODEX_SESSIONS, *day)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"rollout-{name}.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": start_iso, "type": "session_meta",
                                 "payload": {"cwd": cwd}}) + "\n")
            fh.write(json.dumps({"type": "event_msg"}) + "\n")
        os.utime(p, (mtime, mtime))
        return p


class ClaudePin(Base):
    CWD = "/x/proj"
    PAIR = "aaaaaaaa-1111-2222-3333-444444444444"
    OTHER = "bbbbbbbb-9999-9999-9999-999999999999"

    def test_no_pin_picks_the_newest(self):
        old = self.claude(self.CWD, self.PAIR, 100)
        new = self.claude(self.CWD, self.OTHER, 900)   # newer
        self.assertEqual(pl.claude_file(self.CWD), new)
        self.assertNotEqual(pl.claude_file(self.CWD), old)

    def test_pin_wins_over_a_newer_unrelated_session(self):
        self.claude(self.CWD, self.PAIR, 100)          # the pair's (older)
        self.claude(self.CWD, self.OTHER, 900)         # a newer unrelated Claude, same cwd
        with mock.patch.dict(os.environ, {"AIPAIR_CLAUDE_SESSION": self.PAIR}):
            got = pl.claude_file(self.CWD)
        self.assertEqual(os.path.basename(got), self.PAIR + ".jsonl",
                         "must read the pinned pair session, not the newest one")

    def test_pin_resolves_regardless_of_cwd(self):
        # the peer's shell may have cd'ed elsewhere; the id is globally unique.
        self.claude(self.CWD, self.PAIR, 100)
        with mock.patch.dict(os.environ, {"AIPAIR_CLAUDE_SESSION": self.PAIR}):
            got = pl.claude_file("/some/other/dir")
        self.assertEqual(os.path.basename(got), self.PAIR + ".jsonl")

    def test_pin_returns_none_until_the_session_exists(self):
        self.claude(self.CWD, self.OTHER, 900)         # only an unrelated session so far
        with mock.patch.dict(os.environ, {"AIPAIR_CLAUDE_SESSION": self.PAIR}):
            self.assertIsNone(pl.claude_file(self.CWD),
                              "never fall back to a different session while pinned")

    def test_a_malformed_pin_fails_closed(self):
        # a set-but-invalid pin must NOT silently fall back to the newest (that would revive
        # the very cross-session mixing the pin exists to prevent) — read nothing instead.
        self.claude(self.CWD, self.OTHER, 900)
        for bad in ("*", "x; echo hi", "../../etc/passwd", "short"):
            with mock.patch.dict(os.environ, {"AIPAIR_CLAUDE_SESSION": bad}):
                self.assertIsNone(pl.claude_file(self.CWD), f"malformed pin {bad!r} must fail closed")

    def test_a_non_canonical_id_with_a_matching_file_is_still_refused(self):
        # only a strict UUID is honoured: a plausible-looking id that has its own .jsonl on
        # disk must not be read just because it passes a loose [alnum-_]{8,} check.
        self.claude(self.CWD, "not-a-real-uuid", 900)
        with mock.patch.dict(os.environ, {"AIPAIR_CLAUDE_SESSION": "not-a-real-uuid"}):
            self.assertIsNone(pl.claude_file(self.CWD), "non-canonical id must fail closed even with a file")


class CodexPin(Base):
    CWD = "/x/proj"

    def seed(self):
        # before-pair (older) / pair (at since) / newer-unrelated (newest by mtime)
        self.before = self.rollout("before", self.CWD, "2026-08-21T00:00:00Z", 100)
        self.pair = self.rollout("pair", self.CWD, "2026-08-21T00:10:00Z", 200)
        self.newer = self.rollout("newer", self.CWD, "2026-08-21T00:20:00Z", 900)
        self.since = epoch("2026-08-21T00:10:00Z")

    def test_newest_is_the_default_without_a_pin(self):
        self.seed()
        self.assertEqual(pl.codex_newest(self.CWD), self.newer)

    def test_since_pins_to_the_pair_not_the_newest(self):
        self.seed()
        self.assertEqual(pl.codex_since(self.CWD, self.since), self.pair,
                         "earliest rollout at/after the launch epoch, never a later unrelated one")

    def test_since_excludes_sessions_started_before_it(self):
        self.seed()
        # a since strictly after the pair leaves only 'newer'
        self.assertEqual(pl.codex_since(self.CWD, epoch("2026-08-21T00:15:00Z")), self.newer)

    def test_since_is_none_until_the_pair_rollout_appears(self):
        # only an older session exists; the pair has not written yet
        self.rollout("old", self.CWD, "2026-08-21T00:00:00Z", 100)
        self.assertIsNone(pl.codex_since(self.CWD, epoch("2026-08-21T00:10:00Z")))

    def test_high_res_since_excludes_a_rollout_started_earlier_in_the_same_second(self):
        # the pair launches at 00:10:00.500; an unrelated Codex opened at 00:10:00.100 —
        # same whole second, but BEFORE the launch. A second-granular stamp would keep it;
        # the high-resolution epoch excludes it, leaving only the pair (.800).
        unrelated = self.rollout("same-sec-before", self.CWD, "2026-08-21T00:10:00.100Z", 150)
        pair = self.rollout("pair", self.CWD, "2026-08-21T00:10:00.800Z", 200)
        since = epoch("2026-08-21T00:10:00.500Z")
        self.assertEqual(pl.codex_since(self.CWD, since), pair)
        self.assertNotEqual(pl.codex_since(self.CWD, since), unrelated)

    def test_a_concurrent_rollout_inside_the_launch_window_is_the_known_residual(self):
        # DOCUMENTED LIMITATION (Codex has no session-id): if an unrelated Codex opens in the
        # SAME cwd AFTER the launch stamp but BEFORE the pair's own rollout, codex_since (earliest
        # at/after `since`) selects it. Time cannot tell them apart; only Claude's --session-id is
        # a hard guarantee. This test pins the behaviour so it stays visible, not hidden.
        since = epoch("2026-08-21T00:10:00.000Z")
        concurrent = self.rollout("concurrent", self.CWD, "2026-08-21T00:10:00.300Z", 150)
        self.rollout("pair", self.CWD, "2026-08-21T00:10:00.800Z", 200)
        self.assertEqual(pl.codex_since(self.CWD, since), concurrent,
                         "known residual: an in-window concurrent Codex is picked (time is not identity)")

    def test_since_stays_put_when_a_newer_unrelated_rollout_appears(self):
        self.seed()
        first = pl.codex_since(self.CWD, self.since)
        # a brand-new unrelated Codex starts in the same cwd, becomes the newest
        newest2 = self.rollout("newest2", self.CWD, "2026-08-21T00:30:00Z", 1500)
        pl.CODEX_INDEX.full_scan()
        self.assertEqual(pl.codex_since(self.CWD, self.since), first,
                         "the pin does not drift to the newer unrelated session")
        self.assertNotEqual(pl.codex_since(self.CWD, self.since), newest2)


class LoadIntegration(Base):
    CWD = "/x/proj"
    PAIR = "cccccccc-1111-2222-3333-444444444444"

    def test_load_claude_honours_the_pin(self):
        self.claude(self.CWD, self.PAIR, 100)
        self.claude(self.CWD, "dddddddd-0000-0000-0000-000000000000", 900)  # newer, unrelated
        with mock.patch.dict(os.environ, {"AIPAIR_CLAUDE_SESSION": self.PAIR}):
            f, _ = pl.load("claude", self.CWD, show_tools=False)
        self.assertEqual(os.path.basename(f), self.PAIR + ".jsonl")

    def test_load_codex_honours_since(self):
        pair = self.rollout("pair", self.CWD, "2026-08-21T00:10:00Z", 200)
        self.rollout("newer", self.CWD, "2026-08-21T00:20:00Z", 900)
        with mock.patch.dict(os.environ, {"AIPAIR_CODEX_SINCE": str(epoch("2026-08-21T00:10:00Z"))}):
            f, _ = pl.load("codex", self.CWD, show_tools=False)
        self.assertEqual(f, pair, "load() pins codex to the pair when AIPAIR_CODEX_SINCE is set")

    def test_load_codex_bad_since_fails_closed(self):
        self.rollout("newer", self.CWD, "2026-08-21T00:20:00Z", 900)
        self.rollout("older", self.CWD, "2026-08-21T00:10:00Z", 200)
        for bad in ("not-a-number", "nan", "inf", "-1", "1e3", "  5", "0x10"):
            with mock.patch.dict(os.environ, {"AIPAIR_CODEX_SINCE": bad}):
                f, _ = pl.load("codex", self.CWD, show_tools=False)
            self.assertIsNone(f, f"AIPAIR_CODEX_SINCE={bad!r} must fail closed, never the newest")


class ProcIdentity(Base):
    """Exact identity: peer-log resolves the pair's Codex by the rollout FILE its process holds
    open (via the recorded @aipair-codex-pane), so it never depends on launch time at all."""
    CWD = "/x/proj"

    def test_ppid_from_stat_handles_comm_with_spaces_and_parens(self):
        self.assertEqual(pl._ppid_from_stat("1234 (co)d ex) S 74009 1 1 0 -1"), 74009)
        self.assertEqual(pl._ppid_from_stat("42 (bash) S 7 0"), 7)
        self.assertIsNone(pl._ppid_from_stat("garbage without paren"))

    def test_resolves_the_pane_codex_open_rollout_not_a_newer_unrelated_one(self):
        ours = self.rollout("ours", self.CWD, "2026-08-21T00:00:00Z", 100)      # the pair's (older)
        self.rollout("theirs", self.CWD, "2026-08-21T00:30:00Z", 900)           # newer, unrelated
        def fake_tmux(*a):
            if a[0] == "display-message" and a[-1] == "#{session_name}": return "aipair-x"
            if a[0] == "show-options": return "%5"
            if a[0] == "display-message": return "1000"                          # pane_pid
            return None
        with mock.patch.object(pl, "_tmux", side_effect=fake_tmux), \
             mock.patch.object(pl, "_descendants", return_value=[1000, 1001]), \
             mock.patch.object(pl, "_open_rollouts", side_effect=lambda pid: [ours] if pid == 1001 else []), \
             mock.patch.dict(os.environ, {"TMUX": "/tmp/sock"}):
            self.assertEqual(pl.codex_via_pane(self.CWD), ours)

    def test_explicit_pane_skips_session_resolution(self):
        # the relay passes its OWN codex pane → codex_via_pane must NOT re-derive the session from
        # the current pane (that would read a DIFFERENT pair's @aipair-codex-pane, P1 2026-08-22).
        ours = self.rollout("ours", self.CWD, "2026-08-21T00:00:00Z", 100)
        calls = []
        def fake_tmux(*a):
            calls.append(a)
            return "2000" if (a[0] == "display-message" and "-t" in a) else None   # pane_pid
        with mock.patch.object(pl, "_tmux", side_effect=fake_tmux), \
             mock.patch.object(pl, "_descendants", return_value=[2000]), \
             mock.patch.object(pl, "_open_rollouts", side_effect=lambda pid: [ours] if pid == 2000 else []):
            self.assertEqual(pl.codex_via_pane(self.CWD, "%7"), ours)
        self.assertFalse(any(a and a[-1] == "#{session_name}" for a in calls),
                         "must not resolve the current session when the pane is explicit")
        self.assertFalse(any("show-options" in a for a in calls),
                         "must not read @aipair-codex-pane when the pane is explicit")

    def test_codex_identity_capable_reflects_pane_resolution(self):
        resolves = lambda *a: "3000" if (a[0] == "display-message" and "-t" in a) else None
        with mock.patch.object(pl, "_tmux", side_effect=resolves):
            self.assertEqual(pl.codex_identity_capable("%1"), os.path.isdir("/proc"))
        with mock.patch.object(pl, "_tmux", return_value=None):
            self.assertFalse(pl.codex_identity_capable("%1"))   # pane pid unresolvable → not capable

    def test_returns_none_without_tmux_or_the_pane_option(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMUX", None)
            self.assertIsNone(pl.codex_via_pane(self.CWD))                       # no TMUX → give up
        def no_opt(*a):
            return "aipair-x" if a[-1] == "#{session_name}" else None            # option unset
        with mock.patch.object(pl, "_tmux", side_effect=no_opt), \
             mock.patch.dict(os.environ, {"TMUX": "/tmp/sock"}):
            self.assertIsNone(pl.codex_via_pane(self.CWD))

    def test_codex_pick_prefers_pane_identity_over_the_launch_time_pin(self):
        ours = self.rollout("ours", self.CWD, "2026-08-21T00:20:00Z", 300)      # what the process holds
        self.rollout("since-earliest", self.CWD, "2026-08-21T00:10:00Z", 200)   # what codex_since would pick
        with mock.patch.object(pl, "codex_via_pane", return_value=ours), \
             mock.patch.dict(os.environ, {"AIPAIR_CODEX_SINCE": "1700000000"}):
            self.assertEqual(pl._codex_pick(self.CWD, None), ours)

    def test_codex_pick_falls_back_to_since_when_the_process_is_not_found(self):
        pair = self.rollout("pair", self.CWD, "2026-08-21T00:10:00Z", 200)
        self.rollout("before", self.CWD, "2026-08-21T00:00:00Z", 100)
        with mock.patch.object(pl, "codex_via_pane", return_value=None), \
             mock.patch.dict(os.environ, {"AIPAIR_CODEX_SINCE": str(epoch("2026-08-21T00:10:00Z"))}):
            self.assertEqual(pl._codex_pick(self.CWD, None), pair)

    def test_shallowest_codex_wins_over_a_nested_one(self):
        # the pair's own codex (shallow) holds `main`; a nested codex-in-codex (deeper) holds a
        # NEWER `nested`. _descendants yields breadth-first, so the shallow one must win — mtime
        # must not drag the pin down to the subprocess.
        main = self.rollout("main", self.CWD, "2026-08-21T00:00:00Z", 100)
        nested = self.rollout("nested", self.CWD, "2026-08-21T00:59:00Z", 9999)   # newer
        def fake_tmux(*a):
            if a[0] == "display-message" and a[-1] == "#{session_name}": return "aipair-x"
            if a[0] == "show-options": return "%5"
            if a[0] == "display-message": return "1000"
            return None
        # BFS: 1001 (pane's codex, shallow) before 1002 (nested, deep)
        opens = {1001: [main], 1002: [nested]}
        with mock.patch.object(pl, "_tmux", side_effect=fake_tmux), \
             mock.patch.object(pl, "_descendants", return_value=[1001, 1002]), \
             mock.patch.object(pl, "_open_rollouts", side_effect=lambda pid: opens.get(pid, [])), \
             mock.patch.dict(os.environ, {"TMUX": "/tmp/sock"}):
            self.assertEqual(pl.codex_via_pane(self.CWD), main,
                             "the shallow (pair) codex wins over a deeper, newer nested one")

    def test_valid_rollout_only_accepts_files_under_codex_sessions(self):
        good = self.rollout("good", self.CWD, "2026-08-21T00:00:00Z", 100)
        self.assertTrue(pl._valid_rollout(good))
        # right name, wrong place (a process holding a look-alike open elsewhere)
        elsewhere = os.path.join(self.tmp.name, "rollout-decoy.jsonl")
        open(elsewhere, "w").close()
        self.assertFalse(pl._valid_rollout(elsewhere), "a rollout-named file outside CODEX_SESSIONS is refused")
        # under CODEX_SESSIONS but not a rollout basename
        notroll = os.path.join(pl.CODEX_SESSIONS, "2026", "08", "21", "notes.jsonl")
        open(notroll, "w").close()
        self.assertFalse(pl._valid_rollout(notroll), "a non-rollout basename is refused")


if __name__ == "__main__":
    unittest.main(verbosity=2)
