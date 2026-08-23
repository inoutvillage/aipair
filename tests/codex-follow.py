#!/usr/bin/env python3
"""Regression tests for Codex rollout discovery / following (peer-log + aipair-relay).

Fixtures live in a temp dir wired in as CODEX_SESSIONS; nothing under ~/.codex is read.
    python3 tests/codex-follow.py
"""
import builtins, glob, importlib.machinery, importlib.util, json, os, shutil, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


sys.path.insert(0, BIN)
import aipairlib.peerlog as peerlog
import aipairlib.relay as relay


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aipair-codex-follow.")
        root = self.tmp.name
        self.sessions = os.path.join(root, "sessions")
        self.A = os.path.join(root, "proj", "a", "api"); self.B = os.path.join(root, "proj", "b", "api")
        os.makedirs(self.A); os.makedirs(self.B)
        for mod in (peerlog, relay.peerlog):
            mod.CODEX_SESSIONS = self.sessions
            mod._CODEX_CWD_CACHE.clear()
            mod.CODEX_INDEX = mod.CodexIndex()            # fresh inventory per test
            mod.CODEX_INDEX.FULL_RESCAN_SECS = 10 ** 9     # only explicit full rescans below
            mod.codex_via_pane = lambda cwd, pane=None: None   # default: no /proc identity → fallback path
            mod.codex_identity_capable = lambda pane=None: False
        # 抽出後、ログ locking 関数は log_lock 側の dim を使うので、そちらも黙らせる（Codex 指摘:
        # relay.dim だけだと「codex rollout ローテート検知」等の ANSI ログがテスト出力に漏れる）。
        relay.dim = relay.log_lock.dim = lambda *a, **k: None   # silence relay + log_lock log lines
        relay.log_lock._CODEX_SINCE_EPOCH = None; relay.log_lock._CODEX_SINCE_BAD = False   # reset module globals per test
        self.t0 = 1_700_000_000
        # A-old (t+1) / B-new (t+3, globally newest) / A-mid (t+2)
        self.a_old = self.rollout("a-old", self.A, 1)
        self.b_new = self.rollout("b-new", self.B, 3)
        self.a_mid = self.rollout("a-mid", self.A, 2)
        self.freeze_dirs()

    def freeze_dirs(self, t=10):
        """Give every directory an old mtime, so that only a later change moves it."""
        for d, subs, _files in os.walk(self.sessions):
            os.utime(d, (self.t0 + t, self.t0 + t))

    def tearDown(self):
        self.tmp.cleanup()

    def rollout(self, name, cwd, t, first=None, extra_lines=1, day=("2026", "08", "21")):
        d = os.path.join(self.sessions, *day); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"rollout-{name}.jsonl")
        if first is None:
            first = json.dumps({"timestamp": "2026-08-21T00:00:00Z", "type": "session_meta",
                                "payload": {"cwd": cwd}}) + "\n"
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(first)
            for _ in range(extra_lines):
                fh.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count"}}) + "\n")
        os.utime(p, (self.t0 + t, self.t0 + t))
        return p


class CodexNewest(Fixture):
    def test_newest_per_cwd(self):
        self.assertEqual(peerlog.codex_newest(self.A), self.a_mid)
        self.assertEqual(peerlog.codex_newest(self.B), self.b_new)
        self.assertIsNone(peerlog.codex_newest(os.path.join(self.tmp.name, "proj")))

    def test_cwd_spelling_is_normalized(self):
        self.assertEqual(peerlog.codex_newest(self.A + "/"), self.a_mid)

    def test_newer_than_filters(self):
        self.assertEqual(peerlog.codex_newest(self.A, newer_than=self.t0 + 1), self.a_mid)
        self.assertIsNone(peerlog.codex_newest(self.A, newer_than=self.t0 + 2))

    def test_limit_caps_the_cold_scan(self):
        # with limit=1 only the globally newest file (B) is examined → no hit for A
        self.assertIsNone(peerlog.codex_newest(self.A, limit=1))
        self.assertEqual(peerlog.codex_newest(self.A, limit=2), self.a_mid)

    def test_partial_first_line_is_retried_not_cached(self):
        p = self.rollout("a-partial", self.A, 5, first='{"type":"session_meta","payload":{"cwd":"' + self.A, extra_lines=0)
        self.assertEqual(peerlog.codex_newest(self.A), self.a_mid, "half-written meta must not match")
        self.assertNotIn(p, peerlog._CODEX_CWD_CACHE)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write('"}}\n')
        os.utime(p, (self.t0 + 5, self.t0 + 5))
        self.assertEqual(peerlog.codex_newest(self.A), p, "completed meta is picked up")

    def test_malformed_first_line_is_ignored(self):
        p = self.rollout("junk", self.A, 9, first="not json\n")
        self.assertEqual(peerlog.codex_newest(self.A), self.a_mid)
        self.assertEqual(peerlog._CODEX_CWD_CACHE.get(p), "")

    def test_first_line_is_opened_once_per_process(self):
        real_open = builtins.open
        with mock.patch("builtins.open", side_effect=real_open) as opened:
            peerlog.codex_newest(self.A)
            first = opened.call_count
            peerlog.codex_newest(self.A); peerlog.codex_newest(self.B); peerlog.codex_follow(self.A, self.a_old)
            self.assertGreater(first, 0)
            self.assertEqual(opened.call_count, first, "re-scans must not reopen cached rollouts")


class CodexFollow(Fixture):
    def test_follows_to_newer_rollout_of_same_cwd_not_the_globally_newest(self):
        self.assertEqual(peerlog.codex_follow(self.A, self.a_old), self.a_mid)

    def test_stays_put_while_a_foreign_rollout_is_newer(self):
        self.assertEqual(peerlog.codex_follow(self.A, self.a_mid), self.a_mid)

    def test_switches_when_our_cwd_gets_a_new_rollout(self):
        a_new = self.rollout("a-new", self.A, 4)
        self.assertEqual(peerlog.codex_follow(self.A, self.a_mid), a_new)

    def test_starts_from_scratch_without_or_with_a_vanished_current(self):
        self.assertEqual(peerlog.codex_follow(self.A, None), self.a_mid)
        self.assertEqual(peerlog.codex_follow(self.A, os.path.join(self.sessions, "gone.jsonl")), self.a_mid)

    def test_load_follows_on_from_current(self):
        f, _ = peerlog.load("codex", self.A, False, current=self.a_old)
        self.assertEqual(f, self.a_mid)


class IndexScan(Fixture):
    """The steady-state poll must not depend on the size of the archive."""
    N_FOREIGN = 300

    def setUp(self):
        super().setUp()
        for i in range(self.N_FOREIGN):                          # a long history of someone else's sessions
            self.rollout(f"b-{i:04d}", self.B, 10 + i * 0.001, day=("2026", "07", f"{1 + i % 28:02d}"))
        self.freeze_dirs()

    def test_second_poll_does_not_walk_the_archive(self):
        self.assertEqual(peerlog.codex_newest(self.A), self.a_mid)          # cold: one full scan
        self.assertEqual(peerlog.CODEX_INDEX.counts["full_scans"], 1)
        real_glob, real_mtime, real_ls = glob.glob, os.path.getmtime, os.listdir
        with mock.patch.object(glob, "glob", side_effect=real_glob) as g, \
             mock.patch.object(os.path, "getmtime", side_effect=real_mtime) as st, \
             mock.patch.object(os, "listdir", side_effect=real_ls) as ls:
            self.assertEqual(peerlog.codex_follow(self.A, self.a_mid), self.a_mid)
            self.assertEqual(g.call_count, 0, "no glob on a quiet poll")
            self.assertEqual(ls.call_count, 0, "no directory listing on a quiet poll")
            budget = len(peerlog.CODEX_INDEX.dirs) + 2 + 1           # watched dirs + A's two files + `current`
            self.assertLessEqual(st.call_count, budget, "stats must not scale with the archive")
        self.assertEqual(peerlog.CODEX_INDEX.counts["full_scans"], 1)

    def test_quiet_follow_stats_only_watched_dirs_and_our_files(self):
        peerlog.codex_newest(self.A)
        real_mtime = os.path.getmtime
        with mock.patch.object(os.path, "getmtime", side_effect=real_mtime) as st:
            peerlog.codex_follow(self.A, self.a_mid)
            self.assertLessEqual(st.call_count, len(peerlog.CODEX_INDEX.dirs) + 2 + 1)   # watched dirs + A's 2 files + current

    def test_busy_project_polls_only_its_most_recent_files(self):
        peerlog.codex_newest(self.B)                                  # B has 301 rollouts
        real_mtime = os.path.getmtime
        with mock.patch.object(os.path, "getmtime", side_effect=real_mtime) as st:
            peerlog.codex_newest(self.B)
            self.assertLessEqual(st.call_count, len(peerlog.CODEX_INDEX.dirs) + peerlog.CodexIndex.FILES_WATCHED)

    def test_resumed_old_session_is_picked_up_at_the_full_rescan(self):
        newest = peerlog.codex_newest(self.B)
        oldest = min(peerlog.CODEX_INDEX.by_cwd[peerlog._norm(self.B)], key=os.path.getmtime)
        with open(oldest, "a", encoding="utf-8") as fh:               # `codex resume` appends to an old rollout
            fh.write("{}\n")
        os.utime(oldest, (self.t0 + 99, self.t0 + 99))
        self.assertEqual(peerlog.codex_newest(self.B), newest, "beyond FILES_WATCHED the stale mtime is used")
        peerlog.CODEX_INDEX.last_full = -1e9
        self.assertEqual(peerlog.codex_newest(self.B), oldest, "…until the next full walk")

    def test_new_rollout_in_the_newest_day_dir_is_seen_without_a_full_scan(self):
        self.assertEqual(peerlog.codex_newest(self.A), self.a_mid)
        a_new = self.rollout("a-new", self.A, 4)                  # creating it bumps 2026/08/21's mtime
        self.assertEqual(peerlog.codex_follow(self.A, self.a_mid), a_new)
        self.assertEqual(peerlog.CODEX_INDEX.counts["full_scans"], 1)

    def test_new_day_dir_is_seen_via_its_month_dir(self):
        self.assertEqual(peerlog.codex_newest(self.A), self.a_mid)
        a_new = self.rollout("a-next-day", self.A, 4, day=("2026", "08", "22"))
        self.assertEqual(peerlog.codex_follow(self.A, self.a_mid), a_new)
        self.assertEqual(peerlog.CODEX_INDEX.counts["full_scans"], 1)

    def test_rollout_in_an_old_unwatched_dir_waits_for_the_full_rescan(self):
        self.assertEqual(peerlog.codex_newest(self.A), self.a_mid)
        odd = self.rollout("a-odd", self.A, 6, day=("2026", "01", "01"))   # not where new sessions normally land
        self.assertEqual(peerlog.codex_follow(self.A, self.a_mid), self.a_mid, "old dirs are not polled")
        peerlog.CODEX_INDEX.last_full = -1e9                                  # safety-net rescan due
        self.assertEqual(peerlog.codex_follow(self.A, self.a_mid), odd)
        self.assertEqual(peerlog.CODEX_INDEX.counts["full_scans"], 2)

    def test_full_rescan_forgets_vanished_files(self):
        self.assertEqual(peerlog.codex_newest(self.A), self.a_mid)
        os.remove(self.a_mid)
        self.assertEqual(peerlog.codex_newest(self.A), self.a_old, "a vanished file is skipped at once")
        peerlog.CODEX_INDEX.last_full = -1e9
        peerlog.codex_newest(self.A)
        self.assertNotIn(self.a_mid, peerlog.CODEX_INDEX.files)


class _RootBase(unittest.TestCase):
    """Fixture without rollouts: the sessions root itself is under test (no test methods here)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aipair-codex-root.")
        self.sessions = os.path.join(self.tmp.name, "sessions")            # not created here
        self.A = os.path.join(self.tmp.name, "proj"); os.makedirs(self.A)
        peerlog.CODEX_SESSIONS = self.sessions
        peerlog._CODEX_CWD_CACHE.clear()
        peerlog.CODEX_INDEX = peerlog.CodexIndex()
        peerlog.CODEX_INDEX.FULL_RESCAN_SECS = 10 ** 9

    def tearDown(self):
        self.tmp.cleanup()

    def rollout(self, name, t, day=("2026", "08", "21")):
        d = os.path.join(self.sessions, *day); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"rollout-{name}.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "session_meta", "payload": {"cwd": self.A}}) + "\n")
        os.utime(p, (1_700_000_000 + t,) * 2)
        return p

    def full_scans(self):
        return peerlog.CODEX_INDEX.counts["full_scans"]


class RootLifecycle(_RootBase):
    """The sessions root may not exist yet (fresh machine), may be empty, or may be removed
    and recreated; none of that may stall detection until the safety-net rescan."""

    def test_root_created_after_the_first_scan_is_seen_on_the_next_poll(self):
        self.assertIsNone(peerlog.codex_newest(self.A))
        self.assertEqual(self.full_scans(), 1)
        p = self.rollout("first", 1)                                        # creates YYYY/MM/DD and the file
        self.assertEqual(peerlog.codex_newest(self.A), p)
        self.assertEqual(self.full_scans(), 1, "no full rescan needed")

    def test_empty_root_populated_later(self):
        os.makedirs(self.sessions)
        self.assertIsNone(peerlog.codex_newest(self.A))
        p = self.rollout("first", 1)
        self.assertEqual(peerlog.codex_follow(self.A, None), p)
        self.assertEqual(self.full_scans(), 1)

    def test_root_removed_and_recreated(self):
        p1 = self.rollout("first", 1)
        self.assertEqual(peerlog.codex_newest(self.A), p1)
        shutil.rmtree(self.sessions)
        self.assertIsNone(peerlog.codex_newest(self.A), "vanished files are skipped")
        self.assertEqual(self.full_scans(), 2, "a vanished watched dir rebuilds the index at once")
        p2 = self.rollout("second", 2)
        self.assertEqual(peerlog.codex_follow(self.A, p1), p2)
        self.assertEqual(self.full_scans(), 2, "…and the recreation needs no further rescan")


class StaleEntries(_RootBase):
    """Deleted rollouts — by pruning the archive or removing the root — must never be returned."""
    N = peerlog.CodexIndex.FILES_WATCHED + 5

    def many(self):
        return [self.rollout(f"r-{i:02d}", i) for i in range(self.N)]

    def test_root_removed_with_more_rollouts_than_are_watched(self):
        files = self.many()
        self.assertEqual(peerlog.codex_newest(self.A), files[-1])
        shutil.rmtree(self.sessions)
        self.assertIsNone(peerlog.codex_newest(self.A), "no stale path from the unwatched tail")
        self.assertEqual(peerlog.load("codex", self.A, False), (None, []), "load() must not raise")
        again = self.rollout("again", 99)
        self.assertEqual(peerlog.codex_follow(self.A, files[-1]), again, "recreation is still detected")

    def test_vanished_tail_candidate_is_dropped_and_the_next_one_verified(self):
        files = self.many()
        peerlog.codex_newest(self.A)
        os.remove(files[4])                                  # newest of the unwatched tail (files[5:] are the 20 watched)
        self.assertEqual(peerlog.codex_newest(self.A, exclude=set(files[5:])), files[3])
        self.assertNotIn(files[4], peerlog.CODEX_INDEX.files)

    def test_deleted_file_in_a_watched_day_dir_is_pruned_when_relisted(self):
        files = self.many()
        peerlog.codex_newest(self.A)
        os.remove(files[3])
        newer = self.rollout("new", 100)                     # bumps the day dir → re-list → prune
        self.assertEqual(peerlog.codex_newest(self.A), newer)
        self.assertNotIn(files[3], peerlog.CODEX_INDEX.files)
        self.assertNotIn(files[3], peerlog.CODEX_INDEX.by_cwd[peerlog._norm(self.A)])

    def test_load_survives_a_file_vanishing_after_selection(self):
        files = self.many()
        peerlog.codex_newest(self.A)
        real_parse = peerlog.parse_codex
        def parse_then_gone(path, show_tools):
            os.remove(path)
            return real_parse(path, show_tools)
        with mock.patch.object(peerlog, "parse_codex", side_effect=parse_then_gone):
            self.assertEqual(peerlog.load("codex", self.A, False), (None, []))
        self.assertEqual(peerlog.codex_newest(self.A), files[-2])


class DiscoveryRaces(Fixture):
    """Files may disappear between the checks that locate them and the read; never raise."""

    def test_current_vanishing_right_before_its_stat_falls_back_to_the_next_rollout(self):
        # codex_follow no longer pre-checks exists(): the only window is the stat itself.
        peerlog.codex_newest(self.A)
        real_mtime = os.path.getmtime
        def mtime_gone(path):
            if path == self.a_mid and os.path.exists(path):
                os.remove(path)                              # deleted right before its mtime is read
            return real_mtime(path)
        with mock.patch.object(os.path, "getmtime", side_effect=mtime_gone):
            self.assertEqual(peerlog.codex_follow(self.A, self.a_mid), self.a_old)
        self.assertFalse(os.path.exists(self.a_mid))
        self.assertNotIn(self.a_mid, peerlog.CODEX_INDEX.files)

    def test_load_survives_current_vanishing_during_discovery(self):
        peerlog.codex_newest(self.A)
        real_mtime = os.path.getmtime
        def mtime_gone(path):
            if path == self.a_mid and os.path.exists(path):
                os.remove(path)
            return real_mtime(path)
        with mock.patch.object(os.path, "getmtime", side_effect=mtime_gone):
            f, entries = peerlog.load("codex", self.A, False, current=self.a_mid)
        self.assertEqual(f, self.a_old)

    def test_load_claude_survives_a_file_vanishing_between_glob_and_stat(self):
        proj = os.path.join(self.tmp.name, "claude-projects")
        d = os.path.join(proj, peerlog.re.sub(r"[^A-Za-z0-9]", "-", self.A)); os.makedirs(d)
        only = os.path.join(d, "s1.jsonl"); open(only, "w").close()
        peerlog.CLAUDE_PROJECTS = proj
        real_mtime = os.path.getmtime
        def mtime_gone(path):
            if path == only:
                os.remove(path)
            return real_mtime(path)
        with mock.patch.object(os.path, "getmtime", side_effect=mtime_gone):
            self.assertEqual(peerlog.load("claude", self.A, False), (None, []))


class RelayLock(Fixture):
    def test_refresh_codex_lock_is_not_fooled_by_a_busier_neighbour(self):
        # the exact 2026-08-21 bug: B is the globally newest file, yet A has a newer session than tracked
        self.assertEqual(relay.refresh_codex_lock(self.a_old, self.A, "%0"), self.a_mid)
        self.assertEqual(relay.refresh_codex_lock(self.a_mid, self.A, "%0"), self.a_mid)
        a_new = self.rollout("a-new", self.A, 4)
        self.assertEqual(relay.refresh_codex_lock(self.a_mid, self.A, "%0"), a_new)

    def test_lock_holds_none_when_identity_capable_but_unresolved(self):
        # identity is the mechanism (capable) but momentarily unresolved → wait, NEVER fall to the
        # mtime heuristic (which could mis-pin a same-cwd Codex from the very first lock).
        with mock.patch.object(relay.peerlog, "codex_identity_capable", return_value=True), \
             mock.patch.object(relay.peerlog, "codex_newest", return_value="MTIME") as cn:
            self.assertIsNone(relay.lock_codex(self.A, set(), "%0"))
            self.assertIsNone(relay.refresh_codex_lock(None, self.A, "%0"))
            cn.assert_not_called()

    def test_fallback_uses_codex_since_when_the_launch_epoch_is_known(self):
        # non-/proc env (capable False) → relay uses peer's EXACT picker (codex_since on the
        # launch epoch), not codex_newest — so peer and relay agree on macOS.
        with mock.patch.object(relay.log_lock, "_CODEX_SINCE_EPOCH", 1_700_000_000.0), \
             mock.patch.object(relay.peerlog, "codex_since", return_value="SINCE_PICK") as cs, \
             mock.patch.object(relay.peerlog, "codex_newest", return_value="NEWEST"), \
             mock.patch.object(relay.peerlog, "codex_follow", return_value="FOLLOW"):
            self.assertEqual(relay.lock_codex(self.A, set(), "%0"), "SINCE_PICK")
            self.assertEqual(relay.refresh_codex_lock(self.a_old, self.A, "%0"), "SINCE_PICK")
            cs.assert_called()

    def test_read_codex_since_validates_like_peer_log(self):
        with mock.patch.object(relay.tmuxlib, "tmux") as t:
            def setopt(v):
                t.return_value.stdout = v + "\n"
                relay.read_codex_since("aipair-x")
            setopt("1700000000.5")           # valid
            self.assertEqual(relay.log_lock._CODEX_SINCE_EPOCH, 1700000000.5)
            self.assertFalse(relay.log_lock._CODEX_SINCE_BAD)
            setopt("")                       # genuinely unset (legacy) → not BAD
            self.assertIsNone(relay.log_lock._CODEX_SINCE_EPOCH)
            self.assertFalse(relay.log_lock._CODEX_SINCE_BAD)
            for bad in ("nan", "inf", "-1", "1e3", "junk", "0x10"):   # same set peer-log rejects
                setopt(bad)
                self.assertTrue(relay.log_lock._CODEX_SINCE_BAD, f"{bad!r} must be flagged present-but-invalid")
                self.assertIsNone(relay.log_lock._CODEX_SINCE_EPOCH)

    def test_fallback_fails_closed_on_a_present_but_invalid_since(self):
        # a corrupt @aipair-codex-since must NOT masquerade as legacy → never the mtime heuristic.
        with mock.patch.object(relay.log_lock, "_CODEX_SINCE_BAD", True), \
             mock.patch.object(relay.log_lock, "_CODEX_SINCE_EPOCH", None), \
             mock.patch.object(relay.peerlog, "codex_newest", return_value="NEWEST") as cn, \
             mock.patch.object(relay.peerlog, "codex_follow", return_value="FOLLOW") as cf:
            self.assertIsNone(relay.lock_codex(self.A, set(), "%0"))
            self.assertEqual(relay.refresh_codex_lock(self.a_old, self.A, "%0"), self.a_old)  # hold
            cn.assert_not_called()
            cf.assert_not_called()

    def test_relay_prefers_codex_via_pane_over_the_mtime_heuristic(self):
        # when peer-log's /proc identity resolves the pair's Codex, the relay adopts / locks /
        # follows THAT — one source of truth with `peer`, not the newest-for-cwd guess.
        with mock.patch.object(relay.peerlog, "codex_via_pane", return_value=self.a_old):
            self.assertEqual(relay.lock_codex(self.A, set(), "%0"), self.a_old)
            # even though a_mid is newer for cwd A, the identity wins
            self.assertEqual(relay.refresh_codex_lock(self.a_mid, self.A, "%0"), self.a_old)

    def test_lock_codex_only_takes_rollouts_unseen_at_start(self):
        seen = set(relay.codex_all())
        self.assertIsNone(relay.lock_codex(self.A, seen, "%0"))
        a_new = self.rollout("a-new", self.A, 4)
        self.rollout("b-newer", self.B, 5)
        self.assertEqual(relay.lock_codex(self.A, seen, "%0"), a_new)

    def test_codex_cwd_matches_uses_the_shared_cache(self):
        self.assertTrue(relay.codex_cwd_matches(self.a_old, self.A))
        self.assertFalse(relay.codex_cwd_matches(self.b_new, self.A))
        self.assertIn(self.a_old, relay.peerlog._CODEX_CWD_CACHE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
