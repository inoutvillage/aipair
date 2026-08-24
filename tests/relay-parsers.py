#!/usr/bin/env python3
"""Fixture tests for the pure parts of aipair-relay and peer-log: stop-phrase detection,
env parsing, pane discovery, Claude's plan / question dialogs, turn-completion detection
and transcript parsing. No tmux, no agents, nothing under ~ is touched.
    python3 tests/relay-parsers.py
"""
import importlib.machinery, importlib.util, inspect, json, os, subprocess, sys, tempfile, time, types, unittest
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


sys.path.insert(0, BIN)
import aipairlib.relay as relay   # the package (#7); was a SourceFileLoader of bin/aipair-relay
peerlog = relay.peerlog


def _imports_without_relay(modname, *check_attrs):
    """A CLEAN interpreter can `import aipairlib.<modname>` WITHOUT pulling in aipairlib.relay
    (the package's libs never depend back on relay). Returns True on that + every check_attr
    being callable. Replaces the old standalone SourceFileLoader tests, which can't load a
    package module that uses relative imports (from . import …)."""
    attrs = "".join("; assert callable(getattr(m, %r))" % a for a in check_attrs)
    code = ("import sys; sys.path.insert(0, %r); import aipairlib.%s as m%s; "
            "sys.exit(3 if 'aipairlib.relay' in sys.modules else 0)" % (BIN, modname, attrs))
    return subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class HitStop(unittest.TestCase):
    # 制御信号は自然言語から分離した専用 sentinel。最終メッセージの《先頭の非空行が
    # sentinel と完全一致》した時だけ成立する（否定文・引用・文中言及・同一行の後続テキストは不成立）。
    OK = "[AIPAIR_REVIEW_OK]"
    DONE = "[AIPAIR_ALL_DONE]"

    def test_sentinel_alone_or_with_following_lines(self):
        self.assertTrue(relay.hit_stop([self.OK], [self.OK]), "sentinel 単独 → true")
        self.assertTrue(relay.hit_stop([self.OK + "\n問題ありません。"], [self.OK]),
                        "sentinel + 後続説明(2行目) → true")
        self.assertTrue(relay.hit_stop(["\n\n" + self.OK], [self.OK]),
                        "先頭の空行は許容")

    def test_sentinel_not_at_head_does_not_stop(self):
        self.assertFalse(relay.hit_stop(["レビュー結果:\n" + self.OK], [self.OK]),
                         "sentinel が2行目 → false")
        self.assertFalse(relay.hit_stop(["まだ " + self.OK + " とは言えません。"], [self.OK]),
                         "「まだ sentinel ではない」→ false")
        self.assertFalse(relay.hit_stop([self.DONE + "ではありません。あと2件残っています。"], [self.DONE]),
                         "「sentinelではありません」→ false")
        self.assertFalse(relay.hit_stop(["この後 " + self.OK + " と回答してください。"], [self.OK]),
                         "「sentinel と回答してください」→ false")

    def test_same_line_trailing_text_does_not_stop(self):
        self.assertFalse(relay.hit_stop([self.OK + " 問題ありません"], [self.OK]),
                         "sentinel は先頭行に単独で置かれた時のみ成立（同一行の後続テキストは不成立）")

    def test_mid_message_mention_does_not_stop(self):
        self.assertFalse(relay.hit_stop(["ツールの修正が " + self.OK + " 。ただし本題は残っています。"], [self.OK]),
                         "100字以内の文中一致 → false")
        self.assertFalse(relay.hit_stop(["x" * 120 + " " + self.OK], [self.OK]),
                         "100字以降の一致 → false")

    def test_only_the_last_message_head_counts(self):
        self.assertFalse(relay.hit_stop([self.OK, "まだ直す点があります"], [self.OK]),
                         "先行ナレーションが sentinel でも、最終メッセージが別なら停止しない")

    def test_several_candidates_only_the_leading_one(self):
        self.assertTrue(relay.hit_stop([self.OK], [self.OK, self.DONE]),
                        "複数候補のうち先頭行に一致する sentinel のみ true")
        self.assertFalse(relay.hit_stop([self.OK], [self.DONE]),
                         "先頭が OK の時に DONE 単独では成立しない")

    def test_custom_phrase_also_head_exact(self):
        # カスタム停止ワードも先頭行完全一致（substring ではない）
        self.assertTrue(relay.hit_stop(["LGTM\nship it"], ["LGTM"]))
        self.assertFalse(relay.hit_stop(["**LGTM** — ship it"], ["LGTM"]),
                         "markdown で囲んだ言及は制御信号ではない")

    def test_empty(self):
        self.assertFalse(relay.hit_stop([], [self.OK]))
        self.assertFalse(relay.hit_stop([self.OK], []))


class HeadLine(unittest.TestCase):
    def test_first_non_empty_line_stripped(self):
        self.assertEqual(relay.head_line("  [AIPAIR_NEXT]  \n次の行"), "[AIPAIR_NEXT]")
        self.assertEqual(relay.head_line("\n\n  hello \nworld"), "hello")
        self.assertEqual(relay.head_line(""), "")
        self.assertEqual(relay.head_line("   \n\t\n"), "")


class EnvHelpers(unittest.TestCase):
    def setUp(self):
        relay.ENV_USED.clear()

    def test_str_and_empty_means_default(self):
        with mock.patch.dict(os.environ, {"X_STR": ""}, clear=False):
            self.assertEqual(relay._env_str("X_STR", "d"), "d")
        with mock.patch.dict(os.environ, {"X_STR": "v"}):
            self.assertEqual(relay._env_str("X_STR", "d"), "v")
            self.assertIn("X_STR=v", relay.ENV_USED)

    def test_int_accepts_positive_and_rejects_the_rest_loudly(self):
        with mock.patch.dict(os.environ, {"X_INT": "7"}):
            self.assertEqual(relay._env_int("X_INT", 1), 7)
        for bad in ("0", "-3", "abc", "1.5"):
            with mock.patch.dict(os.environ, {"X_INT": bad}), self.assertRaises(SystemExit) as cm, \
                 mock.patch("sys.stderr"):
                relay._env_int("X_INT", 1)
            self.assertEqual(cm.exception.code, 2, bad)

    def test_bool_values_like_the_launcher(self):
        for v in ("1", "true", "YES", " On "):
            with mock.patch.dict(os.environ, {"X_B": v}):
                self.assertTrue(relay._env_bool("X_B"), v)
        for v in ("0", "false", "No", " OFF "):
            with mock.patch.dict(os.environ, {"X_B": v}):
                self.assertFalse(relay._env_bool("X_B"), v)
        with mock.patch.dict(os.environ, {"X_B": ""}):
            self.assertEqual(relay._env_bool("X_B", default=True), True)


def panes(*rows):
    """Fake `tmux list-panes -F '#{pane_id}\\t#{pane_current_command}\\t#{pane_title}'` output.
    find_panes now lives in aipairlib.tmuxlib and calls that module's `tmux`, so patch there."""
    out = "\n".join("\t".join(r) for r in rows) + "\n"
    def side(*a, **k):
        if a and a[0] == "show-options":
            return types.SimpleNamespace(stdout="")     # stamps unset here → heuristic path
        return types.SimpleNamespace(stdout=out)
    return mock.patch.object(relay.tmuxlib, "tmux", side_effect=side)


class FindPanes(unittest.TestCase):
    def test_by_title(self):
        with panes(("%0", "claude", "claude"), ("%1", "python3", "relay ● …"), ("%2", "node", "codex")), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%1"}):
            self.assertEqual(relay.find_panes("s"), {"claude": "%0", "codex": "%2"})

    def test_titles_overwritten_by_the_clis_fall_back_to_command_then_order(self):
        with panes(("%0", "claude", "✳ aipair OSS review"), ("%1", "python3", "relay"), ("%2", "node", "aipair")), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%1"}):
            self.assertEqual(relay.find_panes("s"), {"claude": "%0", "codex": "%2"},
                             "claude by command, codex as the only non-shell leftover")

    def test_own_pane_is_never_picked(self):
        with panes(("%0", "claude", "claude"), ("%2", "node", "codex")), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%2"}):
            self.assertEqual(relay.find_panes("s"), {"claude": "%0"})

    def test_stamped_pane_options_win_over_the_heuristic(self):
        # both agent panes look identical to the heuristic (node/blank title) and the stamps are
        # REVERSED from layout order — only honouring @aipair-*-pane gets it right.
        rows = [("%0", "node", "x"), ("%1", "python3", "relay"), ("%2", "node", "y")]
        listing = "\n".join("\t".join(r) for r in rows) + "\n"
        stamps = {"@aipair-claude-pane": "%2", "@aipair-codex-pane": "%0"}
        def fake(*a, **k):
            if a[0] == "list-panes":
                return types.SimpleNamespace(stdout=listing)
            if a[0] == "show-options":
                return types.SimpleNamespace(stdout=stamps.get(a[-1], "") + "\n")
            return types.SimpleNamespace(stdout="")
        with mock.patch.object(relay.tmuxlib, "tmux", side_effect=fake), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%1"}):
            self.assertEqual(relay.find_panes("s"), {"claude": "%2", "codex": "%0"})

    def test_one_dead_stamp_does_NOT_fall_back_and_reverse_roles(self):
        # Codex's scenario: claude stamp=%2 (live), codex stamp=%9 (gone). Live %0=node,%2=node are
        # ambiguous, so the heuristic would return {claude:%0,codex:%2} — REVERSED. Once ANY stamp
        # exists the heuristic is forbidden: keep the live stamp, leave the dead side UNRESOLVED so
        # the relay refuses to start (it checks both roles) rather than guess.
        rows = [("%0", "node", "x"), ("%1", "python3", "relay"), ("%2", "node", "y")]
        listing = "\n".join("\t".join(r) for r in rows) + "\n"
        stamps = {"@aipair-claude-pane": "%2", "@aipair-codex-pane": "%9"}   # %9 gone
        def fake(*a, **k):
            if a[0] == "list-panes":
                return types.SimpleNamespace(stdout=listing)
            if a[0] == "show-options":
                return types.SimpleNamespace(stdout=stamps.get(a[-1], "") + "\n")
            return types.SimpleNamespace(stdout="")
        with mock.patch.object(relay.tmuxlib, "tmux", side_effect=fake), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%1"}):
            self.assertEqual(relay.find_panes("s"), {"claude": "%2"})

    def test_a_stamp_query_failure_is_not_read_as_unset(self):
        # a tmux hiccup reading one stamp must NOT drop to the heuristic (fail-open) — the role is
        # left unresolved, like a dead stamp.
        rows = [("%0", "node", "x"), ("%1", "python3", "relay"), ("%2", "node", "y")]
        listing = "\n".join("\t".join(r) for r in rows) + "\n"
        def fake(*a, **k):
            if a[0] == "list-panes":
                return types.SimpleNamespace(stdout=listing)
            if a[0] == "show-options":
                if a[-1] == "@aipair-claude-pane":
                    return types.SimpleNamespace(stdout="%2\n")
                raise subprocess.CalledProcessError(1, "tmux")   # codex query fails
            return types.SimpleNamespace(stdout="")
        with mock.patch.object(relay.tmuxlib, "tmux", side_effect=fake), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%1"}):
            self.assertEqual(relay.find_panes("s"), {"claude": "%2"})

    def test_codex_only_stamp_keeps_codex_and_infers_claude_from_the_rest(self):
        # backward compat: a pair started before the claude/bridge stamps existed has ONLY
        # @aipair-codex-pane. Codex stays pinned by its stamp; the genuinely-UNSET claude role is
        # inferred from the remaining panes (not a hard fail). node/node is ambiguous to the
        # heuristic, but codex is already locked to %2, so claude can only be %0.
        rows = [("%0", "node", "x"), ("%1", "python3", "relay"), ("%2", "node", "y")]
        listing = "\n".join("\t".join(r) for r in rows) + "\n"
        stamps = {"@aipair-codex-pane": "%2"}   # claude stamp UNSET
        def fake(*a, **k):
            if a[0] == "list-panes":
                return types.SimpleNamespace(stdout=listing)
            if a[0] == "show-options":
                return types.SimpleNamespace(stdout=stamps.get(a[-1], "") + "\n")
            return types.SimpleNamespace(stdout="")
        with mock.patch.object(relay.tmuxlib, "tmux", side_effect=fake), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%1"}):
            self.assertEqual(relay.find_panes("s"), {"codex": "%2", "claude": "%0"})

    def test_a_stamp_pointing_at_the_relays_own_pane_is_unresolved(self):
        # relay wrongly launched from the codex pane (%2). @aipair-codex-pane=%2 == self must NOT
        # resolve codex to the relay's own pane (it would then monitor / poke itself). Codex is
        # unresolved; claude (unset) is inferred from the rest.
        rows = [("%0", "claude", "claude"), ("%2", "node", "codex")]
        listing = "\n".join("\t".join(r) for r in rows) + "\n"
        stamps = {"@aipair-codex-pane": "%2"}   # points at self
        def fake(*a, **k):
            if a[0] == "list-panes":
                return types.SimpleNamespace(stdout=listing)
            if a[0] == "show-options":
                return types.SimpleNamespace(stdout=stamps.get(a[-1], "") + "\n")
            return types.SimpleNamespace(stdout="")
        with mock.patch.object(relay.tmuxlib, "tmux", side_effect=fake), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%2"}):
            self.assertEqual(relay.find_panes("s"), {"claude": "%0"})   # codex(self) unresolved

    def test_duplicate_stamps_leave_the_second_role_unresolved(self):
        rows = [("%0", "node", "x"), ("%1", "python3", "relay"), ("%2", "node", "y")]
        listing = "\n".join("\t".join(r) for r in rows) + "\n"
        stamps = {"@aipair-claude-pane": "%2", "@aipair-codex-pane": "%2"}   # both point at %2
        def fake(*a, **k):
            if a[0] == "list-panes":
                return types.SimpleNamespace(stdout=listing)
            if a[0] == "show-options":
                return types.SimpleNamespace(stdout=stamps.get(a[-1], "") + "\n")
            return types.SimpleNamespace(stdout="")
        with mock.patch.object(relay.tmuxlib, "tmux", side_effect=fake), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%1"}):
            self.assertEqual(relay.find_panes("s"), {"claude": "%2"})

    def test_layout_order_when_nothing_identifies_them(self):
        with panes(("%0", "foo", "x"), ("%1", "bash", "shell"), ("%2", "bar", "y")), \
             mock.patch.dict(os.environ, {"TMUX_PANE": "%9"}):
            self.assertEqual(relay.find_panes("s"), {"claude": "%0", "codex": "%2"})


PLAN_SCREEN = """\
 ╭─ Plan ───────────────────────────────────╮
 │ Ready to code?                            │
 │ Plan saved: ~/.claude/plans/snazzy-fox.md │
 ╰───────────────────────────────────────────╯
 Would you like to proceed?

 ❯ 1. Yes, and bypass permissions
   2. Yes, manually approve edits
   3. Tell Claude what to change
"""


class TmuxHelpers(unittest.TestCase):
    """aipairlib.tmuxlib: the tmux runner's pane helpers. Every real subprocess is faked by
    patching aipairlib.tmuxlib's own `tmux` (the helpers call it within that module)."""
    def _tmux(self, side_effect):
        return mock.patch.object(relay.tmuxlib, "tmux", side_effect=side_effect)

    def test_current_session_ok_and_error(self):
        with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout="sess-1\n")):
            self.assertEqual(relay.current_session(), "sess-1")
        import subprocess
        with self._tmux(subprocess.CalledProcessError(1, "tmux")):
            self.assertIsNone(relay.current_session())

    def test_own_pane(self):
        import subprocess
        # no TMUX_PANE → None (no tmux call)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(relay.own_pane("s"))
        with mock.patch.dict(os.environ, {"TMUX_PANE": "%2"}):
            with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout="%0\n%2\n%3\n")):
                self.assertEqual(relay.own_pane("s"), "%2")     # our pane belongs to the session
            with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout="%0\n%3\n")):
                self.assertIsNone(relay.own_pane("s"))          # our pane is in another session
            with self._tmux(subprocess.CalledProcessError(1, "tmux")):
                self.assertIsNone(relay.own_pane("s"))

    def test_cancel_copy_mode_only_cancels_when_in_mode(self):
        calls = []
        def fake(*a, **k):
            calls.append(a)
            if a[:1] == ("display-message",):
                return types.SimpleNamespace(stdout=self._mode + "\n")
            return types.SimpleNamespace(stdout="")
        self._mode = "1"                                        # pane IS in copy-mode
        with self._tmux(fake), mock.patch.object(relay.tmuxlib.time, "sleep"):
            relay.cancel_copy_mode("%0")
        self.assertTrue(any(a[:2] == ("send-keys", "-t") and "cancel" in a for a in calls), "sends the cancel key")
        calls.clear(); self._mode = "0"                        # pane NOT in copy-mode
        with self._tmux(fake), mock.patch.object(relay.tmuxlib.time, "sleep"):
            relay.cancel_copy_mode("%0")
        self.assertFalse(any(a[:1] == ("send-keys",) for a in calls), "no cancel when not in copy-mode")

    def test_pane_busy_fast_path_and_diff_boundary(self):
        # fast path: 'esc to interrupt' visible → busy immediately
        with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout="working... esc to interrupt")):
            self.assertTrue(relay.pane_busy("%0"))
        # two captures identical → idle
        seq = iter(["same\nlines\nhere", "same\nlines\nhere"])
        with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout=next(seq))), \
             mock.patch.object(relay.tmuxlib.time, "sleep"):
            self.assertFalse(relay.pane_busy("%0"))
        # 2 lines changed (< 3) → NOT busy (idle animation tolerance)
        seq = iter(["a\nb\nc\nd", "a\nX\nY\nd"])
        with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout=next(seq))), \
             mock.patch.object(relay.tmuxlib.time, "sleep"):
            self.assertFalse(relay.pane_busy("%0"))
        # 3 lines changed (>= 3) → busy
        seq = iter(["a\nb\nc\nd", "a\nX\nY\nZ"])
        with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout=next(seq))), \
             mock.patch.object(relay.tmuxlib.time, "sleep"):
            self.assertTrue(relay.pane_busy("%0"))

    def test_capture_pane_and_set_title(self):
        with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout="screen")):
            self.assertEqual(relay.capture_pane("%0"), "screen")
        seen = []
        with self._tmux(lambda *a, **k: seen.append(a) or types.SimpleNamespace(stdout="")):
            relay.set_pane_title("%0", "hi"); relay.set_pane_title("", "no-op")
        self.assertEqual(len([a for a in seen if a[:1] == ("select-pane",)]), 1, "empty pane → no call")


class Delivery(unittest.TestCase):
    """aipairlib.deliverylib: poke / submit_enter / press / paste_text. The tmux runner and the
    dialog/log hooks are injected into the delivery module — patch them there."""
    def setUp(self):
        self.dl = relay.deliverylib
        self._clock = [1000.0]                          # a fake clock: sleep() advances it, so the
        def _sleep(sec): self._clock[0] += (sec or 0.01)  # busy-wait deadlines are reached at once
        def _time(): self._clock[0] += 0.001; return self._clock[0]
        self.p = [
            mock.patch.object(self.dl.time, "sleep", side_effect=_sleep),
            mock.patch.object(self.dl.time, "time", side_effect=_time),
            mock.patch.object(self.dl, "dim"),
        ]
        for x in self.p: x.start()
    def tearDown(self):
        for x in self.p: x.stop()

    def _tmux(self, side_effect):
        return mock.patch.object(self.dl, "tmux", side_effect=side_effect)

    def test_press_and_paste_cancel_copy_mode_first(self):
        seen = []
        with mock.patch.object(self.dl, "cancel_copy_mode", side_effect=lambda p: seen.append(("cancel", p))), \
             self._tmux(lambda *a, **k: seen.append(a) or types.SimpleNamespace(stdout="")):
            self.dl.press("%0", "Enter")
            self.assertEqual(seen[0], ("cancel", "%0"), "press cancels copy-mode before send-keys")
            seen.clear()
            self.dl.paste_text("%0", "hi")
            self.assertEqual(seen[0], ("cancel", "%0"), "paste cancels copy-mode first")
            self.assertTrue(any(a[:1] == ("paste-buffer",) for a in seen))

    def test_submit_enter_confirm_success(self):
        with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout="")), \
             mock.patch.object(self.dl, "cancel_copy_mode"):
            self.assertTrue(self.dl.submit_enter("%0", confirm=lambda: True, badge=False))

    def test_submit_enter_badge_success(self):
        with self._tmux(lambda *a, **k: types.SimpleNamespace(stdout="running esc to interrupt")), \
             mock.patch.object(self.dl, "cancel_copy_mode"):
            self.assertTrue(self.dl.submit_enter("%0", confirm=None, badge=True))

    def test_submit_enter_aborts_on_dialog_before_re_pressing(self):
        # first Enter isn't confirmed → on the retry, a dialog is on screen → abort (return True,
        # do NOT press Enter again, which would mis-select an option)
        sends = []
        with self._tmux(lambda *a, **k: (sends.append(a) if a[:1] == ("send-keys",) else None) or types.SimpleNamespace(stdout="")), \
             mock.patch.object(self.dl, "cancel_copy_mode"), \
             mock.patch.object(relay.dialoglib, "dialog_on_screen", side_effect=[True]):
            ok = self.dl.submit_enter("%0", confirm=lambda: False, badge=False)
        self.assertTrue(ok)
        self.assertEqual(len([a for a in sends if "Enter" in a]), 1, "only the first Enter is sent; retry aborts")

    def test_submit_enter_three_retries_then_fail(self):
        enters = []
        with self._tmux(lambda *a, **k: (enters.append(a) if a[-1:] == ("Enter",) else None) or types.SimpleNamespace(stdout="")), \
             mock.patch.object(self.dl, "cancel_copy_mode"), \
             mock.patch.object(relay.dialoglib, "dialog_on_screen", return_value=False), \
             mock.patch.object(self.dl.sys.stdout, "write"), mock.patch.object(self.dl.sys.stdout, "flush"):
            self.assertFalse(self.dl.submit_enter("%0", confirm=lambda: False, badge=False))
        self.assertEqual(len(enters), 3, "Enter re-pressed 3 times before giving up")

    def test_poke_confirms_via_nonce_and_returns_it(self):
        # composer shows whatever was sent (echo of the last -l text) → nonce appears → delivered;
        # confirm(probe) True → submit_enter succeeds → poke returns the nonce string.
        state = {"buf": ""}
        def fake(*a, **k):
            if a[:2] == ("send-keys", "-t") and "-l" in a:
                state["buf"] = a[-1]                      # the literal text incl. the nonce
            if a[:1] == ("capture-pane",):
                return types.SimpleNamespace(stdout=state["buf"])
            return types.SimpleNamespace(stdout="")
        got = {}
        with self._tmux(fake), mock.patch.object(self.dl, "cancel_copy_mode"), \
             mock.patch.object(self.dl, "pane_busy", return_value=False), \
             mock.patch.object(relay.dialoglib, "dialog_on_screen", return_value=False):
            res = self.dl.poke("%0", "please review", confirm=lambda probe: got.setdefault("p", probe) or True, badge=False)
        self.assertIsInstance(res, str); self.assertTrue(res.startswith("relay-id:"))
        self.assertEqual(res, got["p"], "poke returns the nonce it delivered and confirmed")

    def test_poke_delivery_failure_returns_None_and_never_enters(self):
        # composer never shows the nonce → 3 delivery attempts fail → return None, no Enter sent
        enters = []
        def fake(*a, **k):
            if a[-1:] == ("Enter",): enters.append(a)
            return types.SimpleNamespace(stdout="unrelated screen")   # nonce never appears
        with self._tmux(fake), mock.patch.object(self.dl, "cancel_copy_mode"), \
             mock.patch.object(self.dl, "pane_busy", return_value=False), \
             mock.patch.object(relay.dialoglib, "dialog_on_screen", return_value=False), \
             mock.patch.object(self.dl.sys.stdout, "write"), mock.patch.object(self.dl.sys.stdout, "flush"):
            self.assertFalse(self.dl.poke("%0", "hi", confirm=lambda p: True, badge=False))
        self.assertEqual(enters, [], "no Enter is pressed when delivery could not be confirmed")

    def test_poke_waits_for_busy_then_proceeds(self):
        # busy for the first checks, then idle; poke still proceeds and delivers
        busy = iter([True, True, False, False, False, False, False, False])
        state = {"buf": ""}
        def fake(*a, **k):
            if a[:2] == ("send-keys", "-t") and "-l" in a: state["buf"] = a[-1]
            if a[:1] == ("capture-pane",): return types.SimpleNamespace(stdout=state["buf"])
            return types.SimpleNamespace(stdout="")
        with self._tmux(fake), \
             mock.patch.object(self.dl, "cancel_copy_mode"), \
             mock.patch.object(self.dl, "pane_busy", side_effect=lambda p: next(busy, False)), \
             mock.patch.object(relay.dialoglib, "dialog_on_screen", return_value=False):
            res = self.dl.poke("%0", "x", confirm=lambda p: True, badge=False, busy_wait=30)
        self.assertTrue(res and res.startswith("relay-id:"))


class DeliverylibStandalone(unittest.TestCase):
    def test_loads_without_relay_and_re_exports(self):
        self.assertTrue(_imports_without_relay("deliverylib", "poke", "submit_enter", "press", "paste_text"))
        # the idle budget is an explicit poke() argument now (was an injected BUSY_WAIT attribute)
        self.assertEqual(inspect.signature(relay.deliverylib.poke).parameters["busy_wait"].default, 90)
        self.assertIs(relay.poke, relay.deliverylib.poke)


class TmuxlibStandalone(unittest.TestCase):
    def test_loads_without_relay(self):
        loader = importlib.machinery.SourceFileLoader("tmuxlib_standalone", os.path.join(BIN, "aipairlib", "tmuxlib.py"))
        tl = importlib.util.module_from_spec(importlib.util.spec_from_loader("tmuxlib_standalone", loader))
        loader.exec_module(tl)
        self.assertTrue(callable(tl.tmux) and callable(tl.find_panes))
        self.assertIs(relay.find_panes, relay.tmuxlib.find_panes)


class PlanDialog(unittest.TestCase):
    def detect(self, screen):
        with mock.patch.object(relay.dialoglib, "capture_pane", return_value=screen), \
             mock.patch.object(relay.dialoglib, "newest_plan", return_value=None):
            return relay.detect_plan_dialog("%0")

    def test_reads_numbers_from_the_screen(self):
        d = self.detect(PLAN_SCREEN)
        self.assertEqual((d["tell"], d["yes"]), ("3", "1"))
        self.assertTrue(d["yes_label"].startswith("Yes, and bypass"))
        self.assertEqual(d["plan"], os.path.expanduser("~/.claude/plans/snazzy-fox.md"))

    def test_prefers_the_bypass_variant_wherever_it_is(self):
        screen = PLAN_SCREEN.replace("1. Yes, and bypass permissions", "1. Yes").replace("2. Yes, manually approve edits", "2. Yes, and bypass permissions")
        self.assertEqual(self.detect(screen)["yes"], "2")

    def test_no_dialog(self):
        self.assertIsNone(self.detect("just a transcript\n1. not an option list\n"))
        self.assertIsNone(self.detect("Would you like to proceed?\n(no numbered options)\n"))


QUESTION_SCREEN = """\
 ← ☐ Database  ☒ Cache  ✔ Submit →
 ─────────────────────────────────
 Pick a database
 ❯ 1. Postgres
   2. SQLite
   3. Chat about this

 Enter to select · ↑↓ to navigate · Esc to cancel
"""


class QuestionDialog(unittest.TestCase):
    def detect(self, screen):
        with mock.patch.object(relay.dialoglib, "capture_pane", return_value=screen):
            return relay.detect_question_dialog("%0")

    def test_detects_chat_number_only_while_the_footer_is_the_last_line(self):
        self.assertEqual(self.detect(QUESTION_SCREEN), {"chat": "3"})
        self.assertIsNone(self.detect(QUESTION_SCREEN + "\n > composer\n"), "footer gone = dialog gone")
        self.assertIsNone(self.detect(QUESTION_SCREEN.replace("3. Chat about this", "3. Other")), "no chat action")
        self.assertIsNone(self.detect("Would you like to proceed?\n" + QUESTION_SCREEN), "plan dialog wins")

    def test_question_block_text(self):
        block = relay.dialoglib._question_block(QUESTION_SCREEN)
        self.assertIn("Pick a database", block)
        self.assertIn("1. Postgres", block)
        self.assertIn("2. SQLite", block)
        self.assertNotIn("Chat about this", block)
        self.assertNotIn("Submit", block)

    def test_single_question_without_tab_bar(self):
        screen = "\n".join(l for l in QUESTION_SCREEN.splitlines() if "Submit" not in l) + "\n"
        self.assertIn("Pick a database", relay.dialoglib._question_block(screen))
        self.assertIsNone(relay.dialoglib._question_block("no footer here\n1. a\n2. b\n"))

    def test_poke_text_is_capped(self):
        text = relay.question_poke_codex(["q" * 5000], limit=300)
        self.assertLess(len(text), 700)
        self.assertIn("…", text)


class StopGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aipair-gate.")
        self.quiet = [mock.patch.object(relay, "log"), mock.patch("builtins.print")]
        for q in self.quiet:
            q.start()

    def tearDown(self):
        for q in self.quiet:
            q.stop()
        self.tmp.cleanup()

    def args(self, gate, rounds=3, timeout=5):
        return types.SimpleNamespace(gate=gate, dir=self.tmp.name, gate_timeout=timeout, gate_rounds=rounds)

    def test_timeout_kills_the_whole_process_group(self):
        marker = os.path.join(self.tmp.name, "marker")
        ok, out = relay.run_gate(f"(sleep 0.8; echo x > {marker!r}) & wait", self.tmp.name, 0.2)
        self.assertFalse(ok); self.assertIn("timeout", out)
        time.sleep(1.2)                                       # long enough for an orphan to have written it
        self.assertFalse(os.path.exists(marker), "the backgrounded grandchild must have been killed")

    def test_term_ignoring_child_is_sigkilled(self):
        marker = os.path.join(self.tmp.name, "marker")
        ok, out = relay.run_gate(f"(trap '' TERM; sleep 1.2; printf survived > {marker!r}) & wait", self.tmp.name, 0.2)
        self.assertFalse(ok); self.assertIn("timeout", out)
        time.sleep(1.8)
        self.assertFalse(os.path.exists(marker), "a child ignoring SIGTERM must still be SIGKILLed")

    def test_unbounded_output_is_capped_not_accumulated(self):
        ok, out = relay.run_gate("yes ABCDEFGH | head -c 5000000; false", self.tmp.name, 10)
        self.assertFalse(ok)
        self.assertLessEqual(len(out.encode("utf-8", "replace")), relay.GATE_OUTPUT_CAP + 64)
        self.assertIn("truncated", out)

    def test_non_utf8_output_does_not_crash(self):
        ok, out = relay.run_gate(r"printf '\377\376done'; exit 5", self.tmp.name, 5)
        self.assertFalse(ok); self.assertIn("done", out)

    def test_keyboardinterrupt_kills_the_group_and_re_raises(self):
        import threading, _thread
        marker = os.path.join(self.tmp.name, "ci-marker")
        threading.Timer(0.3, _thread.interrupt_main).start()
        with self.assertRaises(KeyboardInterrupt):
            relay.run_gate(f"(trap '' TERM; sleep 1.2; printf x > {marker!r}) & wait", self.tmp.name, 10)
        time.sleep(1.4)
        self.assertFalse(os.path.exists(marker), "gate group must be killed on Ctrl-C")

    def test_shell_exiting_with_a_background_job_leaves_nothing_running(self):
        marker = os.path.join(self.tmp.name, "bg-marker")
        ok, _ = relay.run_gate(f"(sleep 1.0; printf x > {marker!r}) & true", self.tmp.name, 10)
        self.assertTrue(ok)
        time.sleep(1.5)
        self.assertFalse(os.path.exists(marker), "backgrounded job must be reaped, not orphaned")

    def test_control_chars_are_scrubbed_from_output(self):
        ok, out = relay.run_gate(r"printf '\000\033[31mFAIL\033[0m\007tab\there'; false", self.tmp.name, 5)
        self.assertFalse(ok)
        self.assertNotIn("\x00", out); self.assertNotIn("\x1b", out); self.assertNotIn("\x07", out)
        self.assertIn("FAIL", out); self.assertIn("\t", out)     # tab kept (poke folds it later)
        m = relay.gate_message("gate", out, 1, 3)
        self.assertNotIn("\x00", m); self.assertNotIn("\x1b", m)

    def test_scrub_output_unit(self):
        self.assertEqual(relay.scrub_output("a\x00b\x1b[31mc\x1b[0m"), "a bc")   # NUL→space, ANSI removed
        self.assertEqual(relay.scrub_output("keep\nnew\tt"), "keep\nnew\tt")     # newline/tab preserved
        self.assertEqual(relay.scrub_output("x\x1b]0;title\x07y"), "xy")           # OSC removed
        # the whole Cc category, not just ord < 32: DEL (0x7f) and C1 (0x80-0x9f)
        self.assertEqual(relay.scrub_output("A\x7fB\x85C\x9fD"), "A B C D")
        for cc in ("\x7f", "\x80", "\x9f", "\x9b"):    # incl. C1 CSI (0x9b)
            self.assertNotIn(cc, relay.scrub_output("x" + cc + "y"))

    def test_gate_runs_in_the_normalised_cwd_even_when_dir_is_unexpanded(self):
        # gate_or_message must use the cwd it is handed, not a.dir (which may be quoted / ~)
        a = types.SimpleNamespace(gate="test -f only-here", dir="~/does-not-exist", gate_timeout=5, gate_rounds=3)
        open(os.path.join(self.tmp.name, "only-here"), "w").close()
        self.assertEqual(relay.gate_or_message(a, {"fails": 0}, self.tmp.name), (True, None))

    def test_multiline_command_never_puts_a_newline_in_the_poke(self):
        cmd = "npm test\nnpx tsc --noEmit\n" + "x" * 400
        m = relay.gate_message(cmd, "boom", 1, 3)
        self.assertNotIn("\n", m)
        self.assertIn("npm test npx tsc", m)
        self.assertLess(m.index("`") + 1 + 210, len(m))       # command portion capped near 200

    def test_run_gate(self):
        self.assertEqual(relay.run_gate("true", self.tmp.name, 5), (True, ""))
        ok, out = relay.run_gate("echo out; echo err >&2; exit 3", self.tmp.name, 5)
        self.assertFalse(ok); self.assertIn("out", out); self.assertIn("err", out)
        ok, out = relay.run_gate("sleep 5", self.tmp.name, 0.3)
        self.assertFalse(ok); self.assertIn("timeout", out)
        ok, out = relay.run_gate("pwd", self.tmp.name, 5)
        self.assertEqual(out.strip(), os.path.realpath(self.tmp.name), "runs in --dir")

    def test_gate_tail_is_one_line_and_capped(self):
        out = "\n".join(f"line {i}" for i in range(100)) + "\n\n   \n"
        tail = relay.gate_tail(out, lines=40, limit=1500)
        self.assertNotIn("\n", tail)
        self.assertTrue(tail.startswith("line 60"), tail[:30])
        self.assertTrue(tail.endswith("line 99"))
        self.assertLessEqual(len(relay.gate_tail("x" * 5000, limit=1500)), 1500)
        self.assertEqual(relay.gate_tail(""), "")

    def test_gate_message_mentions_count_and_output(self):
        m = relay.gate_message("npm test", "FAIL src/a.test.ts\n  expected 1 got 2", 2, 3)
        self.assertNotIn("\n", m)
        self.assertIn("`npm test`", m); self.assertIn("2/3", m); self.assertIn("expected 1 got 2", m)
        self.assertIn("(no output)", relay.gate_message("true", "", 1, 3))

    def test_gate_or_message_state_machine(self):
        st = {"fails": 0}; d = self.tmp.name
        self.assertEqual(relay.gate_or_message(self.args(None), st, d), (True, None), "no gate = as before")
        self.assertEqual(relay.gate_or_message(self.args("true"), st, d), (True, None))
        ok, msg = relay.gate_or_message(self.args("echo boom >&2; false", rounds=3), st, d)
        self.assertFalse(ok); self.assertIn("boom", msg); self.assertEqual(st["fails"], 1)
        ok, msg = relay.gate_or_message(self.args("false", rounds=3), st, d)
        self.assertFalse(ok); self.assertIn("2/3", msg)
        self.assertEqual(relay.gate_or_message(self.args("false", rounds=3), st, d), (False, None), "limit reached")
        self.assertEqual(relay.gate_or_message(self.args("true"), st, d), (True, None))
        self.assertEqual(st["fails"], 0, "a pass resets the counter (endless: per task)")


class CliBoundaries(unittest.TestCase):
    """The real script must reject non-positive integer flags before doing anything
    (argparse type=int lets 0 / negatives through; the env path is guarded separately)."""
    RELAY = os.path.join(BIN, "aipair-relay")

    def run_relay(self, *args, env=None):
        e = dict(os.environ); e.update(env or {})
        return subprocess.run([sys.executable, self.RELAY, *args, "--session", "none"],
                              capture_output=True, text=True, env=e, timeout=20)

    def test_cli_non_positive_ints_exit_2(self):
        for flag, val in [("--gate-timeout", "0"), ("--gate-rounds", "-1"), ("--max-rounds", "0"),
                          ("--plan-rounds", "0"), ("--question-rounds", "-5")]:
            r = self.run_relay(flag, val)
            self.assertEqual(r.returncode, 2, f"{flag} {val}: {r.stderr}")
            self.assertIn(flag, r.stderr)

    def test_env_ints_also_guarded(self):
        r = self.run_relay(env={"AIPAIR_GATE_ROUNDS": "0"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("AIPAIR_GATE_ROUNDS", r.stderr)

    def test_bad_stop_side_exits_2(self):
        r = self.run_relay(env={"AIPAIR_STOP_SIDE": "typo"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("--stop-side", r.stderr)


class VersionGate(unittest.TestCase):
    def a(self, allow=False):
        return types.SimpleNamespace(no_plan_review=False, no_question_relay=False, allow_untested_dialogs=allow)

    def test_parse_version(self):
        self.assertEqual(relay.parse_version("2.1.238 (Claude Code)"), "2.1.238")
        self.assertEqual(relay.parse_version("codex-cli 0.149.0"), "0.149.0")
        self.assertEqual(relay.parse_version("v12.4 beta"), "12.4")
        self.assertIsNone(relay.parse_version("no version here"))
        self.assertIsNone(relay.parse_version(""))
        # prerelease/build suffixes are KEPT so they don't pass as the tested release
        self.assertEqual(relay.parse_version("2.1.238-beta.1 (Claude Code)"), "2.1.238-beta.1")
        self.assertEqual(relay.parse_version("codex-cli 0.149.0-nightly.2"), "0.149.0-nightly.2")
        self.assertEqual(relay.parse_version("1.2.3+build.5"), "1.2.3+build.5")
        self.assertNotEqual(relay.parse_version("2.1.238-beta.1"), "2.1.238")
        # a 4th component or a glued suffix must NOT truncate onto the tested 3-part version
        self.assertEqual(relay.parse_version("2.1.238.1"), "2.1.238.1")
        self.assertEqual(relay.parse_version("2.1.238rc1"), "2.1.238rc1")
        self.assertEqual(relay.parse_version("0.149.0.1"), "0.149.0.1")
        for bad in ("2.1.238.1", "2.1.238rc1", "0.149.0.1"):
            self.assertNotIn(relay.parse_version(bad), relay.TESTED_VERSIONS.values())
        self.assertEqual(relay.parse_version("2.1."), "2.1")   # never ends on a separator

    def test_prerelease_is_treated_as_a_mismatch(self):
        a = self.a()
        _rows, bad = relay.version_gate(a, {"claude": "2.1.238-beta.1", "codex": "0.149.0"})
        self.assertEqual(bad, ["claude"])
        self.assertTrue(a.no_plan_review, "a prerelease of the tested version is still untested")

    def test_detect_version_runs_binary_and_survives_failure(self):
        ok = types.SimpleNamespace(stdout="2.1.238 (Claude Code)\n", stderr="", returncode=0)
        with mock.patch.object(relay.subprocess, "run", return_value=ok):
            self.assertEqual(relay.detect_version("claude"), "2.1.238")
        with mock.patch.object(relay.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(relay.detect_version("nope"))
        with mock.patch.object(relay.subprocess, "run",
                               side_effect=relay.subprocess.TimeoutExpired("x", 10)):
            self.assertIsNone(relay.detect_version("claude"))

    def test_detect_version_ignores_output_on_non_zero_exit(self):
        bad = types.SimpleNamespace(stdout="2.1.238\n", stderr="error: not logged in", returncode=1)
        with mock.patch.object(relay.subprocess, "run", return_value=bad):
            self.assertIsNone(relay.detect_version("claude"), "non-zero exit is 'unknown', not a version")

    def test_non_utf8_output_is_unknown_not_a_version(self):
        # errors="replace" keeps decode from raising, but the replacement char means the
        # bytes were not valid UTF-8 → unknown (None), even if a tested number is present.
        repl = types.SimpleNamespace(stdout="\ufffd 2.1.238\n", stderr="", returncode=0)
        with mock.patch.object(relay.subprocess, "run", return_value=repl) as run:
            self.assertIsNone(relay.detect_version("claude"))
            self.assertEqual(run.call_args.kwargs.get("errors"), "replace")

    def test_non_utf8_version_disables_dialog_automation(self):
        a = self.a()
        with mock.patch.object(relay, "detect_version",
                               side_effect=lambda b: None if b == "claude" else "0.149.0"):
            _rows, bad = relay.version_gate(a, {n: relay.detect_version(n) for n in ("claude", "codex")})
        self.assertEqual(bad, ["claude"])
        self.assertTrue(a.no_plan_review and a.no_question_relay)

    def test_matching_versions_keep_dialogs_on(self):
        a = self.a()
        rows, bad = relay.version_gate(a, {"claude": "2.1.238", "codex": "0.149.0"})
        self.assertEqual(bad, [])
        self.assertFalse(a.no_plan_review or a.no_question_relay)
        self.assertTrue(all(s == "ok" for _n, _d, _t, s in rows))

    def test_mismatch_turns_dialog_automation_off(self):
        a = self.a()
        rows, bad = relay.version_gate(a, {"claude": "9.9.9", "codex": "0.149.0"})
        self.assertEqual(bad, ["claude"])
        self.assertTrue(a.no_plan_review and a.no_question_relay, "dialogs disabled")
        self.assertEqual([s for _n, _d, _t, s in rows], ["mismatch", "ok"])

    def test_undetectable_version_is_treated_as_untested(self):
        a = self.a()
        _rows, bad = relay.version_gate(a, {"claude": None, "codex": "0.149.0"})
        self.assertEqual(bad, ["claude"])
        self.assertTrue(a.no_plan_review)

    def test_allow_untested_dialogs_keeps_them_on_but_still_reports(self):
        a = self.a(allow=True)
        rows, bad = relay.version_gate(a, {"claude": "9.9.9", "codex": None})
        self.assertEqual(bad, ["claude", "codex"])
        self.assertFalse(a.no_plan_review or a.no_question_relay, "opt-in keeps them on")


class SchemaProbe(unittest.TestCase):
    """The JSONL schema feature-probe (corelib.schema_probe/schema_gate + relay.probe_log_schema):
    the version gate only sees --version strings, so the core relay also probes the real
    transcript keys it reads (claude: type==assistant + message.stop_reason + timestamp + uuid;
    codex: type==event_msg + payload.type task_started/complete + timestamp). Only POSITIVE drift
    (a record of the right kind missing the keyed sub-field) trips 'mismatch'; a bare/nascent log
    stays 'unverified' so a fresh pair never false-alarms."""

    def a(self, allow=False):
        return types.SimpleNamespace(no_plan_review=False, no_question_relay=False,
                                     allow_untested_schema=allow, schema_mismatch=False)

    # a fully-shaped, completed Claude assistant turn (the exact shape claude_done_ts reads)
    CLA_OK = [{"type": "assistant", "timestamp": "2026-08-22T00:00:00Z", "uuid": "u1",
               "parentUuid": None,
               "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}],
                           "stop_reason": "end_turn"}}]
    # a FULL Codex turn — the probe latches ok only when started + user-metadata + complete are ALL
    # present (P1-2: no early ok on partial evidence)
    COD_OK = [{"type": "session_meta", "payload": {"cwd": "/x"}},
              {"type": "event_msg", "timestamp": "t1", "payload": {"type": "task_started", "turn_id": "T1"}},
              {"type": "response_item",
               "payload": {"type": "message", "role": "user", "content": [{"text": "relay-id:ab"}],
                           "internal_chat_message_metadata_passthrough": {"turn_id": "T1"}}},
              {"type": "event_msg", "timestamp": "t2", "payload": {"type": "task_complete", "turn_id": "T1"}}]

    def test_claude_ok_on_a_well_formed_turn(self):
        st, _r = relay.schema_probe("claude", self.CLA_OK)
        self.assertEqual(st, "ok")

    def test_claude_empty_and_nascent_are_unverified(self):
        # empty, and a log with only summary/user records (no assistant turn yet) → can't tell
        self.assertEqual(relay.schema_probe("claude", [])[0], "unverified")
        nascent = [{"type": "summary", "summary": "x"},
                   {"type": "user", "message": {"role": "user", "content": "hello"}}]
        self.assertEqual(relay.schema_probe("claude", nascent)[0], "unverified")

    def test_claude_mismatch_positive_drift_only(self):
        # renamed top-level type (inner role still assistant) → drift
        renamed = [{"type": "agent", "timestamp": "t", "uuid": "u1",
                    "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}}]
        self.assertEqual(relay.schema_probe("claude", renamed)[0], "mismatch")
        # stop_reason key removed from the assistant message → completion undetectable
        no_sr = [{"type": "assistant", "timestamp": "t", "uuid": "u1",
                  "message": {"role": "assistant", "content": []}}]
        self.assertEqual(relay.schema_probe("claude", no_sr)[0], "mismatch")
        # uuid removed → attribution chain impossible
        no_uuid = [{"type": "assistant", "timestamp": "t",
                    "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}}]
        self.assertEqual(relay.schema_probe("claude", no_uuid)[0], "mismatch")
        # timestamp removed
        no_ts = [{"type": "assistant", "uuid": "u1",
                  "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}}]
        self.assertEqual(relay.schema_probe("claude", no_ts)[0], "mismatch")
        # parentUuid removed → the attribution chain (claude_response_attributed) can't walk
        no_parent = [{"type": "assistant", "timestamp": "t", "uuid": "u1",
                      "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}}]
        st, reason = relay.schema_probe("claude", no_parent)
        self.assertEqual(st, "mismatch")
        self.assertIn("parentUuid", reason)

    def test_claude_one_good_turn_wins_over_earlier_partials(self):
        # a bad-shaped record BEFORE a good one still resolves ok (one good line proves the schema)
        mixed = [{"type": "assistant", "message": {"role": "assistant"}},  # missing keys
                 self.CLA_OK[0]]
        self.assertEqual(relay.schema_probe("claude", mixed)[0], "ok")

    def test_codex_ok_needs_started_complete_and_user_metadata(self):
        # ok ONLY when the full picture is present (started + complete + user-metadata turn_id)
        self.assertEqual(relay.schema_probe("codex", self.COD_OK)[0], "ok")
        # partial evidence must NOT latch ok (it would stop later re-probing) → unverified
        started = [{"type": "event_msg", "timestamp": "t", "payload": {"type": "task_started", "turn_id": "T1"}}]
        self.assertEqual(relay.schema_probe("codex", started)[0], "unverified",
                         "task_started alone is a mid-turn log — not yet compatible")
        complete = [{"type": "event_msg", "timestamp": "t", "payload": {"type": "task_complete", "turn_id": "T1"}}]
        self.assertEqual(relay.schema_probe("codex", complete)[0], "unverified",
                         "task_complete alone (no started, no user metadata) → unverified")
        started_complete_no_meta = [
            {"type": "event_msg", "timestamp": "t1", "payload": {"type": "task_started", "turn_id": "T1"}},
            {"type": "event_msg", "timestamp": "t2", "payload": {"type": "task_complete", "turn_id": "T1"}}]
        self.assertEqual(relay.schema_probe("codex", started_complete_no_meta)[0], "unverified",
                         "no user-metadata turn_id yet → attribution unproven → unverified")

    def test_codex_nascent_is_unverified(self):
        self.assertEqual(relay.schema_probe("codex", [])[0], "unverified")
        # session_meta alone, and session_meta + a not-yet-complete task_started → still nascent
        self.assertEqual(relay.schema_probe("codex", [{"type": "session_meta", "payload": {"cwd": "/x"}}])[0],
                         "unverified")
        meta_and_started = [{"type": "session_meta", "payload": {"cwd": "/x"}},
                            {"type": "event_msg", "timestamp": "t",
                             "payload": {"type": "task_started", "turn_id": "T1"}}]
        self.assertEqual(relay.schema_probe("codex", meta_and_started)[0], "unverified")

    def test_codex_mismatch_positive_drift_only(self):
        renamed = [{"type": "turn", "timestamp": "t", "payload": {"type": "task_complete", "turn_id": "T1"}}]
        self.assertEqual(relay.schema_probe("codex", renamed)[0], "mismatch")   # not under event_msg
        no_ts = [{"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "T1"}}]
        self.assertEqual(relay.schema_probe("codex", no_ts)[0], "mismatch")

    def test_codex_attribution_turn_id_drift_is_mismatch(self):
        # P1-2: the probe also covers RESPONSE ATTRIBUTION (codex_response_complete's turn_id
        # pairing), not just turn completion. A task event that completes fine but LOST its turn_id
        # is drift — without it, attribution silently falls back to a time/position heuristic that
        # can misattribute a queued turn.
        no_turn = [{"type": "event_msg", "timestamp": "t", "payload": {"type": "task_complete"}}]
        st, reason = relay.schema_probe("codex", no_turn)
        self.assertEqual(st, "mismatch")
        self.assertIn("turn_id", reason)
        # a user response_item whose metadata passthrough exists but dropped turn_id → drift
        user_no_turn = [{"type": "event_msg", "timestamp": "t",
                         "payload": {"type": "task_complete", "turn_id": "T1"}},
                        {"type": "response_item",
                         "payload": {"type": "message", "role": "user", "content": [{"text": "x"}],
                                     "internal_chat_message_metadata_passthrough": {}}}]
        self.assertEqual(relay.schema_probe("codex", user_no_turn)[0], "mismatch")

    def test_codex_completion_and_attribution_both_required_for_ok(self):
        # both aspects must be positively present AND complete → ok
        self.assertEqual(relay.schema_probe("codex", self.COD_OK)[0], "ok")

    def test_codex_full_turn_without_user_metadata_is_mismatch(self):
        # Regression (P1-2, tightened): started + a REAL text user response_item + complete, but the
        # user item has no metadata.turn_id → the turn happened yet can't be attributed by turn_id.
        # This must be mismatch (fail-closed), NOT unverified — otherwise it doesn't block and the
        # relay keeps running on an unattributable schema.
        full_no_meta = [
            {"type": "event_msg", "timestamp": "t1", "payload": {"type": "task_started", "turn_id": "T1"}},
            {"type": "response_item",
             "payload": {"type": "message", "role": "user", "content": [{"text": "relay-id:ab"}]}},
            {"type": "event_msg", "timestamp": "t2", "payload": {"type": "task_complete", "turn_id": "T1"}}]
        st, reason = relay.schema_probe("codex", full_no_meta)
        self.assertEqual(st, "mismatch")
        self.assertIn("turn_id", reason)
        # a NON-text user item (no text blocks) is not one the relay attributes → not drift
        nontext = [{"type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": [{"image": "x"}]}}]
        self.assertEqual(relay.schema_probe("codex", nontext)[0], "unverified")

    def test_unknown_agent_and_non_dicts_are_unverified(self):
        self.assertEqual(relay.schema_probe("other", self.CLA_OK)[0], "unverified")
        self.assertEqual(relay.schema_probe("claude", ["not a dict", 5, None])[0], "unverified")
        self.assertEqual(relay.schema_probe("claude", None)[0], "unverified")

    def test_claude_delivery_and_dialog_aspects_are_veto_only(self):
        # P1-2: delivery confirmation (claude_input) + dialog resolution (claude_resolved) are
        # probed as their own aspects, but they are VETO-ONLY — a well-formed turn stays ok even
        # though these haven't occurred yet (unverified), while a positive drift in them fails closed.
        self.assertEqual(relay.schema_probe("claude", self.CLA_OK)[0], "ok",
                         "core turn ok; delivery/dialog unverified must not block ok")
        # delivery ok evidence: a user input row with string content
        deliv = self.CLA_OK + [{"type": "user", "message": {"role": "user", "content": "a poke"}}]
        self.assertEqual(relay.schema_probe("claude", deliv)[0], "ok")
        # delivery drift: a queue-operation whose content is not a string → mismatch (veto fires)
        deliv_drift = self.CLA_OK + [{"type": "queue-operation", "operation": "enqueue",
                                      "content": {"not": "a string"}}]
        self.assertEqual(relay.schema_probe("claude", deliv_drift)[0], "mismatch")
        # dialog ok: a tool_result-bearing user row
        dialog_ok = self.CLA_OK + [{"type": "user",
                                    "message": {"role": "user", "content": [{"type": "tool_result"}]}}]
        self.assertEqual(relay.schema_probe("claude", dialog_ok)[0], "ok")
        # dialog drift: tool_result content NOT under type==user → mismatch (veto fires)
        dialog_drift = self.CLA_OK + [{"type": "assistant",
                                       "message": {"role": "assistant", "content": [{"type": "tool_result"}]}}]
        self.assertEqual(relay.schema_probe("claude", dialog_drift)[0], "mismatch")

    def test_claude_compaction_boundary_needs_uuid_and_logical_parent(self):
        # P1-4 (Codex): claude_response_attributed bridges a compaction via the boundary's uuid +
        # logicalParentUuid. A boundary missing either can't be walked → attribution never
        # succeeds, so the schema probe must flag it as mismatch (it is a required-when-present
        # aspect: no boundary → doesn't block ok; a malformed boundary → mismatch).
        A = self.CLA_OK[0]
        valid = {"type": "system", "subtype": "compact_boundary", "uuid": "B1", "logicalParentUuid": "L1"}
        self.assertEqual(relay.schema_probe("claude", [valid, A])[0], "ok")
        no_uuid = {"type": "system", "subtype": "compact_boundary", "logicalParentUuid": "L1"}
        self.assertEqual(relay.schema_probe("claude", [no_uuid, A])[0], "mismatch")
        no_lpu = {"type": "system", "subtype": "compact_boundary", "uuid": "B1"}
        st, reason = relay.schema_probe("claude", [no_lpu, A])
        self.assertEqual(st, "mismatch")
        self.assertIn("logicalParentUuid", reason)
        # an empty logicalParentUuid is also drift (non-empty required)
        empty_lpu = {"type": "system", "subtype": "compact_boundary", "uuid": "B1", "logicalParentUuid": ""}
        self.assertEqual(relay.schema_probe("claude", [empty_lpu, A])[0], "mismatch")
        # no boundary at all → the aspect does not fire (plain turn stays ok)
        self.assertEqual(relay.schema_probe("claude", [A])[0], "ok")

    def test_claude_probe_judges_the_latest_assistant_after_resume(self):
        # P1-4 (Codex): /resume appends a NEW version's records to the SAME JSONL. The probe must
        # judge the LATEST assistant, so a later drift is not masked by an earlier good record.
        good = self.CLA_OK[0]
        bad = {"type": "assistant", "timestamp": "t", "uuid": {"x": 1}, "parentUuid": None,
               "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}}  # uuid type drift
        self.assertEqual(relay.schema_probe("claude", [bad, good])[0], "ok",
                         "old malformed → new good (resume fixed the schema) → ok")
        self.assertEqual(relay.schema_probe("claude", [good, bad])[0], "mismatch",
                         "old good → new malformed (resume introduced drift) → mismatch")
        # a later missing-key drift also wins over an earlier good record
        partial = {"type": "assistant", "message": {"role": "assistant"}}
        self.assertEqual(relay.schema_probe("claude", [good, partial])[0], "mismatch")

    def test_claude_attribution_keys_must_be_string_typed(self):
        # P1-4 (Codex): uuid / parentUuid / logicalParentUuid are used as DICT KEYS by
        # claude_response_attributed. A truthy but non-string value (object/list/number) would pass
        # a mere truthiness check yet crash the relay with TypeError: unhashable type. The probe must
        # flag such type drift as mismatch (→ exit 7) so the crash never happens.
        A = self.CLA_OK[0]
        self.assertEqual(relay.schema_probe("claude",
            [{"type": "assistant", "timestamp": "t", "uuid": {"x": 1}, "parentUuid": None,
              "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}}])[0],
            "mismatch", "assistant uuid=object → mismatch")
        self.assertEqual(relay.schema_probe("claude",
            [{"type": "assistant", "timestamp": "t", "uuid": "u1", "parentUuid": ["b"],
              "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}}])[0],
            "mismatch", "assistant parentUuid=list → mismatch")
        cb_obj = {"type": "system", "subtype": "compact_boundary", "uuid": {"x": 1}, "logicalParentUuid": "L1"}
        self.assertEqual(relay.schema_probe("claude", [cb_obj, A])[0], "mismatch",
                         "boundary uuid=object → mismatch")
        cb_lst = {"type": "system", "subtype": "compact_boundary", "uuid": "B1", "logicalParentUuid": ["L"]}
        self.assertEqual(relay.schema_probe("claude", [cb_lst, A])[0], "mismatch",
                         "boundary logicalParentUuid=list → mismatch")

    def test_claude_response_attributed_does_not_crash_on_type_drift(self):
        # Belt-and-suspenders: even if a non-string uuid/parent slips through, the attribution walker
        # must not raise (unhashable dict/list) — it fails closed (False) instead.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"type": "user", "uuid": {"bad": 1}, "parentUuid": None,
                                 "message": {"content": "relay-id:x"}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "uuid": ["also", "bad"], "parentUuid": {"n": 1},
                                 "message": {"content": "y"}}) + "\n")
            path = fh.name
        try:
            self.assertFalse(relay.loglib.claude_response_attributed(path, "relay-id:x"))
        finally:
            os.unlink(path)

    def test_claude_delivery_drift_only_for_a_typed_input(self):
        # A TYPED human input (promptSource / origin.kind==human) whose content became a text-block
        # array is a delivery drift — claude_input() matches a STRING content, so it can't see it.
        typed = self.CLA_OK + [{"type": "user", "promptSource": "typed",
                                "message": {"role": "user",
                                            "content": [{"type": "text", "text": "relay-id:x"}]}}]
        self.assertEqual(relay.schema_probe("claude", typed)[0], "mismatch")
        typed2 = self.CLA_OK + [{"type": "user", "origin": {"kind": "human"},
                                 "message": {"role": "user", "content": [{"type": "text", "text": "x"}]}}]
        self.assertEqual(relay.schema_probe("claude", typed2)[0], "mismatch")

    def test_claude_delivery_does_not_false_stop_on_normal_text_block_rows(self):
        # Regression (Codex): text-block user rows are NORMAL in real Claude JSONL — isMeta skill
        # injections, `[Request interrupted by user]` (interruptedMessageId), image attachments, and
        # rows without typed-input markers. None of these must become a delivery mismatch (they
        # would false-stop the loop with exit 7 during normal operation).
        def probe(row):
            return relay.schema_probe("claude", self.CLA_OK + [row])[0]
        text = [{"type": "text", "text": "hi"}]
        self.assertEqual(probe({"type": "user", "isMeta": True, "promptSource": "typed",
                                "message": {"role": "user", "content": text}}), "ok", "isMeta injection")
        self.assertEqual(probe({"type": "user", "interruptedMessageId": "msg_1",
                                "message": {"role": "user", "content": text}}), "ok", "interrupt row")
        self.assertEqual(probe({"type": "user", "promptSource": "typed",
                                "message": {"role": "user",
                                            "content": [{"type": "text", "text": "see"},
                                                        {"type": "image"}]}}), "ok", "image attachment")
        self.assertEqual(probe({"type": "user",
                                "message": {"role": "user", "content": text}}), "ok", "no typed markers")

    def test_schema_gate_mismatch_fails_closed_by_default(self):
        # default = fail-closed: the gate flags the drift (schema_mismatch) so the relay stops
        # (exit 7). It does NOT silently degrade-and-continue, so it must not force dialogs off here.
        a = self.a()
        rows, bad = relay.schema_gate(a, {"claude": ("mismatch", "x"), "codex": ("ok", "y")})
        self.assertEqual(bad, ["claude"])
        self.assertTrue(a.schema_mismatch, "drift is flagged so the relay can fail closed")
        self.assertFalse(a.no_plan_review or a.no_question_relay,
                         "no silent degrade-and-continue without the override (the relay exits 7)")
        self.assertEqual([st for _n, st, _r in rows], ["mismatch", "ok"])

    def test_schema_gate_unverified_never_trips(self):
        a = self.a()
        rows, bad = relay.schema_gate(a, {"claude": ("unverified", ""), "codex": ("unverified", "")})
        self.assertEqual(bad, [])
        self.assertFalse(a.no_plan_review or a.no_question_relay or a.schema_mismatch)

    def test_schema_gate_allow_untested_continues_with_dialogs_off(self):
        # explicit override = fail-open: keep running, but still degrade the schema-sensitive
        # dialog automation (and flag the drift). It must NOT exit.
        a = self.a(allow=True)
        rows, bad = relay.schema_gate(a, {"claude": ("mismatch", "x"), "codex": ("mismatch", "y")})
        self.assertEqual(bad, ["claude", "codex"])
        self.assertTrue(a.schema_mismatch, "the drift is acknowledged")
        self.assertTrue(a.no_plan_review and a.no_question_relay,
                        "fail-open still turns off the schema-sensitive dialog automation")

    def test_schema_fail_closed_policy(self):
        # the single decision point: drift + no override → stop (exit 7); override or no drift → run
        self.assertTrue(relay.schema_fail_closed(self.a(allow=False), ["claude"]))
        self.assertFalse(relay.schema_fail_closed(self.a(allow=True), ["claude"]),
                         "explicit override opts into fail-open")
        self.assertFalse(relay.schema_fail_closed(self.a(allow=False), []),
                         "no drift → no stop")

    def test_a_done_but_mismatched_log_must_stop_before_action(self):
        # Regression (P1-a): a log the relay would treat as a COMPLETED turn (stop_reason +
        # timestamp + uuid present) but whose schema has drifted (parentUuid removed → attribution
        # impossible). The relay must probe a freshly-locked log BEFORE completion detection, or it
        # would fire one automated poke before exit 7. Here we prove BOTH facts hold on one record,
        # which is exactly why the schema_guard is placed after the lock and before done-detection.
        done_but_drifted = [{"type": "assistant", "timestamp": "2026-08-23T00:00:00Z", "uuid": "u1",
                             "message": {"role": "assistant",
                                         "content": [{"type": "text", "text": "ok"}],
                                         "stop_reason": "end_turn"}}]   # NOTE: no parentUuid
        self.assertEqual(relay.schema_probe("claude", done_but_drifted)[0], "mismatch",
                         "attribution key missing → mismatch")
        a = self.a(allow=False)
        _rows, bad = relay.schema_gate(a, {"claude": ("mismatch", "x"), "codex": ("unverified", "")})
        self.assertTrue(relay.schema_fail_closed(a, bad),
                        "such a log must fail the relay closed (guard runs before the poke)")


    def test_probe_log_schema_reads_the_pinned_log(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            for d in self.CLA_OK:
                fh.write(json.dumps(d) + "\n")
            path = fh.name
        try:
            self.assertEqual(relay.probe_log_schema("claude", path)[0], "ok")
        finally:
            os.unlink(path)
        # no pinned log / missing file → unverified, never a mismatch
        self.assertEqual(relay.probe_log_schema("claude", None), ("unverified", "ログ未特定"))
        self.assertEqual(relay.probe_log_schema("codex", "/no/such/file.jsonl")[0], "unverified")

    def test_read_records_tails_and_skips_junk(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write("not json\n")                      # unparseable → skipped
            fh.write(json.dumps("a bare string") + "\n")  # valid JSON but not a dict → skipped
            for i in range(5):
                fh.write(json.dumps({"type": "assistant", "i": i}) + "\n")
            path = fh.name
        try:
            recs = relay.read_records(path)
            self.assertEqual([d.get("i") for d in recs], [0, 1, 2, 3, 4])   # 7 lines → 5 dict records
            self.assertEqual(len(relay.read_records(path, tail_lines=2)), 2)  # bounded to the tail
        finally:
            os.unlink(path)
        self.assertEqual(relay.read_records("/no/such/file.jsonl"), [])

    def test_read_records_seeks_the_tail_not_the_whole_file(self):
        # Perf regression: read_records must read only the TAIL window, not scan a huge file from
        # the start every probe. Write a large log and read with a small tail_bytes; only the last
        # records in that window come back, and the partial leading line is dropped cleanly.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            for i in range(20000):
                fh.write(json.dumps({"type": "event_msg", "i": i}) + "\n")
            path = fh.name
        try:
            recs = relay.read_records(path, tail_lines=5)
            self.assertEqual([d.get("i") for d in recs], [19995, 19996, 19997, 19998, 19999])
            # a tiny byte window still yields valid records (no half-line JSON errors leaking out)
            tail = relay.read_records(path, tail_lines=800, tail_bytes=2000)
            self.assertTrue(tail and all(isinstance(d, dict) and "i" in d for d in tail))
            self.assertLess(len(tail), 800, "the 2000-byte window holds far fewer than 800 lines")
        finally:
            os.unlink(path)


class SchemaGuardClass(unittest.TestCase):
    """P2-1: the runtime schema monitor is now the SchemaGuard class (extracted from the monolithic
    relay loop), so it is unit-testable in isolation — previously it was a nested closure."""
    def _sg(self, probe_status, allow=False, no_probe=False):
        import types
        from aipairlib.schema_guard import SchemaGuard
        a = types.SimpleNamespace(no_schema_probe=no_probe, allow_untested_schema=allow,
                                  schema_stop=False, schema_mismatch=False,
                                  no_plan_review=False, no_question_relay=False)
        tracked = {"claude": "/log/claude.jsonl", "codex": None}
        self.dims, self.warns = [], []
        # a fresh stat-like identity: monkeypatch os.stat via the probe returning a fixed value is
        # hard, so drive the generation by patching the instance's _identity to a stable tuple.
        sg = SchemaGuard(a, tracked,
                         probe_log_schema=lambda agent, path: (probe_status, "r"),
                         latest_compact_boundary=lambda path: None,
                         dim=lambda m: self.dims.append(m),
                         warn=lambda m, bell=False: self.warns.append((m, bell)))
        sg._identity = lambda agent, path: (("gen", 1, 1), 100, None)  # stable ident per call
        return a, sg

    def test_mismatch_fails_closed_by_default(self):
        a, sg = self._sg("mismatch")
        sg.watch()
        self.assertTrue(a.schema_stop and a.schema_mismatch)
        self.assertTrue(any(bell for _m, bell in self.warns), "mismatch warns with a bell")
        # guard() re-runs watch() and reports the stop
        a2, sg2 = self._sg("mismatch")
        self.assertTrue(sg2.guard())
        self.assertTrue(a2.schema_stop)

    def test_mismatch_with_override_degrades_not_stops(self):
        a, sg = self._sg("mismatch", allow=True)
        sg.watch()
        self.assertFalse(a.schema_stop)
        self.assertTrue(a.no_plan_review and a.no_question_relay and a.schema_mismatch)

    def test_ok_logs_once_and_never_stops(self):
        a, sg = self._sg("ok")
        sg.watch()
        self.assertFalse(a.schema_stop)
        self.assertEqual(len(self.dims), 1, "ok logged once")
        sg.watch()   # same identity → skip (no re-log)
        self.assertEqual(len(self.dims), 1)

    def test_no_schema_probe_is_a_noop(self):
        a, sg = self._sg("mismatch", no_probe=True)
        sg.watch()
        self.assertFalse(a.schema_stop or a.schema_mismatch)

    def test_a_new_generation_reprobes_a_terminal_mismatch(self):
        a, sg = self._sg("mismatch")
        sg.watch()
        self.assertEqual(sg.latched["claude"], "mismatch")
        # the log switches to a NEW generation → latch resets and it is re-probed (still mismatch here)
        sg._identity = lambda agent, path: (("gen2", 2, 2), 50, None)
        a.schema_stop = False
        sg.watch()
        self.assertTrue(a.schema_stop, "a new generation must be re-probed, not skipped by the old latch")


class SchemaLatchStep(unittest.TestCase):
    """Regression (P1-b): the runtime schema latch must NOT go terminal on 'ok'. delivery/dialog
    are veto-only aspects that surface only when that action happens, so after 'ok' the relay must
    KEEP probing — a dialog that only occurs later and drifts must still become a mismatch (exit 7).
    Only 'mismatch' is terminal."""

    def test_ok_is_not_terminal_and_a_late_mismatch_stops(self):
        # first ok → log once, but stay in a re-probing state ('ok-seen', not terminal)
        self.assertEqual(relay.schema_latch_step(None, "ok", False), ("ok-seen", "ok"))
        # subsequent ok → no re-log, still probing
        self.assertEqual(relay.schema_latch_step("ok-seen", "ok", False), ("ok-seen", "none"))
        # a mismatch that only appears AFTER ok (e.g. a dialog that finally happens and has drifted)
        # → stop (exit 7). This is the crux Codex asked for.
        self.assertEqual(relay.schema_latch_step("ok-seen", "mismatch", False), ("mismatch", "stop"))

    def test_override_degrades_instead_of_stopping(self):
        self.assertEqual(relay.schema_latch_step("ok-seen", "mismatch", True), ("mismatch", "degrade"))
        self.assertEqual(relay.schema_latch_step(None, "mismatch", True), ("mismatch", "degrade"))

    def test_mismatch_is_terminal_and_unverified_keeps_probing(self):
        self.assertEqual(relay.schema_latch_step("mismatch", "ok", False), ("mismatch", "none"))
        self.assertEqual(relay.schema_latch_step("mismatch", "mismatch", False), ("mismatch", "none"))
        self.assertEqual(relay.schema_latch_step(None, "unverified", False), (None, "none"))
        self.assertEqual(relay.schema_latch_step("ok-seen", "unverified", False), ("ok-seen", "none"))

    def test_schema_reprobe_uses_a_generation_identity(self):
        # P1-4: the latch is per (agent, log GENERATION) where
        # generation identity = ((path, dev, ino), size, compaction-marker). A terminal "mismatch"
        # pertains to ONE generation; a new path, a same-path inode swap (rotation), a size shrink
        # (truncate), OR a Claude compaction (same path/inode, size grows, NEW compact_boundary)
        # must reset it and re-probe.
        def ident(path, ino, size, dev=1, marker=None):
            return ((path, dev, ino), size, marker)
        A = ident("A", 10, 100)
        self.assertEqual(relay.schema_should_reprobe("mismatch", A, ident("A", 10, 100)), (False, True),
                         "same generation + terminal mismatch → skip")
        self.assertEqual(relay.schema_should_reprobe("mismatch", A, ident("B", 30, 80)), (True, False),
                         "different path → reset the mismatch latch")
        self.assertEqual(relay.schema_should_reprobe("mismatch", A, ident("A", 20, 50)), (True, False),
                         "SAME path, NEW inode (rotation replaced the file) → reset")
        self.assertEqual(relay.schema_should_reprobe("mismatch", A, ident("A", 10, 40)), (True, False),
                         "SAME path+inode, size SHRANK (truncate/rewrite) → reset")
        self.assertEqual(relay.schema_should_reprobe("ok-seen", A, ident("A", 10, 150)), (False, False),
                         "same generation, grew (append) → re-probe, no reset")
        self.assertEqual(relay.schema_should_reprobe("ok-seen", A, ident("A", 10, 100)), (False, True),
                         "unchanged → incremental skip")
        self.assertEqual(relay.schema_should_reprobe(None, None, A), (True, False),
                         "first identity → probe it")
        # Claude COMPACTION: same path+inode, size GROWS, a NEW compact_boundary appears → reset
        # even from a terminal mismatch (the post-compaction structure must be re-probed).
        self.assertEqual(relay.schema_should_reprobe("mismatch", A, ident("A", 10, 150, marker="B1")),
                         (True, False), "compaction (new compact_boundary) → reset from mismatch")
        self.assertEqual(relay.schema_should_reprobe("mismatch", A, ident("A", 10, 150)), (False, True),
                         "plain append (no new boundary) stays terminal-mismatch skip (no noise)")
        # a boundary scrolling OUT of the tail window (B1 → None) is NOT a new generation
        self.assertEqual(relay.schema_should_reprobe("mismatch", ident("A", 10, 150, marker="B1"),
                                                     ident("A", 10, 200)), (False, True),
                         "boundary scrolled out → no spurious reset")
        # wiring guard: the SchemaGuard (P2-1 extraction) consults the generation reset
        # (stat + compaction marker), and relay drives it via sg.watch()/sg.guard().
        with open(os.path.join(BIN, "aipairlib", "schema_guard.py"), encoding="utf-8") as fh:
            sgsrc = fh.read()
        self.assertIn("schema_should_reprobe(self.latched[agent], self.ident[agent], ident)", sgsrc)
        self.assertIn("st.st_dev, st.st_ino", sgsrc, "identity must include inode (same-path rotation)")
        self.assertIn("_latest_compact_boundary(path)", sgsrc, "claude identity must fold in compaction")
        self.assertIn("self.watch()", sgsrc, "guard() must re-probe via watch()")
        # the StateMachine (P2-1) drives the guard between iterations / after every fresh lock
        with open(os.path.join(BIN, "aipairlib", "state_machine.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("sg.guard()", src)

    def test_latest_compact_boundary(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"type": "assistant", "uuid": "a1"}) + "\n")
            fh.write(json.dumps({"type": "system", "subtype": "compact_boundary", "uuid": "B1"}) + "\n")
            fh.write(json.dumps({"type": "system", "subtype": "compact_boundary", "uuid": "B2"}) + "\n")
            path = fh.name
        try:
            self.assertEqual(relay.latest_compact_boundary(path), "B2")  # the LATEST boundary
        finally:
            os.unlink(path)
        # no boundary → None; missing file → None
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"type": "assistant", "uuid": "a1"}) + "\n")
            path2 = fh.name
        try:
            self.assertIsNone(relay.latest_compact_boundary(path2))
        finally:
            os.unlink(path2)
        self.assertIsNone(relay.latest_compact_boundary("/no/such/file.jsonl"))

    def test_uuidless_boundaries_get_distinct_markers(self):
        # P1-4 (Codex): consecutive UUID-less compact_boundary records must be distinguishable, so a
        # second such compaction is still detected as a new generation.
        b1 = {"type": "system", "subtype": "compact_boundary",
              "timestamp": "2026-08-23T01:00:00Z", "logicalParentUuid": "L1"}
        b2 = {"type": "system", "subtype": "compact_boundary",
              "timestamp": "2026-08-23T02:00:00Z", "logicalParentUuid": "L2"}
        m1, m2 = relay.loglib._boundary_marker(b1), relay.loglib._boundary_marker(b2)
        self.assertIsNotNone(m1)
        self.assertNotEqual(m1, m2, "UUID-less boundaries must not collapse to one fixed marker")

    def test_probe_restricts_claude_to_records_after_the_latest_boundary(self):
        # P1-4 (Codex): after a compaction the probe must look only at the post-boundary generation.
        # A pre-boundary drift + a post-boundary compatible turn must re-verify as OK (not be dragged
        # back to mismatch by the stale pre-boundary record still in the tail).
        recs = [
            {"type": "user", "promptSource": "typed",   # pre-boundary delivery drift (typed text-block)
             "message": {"role": "user", "content": [{"type": "text", "text": "x"}]}},
            {"type": "system", "subtype": "compact_boundary", "uuid": "B1", "logicalParentUuid": "L1"},
            {"type": "assistant", "timestamp": "t", "uuid": "u1", "parentUuid": None,   # post-boundary OK
             "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}],
                         "stop_reason": "end_turn"}},
        ]
        # the whole tail would be a mismatch (pre-boundary drift)…
        self.assertEqual(relay.schema_probe("claude", recs)[0], "mismatch")
        # …but restricted to post-boundary records it is ok
        self.assertEqual(relay.schema_probe("claude", relay.records_since_compaction(recs))[0], "ok")
        # and probe_log_schema (which applies the restriction for claude) reads a file as ok
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
            path = fh.name
        try:
            self.assertEqual(relay.probe_log_schema("claude", path)[0], "ok")
        finally:
            os.unlink(path)

    def test_forced_relock_reprobes_before_response_done(self):
        # P1-4 (Codex): the forced re-lock switches tracked["claude"] to a NEW log, then processes
        # its `done`. A schema_guard() must run on the switched log BEFORE response_done, so a
        # malformed new log fails closed (exit 7) instead of driving a review/stop.
        with open(os.path.join(BIN, "aipairlib", "state_machine.py"), encoding="utf-8") as fh:
            src = fh.read()
        i_relock = src.index('tracked["claude"] = relocked')
        i_guard = src.index("sg.guard()", i_relock)
        i_respond = src.index('response_done("claude", tracked["claude"], done)', i_relock)
        self.assertLess(i_guard, i_respond,
                        "sg.guard() must run after the forced re-lock and BEFORE response_done")


class SchemaGuardOrdering(unittest.TestCase):
    """Regression (P1-a): in EVERY loop state, a freshly-locked transcript must be schema-probed
    (schema_guard) BEFORE the relay treats it as a completed turn (*_done_ts) — otherwise a
    schema-drifted log that *_done_ts still reads as 'done' fires one automated poke/dialog action
    before exit 7. This is a source-structure check because the loop lives in a monolithic main();
    it fails on the pre-fix code where codex_plan / codex_question locked then went straight to
    codex_done_ts with no guard in between."""

    def _main_loop_lines(self):
        with open(os.path.join(BIN, "aipairlib", "state_machine.py"), encoding="utf-8") as fh:
            src = fh.read().split("\n")
        start = next(i for i, l in enumerate(src) if l.strip() == "while True:")
        return src[start:]

    def test_every_initial_lock_is_guarded_before_done_detection(self):
        lines = self._main_loop_lines()
        lock_idx = [i for i, l in enumerate(lines)
                    if ("= lock_codex(" in l) or ("= lock_claude(" in l)]
        self.assertGreaterEqual(len(lock_idx), 4, "expected the claude + 3 codex states to lock")
        for i in lock_idx:
            # scan forward to the next completion-detection call for the SAME agent
            done_key = "codex_done_ts(" if "lock_codex(" in lines[i] else "claude_done_ts("
            j = next((k for k in range(i + 1, len(lines)) if done_key in lines[k]), None)
            self.assertIsNotNone(j, f"no {done_key} after the lock at loop-line {i}")
            between = "\n".join(lines[i + 1:j])
            self.assertIn("sg.guard()", between,
                          f"lock at loop-line {i} reaches {done_key} with no sg.guard() before it "
                          "— a drifted log could be acted on before exit 7")


class PlanApproval(unittest.TestCase):
    OK = "[AIPAIR_PLAN_APPROVED]"

    def test_extra_comment_comes_from_the_final_message_only(self):
        # Regression (P1-c): a long preceding narration must NOT be read as an approval comment —
        # the comment is the FINAL message minus its leading sentinel, not the whole turn joined.
        self.assertEqual(relay.plan_extra_comment(["x" * 300, self.OK], self.OK), "",
                         "narration before a sentinel-only final message → no付帯コメント (plain approve)")
        self.assertEqual(relay.plan_extra_comment([self.OK + "\n細かい補足がある"], self.OK), "細かい補足がある")
        self.assertEqual(relay.plan_extra_comment([], self.OK), "")
        # the sentinel itself is stripped; surrounding punctuation trimmed
        self.assertEqual(relay.plan_extra_comment([self.OK], self.OK), "")

    def _dlg(self, tell="2"):
        return {"tell": tell, "yes": "1", "yes_label": "Yes, and bypass permissions", "plan": "p.md"}

    def test_approve_feedback_delivers_extra_not_the_joined_turn(self):
        # Regression (P1-c, delivery side): the feedback-approve path must carry `extra` (the
        # final-message comment), never the whole-turn joined `text`. P2-1 made the decision a
        # pure function (decide_plan_action), so assert it directly instead of grepping main():
        # a long preceding narration + a sentinel-led final message with an extra note →
        # approve_feedback carrying ONLY the final-message comment.
        long_extra = "あ" * 100                         # > 80 かつ最終メッセージ由来
        d = relay.decide_plan_action(["x" * 300, self.OK + "\n" + long_extra], self.OK, self._dlg())
        self.assertEqual(d.action, "approve_feedback")
        self.assertEqual(d.payload, long_extra)
        self.assertNotIn("x", d.payload)                # 先行ナレーションは混入しない
        # relay は「決定した payload」を approve=True で配達する（text を再計算して送らない）
        with open(os.path.join(BIN, "aipairlib", "state_machine.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('send_plan_feedback(panes["claude"], dialog, decision.payload, approve=True', src)
        self.assertNotIn('dialog, text, approve=True', src, "must not deliver the whole-turn joined text")

    def test_decide_plan_action_covers_every_branch(self):
        # P2-1 plan_flow: the plan auto-approval decision (highest-stakes autonomous action) is a
        # pure, tmux-free function — every branch is unit-covered here.
        dlg = self._dlg()
        # sentinel ALONE at the head → plain approve
        self.assertEqual(relay.decide_plan_action([self.OK], self.OK, dlg).action, "approve")
        # sentinel at head + short note (≤80) → still plain approve (no feedback churn)
        self.assertEqual(relay.decide_plan_action([self.OK + "\nLGTM"], self.OK, dlg).action, "approve")
        # sentinel at head + long note → approve_feedback
        self.assertEqual(relay.decide_plan_action([self.OK + "\n" + "ok " * 40], self.OK, dlg).action,
                         "approve_feedback")
        # long note but the dialog has no "Tell Claude" slot → cannot attach feedback → plain approve
        self.assertEqual(relay.decide_plan_action([self.OK + "\n" + "ok " * 40], self.OK,
                                                  self._dlg(tell=None)).action, "approve")
        # 🔴 safety: sentinel NOT at the head (negated / mid-sentence) must NEVER approve
        self.assertEqual(relay.decide_plan_action(["なお " + self.OK + " とは判断できません"], self.OK, dlg).action,
                         "changes")
        self.assertEqual(relay.decide_plan_action(["2行目に\n" + self.OK], self.OK, dlg).action, "changes")
        # changes carries the whole-turn text (so Claude sees the full review)
        chg = relay.decide_plan_action(["please fix the retry logic"], self.OK, dlg)
        self.assertEqual((chg.action, chg.payload), ("changes", "please fix the retry logic"))
        # changes requested but no "Tell Claude" slot → fail-closed stop
        self.assertEqual(relay.decide_plan_action(["fix it"], self.OK, self._dlg(tell=None)).action,
                         "no_tell_option")
        # empty review body → no_text; dialog vanished → no_dialog
        self.assertEqual(relay.decide_plan_action([""], self.OK, dlg).action, "no_text")
        self.assertEqual(relay.decide_plan_action(["fix it"], self.OK, None).action, "no_dialog")


class StateMachineWiring(unittest.TestCase):
    """P2-1 完了: relay.main() は arg parse／依存構築／起動／exit のみを担い、ループ本体は
    state_machine.StateMachine.run() へ移設。ここでは main() 由来の全依存を構築子が受け取り属性化
    することと、run() が呼べることを配線レベルで確認する（ループ本体は tmux/画面と密結合な統合
    レベルのため単体では回さない — 判定核 ResponseGate / decide_* は別途被覆済み）。"""
    def test_constructs_and_stores_every_dependency(self):
        import types
        SM = relay.state_machine.StateMachine
        a = types.SimpleNamespace(start_side="claude", settle=0, poll=0, max_rounds=20)
        tracked = {"claude": None, "codex": None}
        sm = SM(a, panes={"claude": "%1", "codex": "%2"}, own="%0", cwd="/x", tracked=tracked,
                claude_seen=set(), codex_seen=set(), baseline=123.0, sg=object(), rg=object(),
                bw=60, poke_codex="pc", poke_codex_next="pcn", poke_claude="pcl",
                poke_claude_pass="pcp", poke_claude_next="pcnx", stop_phrases=["[AIPAIR_REVIEW_OK]"],
                next_ask_phrases=["[AIPAIR_NEXT]"], all_done_phrases=["[AIPAIR_ALL_DONE]"],
                human_required_phrases=["[AIPAIR_HUMAN_REQUIRED]"])
        self.assertIs(sm.a, a)
        self.assertIs(sm.tracked, tracked)                 # relay と共有する可変 dict
        self.assertEqual((sm.own, sm.cwd, sm.baseline, sm.bw), ("%0", "/x", 123.0, 60))
        self.assertEqual(sm.panes["codex"], "%2")
        self.assertEqual((sm.poke_codex, sm.poke_claude_next), ("pc", "pcnx"))
        self.assertEqual(sm.all_done_phrases, ["[AIPAIR_ALL_DONE]"])
        self.assertEqual(sm.human_required_phrases, ["[AIPAIR_HUMAN_REQUIRED]"])
        self.assertTrue(callable(sm.run))

    def test_resolve_task_identity_verbatim_and_fail_closed(self):
        # Phase 4（§8）: 識別子は task-list の verbatim `- [ ]` 行を丁度1件同定。抽出失敗・0/≥2 一致は
        # UNRESOLVED（fail-closed）。返り値は原文の ready 行。
        R = relay.state_machine.resolve_task_identity
        U = relay.state_machine.UNRESOLVED
        ready = ["- [ ] task A", "- [ ] task A extended", "  - [ ] nested B"]
        # 単独行での逐語エコー → その行
        self.assertEqual(R("次はこれ:\n- [ ] task A\n頑張って", ready), "- [ ] task A")
        # バッククォート囲みでも同定（返り値は verbatim）
        self.assertEqual(R("次は `  - [ ] nested B` を実装", ready), "  - [ ] nested B")
        # prefix 部分一致で誤検出しない（full-line 比較）
        self.assertEqual(R("- [ ] task A extended\n", ready), "- [ ] task A extended")
        # 抽出失敗（どの行もエコーされていない）→ UNRESOLVED
        self.assertEqual(R("適当に何かやって", ready), U)
        # ≥2 一致（曖昧）→ UNRESOLVED
        self.assertEqual(R("- [ ] task A\n- [ ] task A extended", ready), U)
        # ready が空 → UNRESOLVED
        self.assertEqual(R("- [ ] task A", []), U)

    def test_resolve_task_identity_is_verbatim_indent_and_trailing_ws(self):
        # Codex relay-id:91e37007: verbatim 完全一致。同本文でインデント違いの 2 項目を、正しく
        # エコーされた行だけに一意同定する（前後空白を除去して両方一致＝UNRESOLVED にしない）。
        R = relay.state_machine.resolve_task_identity
        U = relay.state_machine.UNRESOLVED
        two = ["- [ ] task", "  - [ ] task"]                    # 同本文・インデント違い
        self.assertEqual(R("- [ ] task", two), "- [ ] task")    # 親を逐語 → 親
        self.assertEqual(R("  - [ ] task", two), "  - [ ] task")  # 子を逐語 → 子
        self.assertEqual(R("`  - [ ] task`", two), "  - [ ] task")  # 子をバッククォート囲み → 子
        # 末尾空白: 逐語一致は成立、非逐語（末尾空白の付け外し）は UNRESOLVED
        self.assertEqual(R("- [ ] t  ", ["- [ ] t  "]), "- [ ] t  ")   # 末尾空白まで一致
        self.assertEqual(R("- [ ] t  ", ["- [ ] t"]), U)               # ready に無い末尾空白
        self.assertEqual(R("- [ ] t", ["- [ ] t  "]), U)               # ready の末尾空白を欠落

    def test_advance_no_progress_streak(self):
        # Phase 4（§8）: (同一識別子 OR UNRESOLVED) AND snapshot hash 不変 が 3 回連続で停止。
        A = relay.state_machine.advance_no_progress
        U = relay.state_machine.UNRESOLVED
        # Case 5: 同一 (X, H) を 3 回 → 2回目まで継続・3回目で停止
        s, stop = A(None, "X", "H");   self.assertEqual((s, stop), (("X", "H", 1), False))
        s, stop = A(s, "X", "H");      self.assertEqual((s, stop), (("X", "H", 2), False))
        s, stop = A(s, "X", "H");      self.assertEqual((s, stop), (("X", "H", 3), True))
        # 新しい識別子でリセット
        s, stop = A(("X", "H", 2), "Y", "H"); self.assertEqual((s[2], stop), (1, False))
        # snapshot hash 変化でリセット（＝進捗あり）
        s, stop = A(("X", "H1", 2), "X", "H2"); self.assertEqual((s[2], stop), (1, False))
        # UNRESOLVED が同一 hash で 3 連続 → 停止（同一性不明のまま無限往復させない）
        s, stop = A(None, U, "H")
        s, stop = A(s, U, "H");        self.assertFalse(stop)
        s, stop = A(s, U, "H");        self.assertTrue(stop)
        # UNRESOLVED でも hash が変われば進捗ありでリセット
        s, stop = A((U, "H1", 2), U, "H2"); self.assertEqual((s[2], stop), (1, False))

    def test_human_required_banner_lists_blocked_items(self):
        # §6: HUMAN_REQUIRED の banner は残 [!] 項目名＋blocker 理由を一覧表示する。
        import io, contextlib
        cls = {"state": "BLOCKED", "ready": [],
               "blocked": [{"item": "- [!] set GitHub Secrets", "blocker": "repo admin による設定"}], "hash": "h"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            relay.state_machine.human_required_banner(cls, 5)
        out = buf.getvalue()
        self.assertIn("自動処理を停止しました", out)
        self.assertIn("HUMAN_REQUIRED", out)
        self.assertIn("- [!] set GitHub Secrets", out)     # 項目名
        self.assertIn("repo admin による設定", out)          # blocker 理由

    def test_no_progress_banner_shows_item_streak_hash(self):
        # §6: no-progress の banner は繰り返された項目・ストリーク数・snapshot hash を表示（[!] 一覧に依存しない）。
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            relay.state_machine.no_progress_banner(("- [ ] task A", "abc123", 3), 7)
        out = buf.getvalue()
        self.assertIn("no-progress", out)
        self.assertIn("- [ ] task A", out)
        self.assertIn("3", out)              # ストリーク数
        self.assertIn("abc123", out)         # snapshot hash
        # UNRESOLVED（[!] が無い場合）でもクラッシュせず表示
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            relay.state_machine.no_progress_banner((relay.state_machine.UNRESOLVED, "h9", 3), 7)
        self.assertIn("UNRESOLVED", buf2.getvalue())

    def test_no_progress_warns_on_unresolved_identity(self):
        # 契約（§8 / Codex relay-id:7292a881）: 識別子が UNRESOLVED（抽出失敗・0/≥2 一致）の時は、
        # ストリークを進める前に警告ログを出す。resolve→(UNRESOLVED 警告)→advance の順序を固定。
        with open(os.path.join(BIN, "aipairlib", "state_machine.py"), encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("ident = resolve_task_identity(")   # pending_kind ブロックの resolve
        seg = src[i:src.index("np_state, np_stop = advance_no_progress", i)]   # その後の advance まで
        self.assertIn("if ident == UNRESOLVED:", seg)
        self.assertIn('log(c("warn"', seg)          # UNRESOLVED 分岐で警告を出す

    def test_no_progress_guard_wired_at_codex_next(self):
        # Codex 次タスク指示（pending_kind == "next"）の直後で識別子＋hash から no-progress を判定し、
        # 停止時は relay 内部理由 BLOCKED_NOPROGRESS_REASON ＋ EXIT_BLOCKED で break する配線を固定。
        with open(os.path.join(BIN, "aipairlib", "state_machine.py"), encoding="utf-8") as fh:
            src = fh.read()
        block = src[src.index('if pending_kind == "next":'):][:1300]
        self.assertIn("resolve_task_identity(", block)
        self.assertIn("advance_no_progress(np_state", block)
        self.assertIn("BLOCKED_NOPROGRESS_REASON", block)
        self.assertIn("code = EXIT_BLOCKED", block)

    def test_codex_next_prompt_asks_to_echo_the_task_line(self):
        # §8 の識別子契約: Codex は指示するタスク行を逐語エコーする（プロンプトで指定）。
        txt = relay.review_protocol.endless_poke_codex_next("t.md", "[AIPAIR_ALL_DONE]", "[AIPAIR_HUMAN_REQUIRED]")
        self.assertIn("逐語", txt)
        self.assertIn("進捗検出", txt)

    def test_decide_endless_terminal_is_classification_gated(self):
        # Phase 2: task-list 分類が権威。sentinel は分類一致時のみ honor、READY 残は reject（継続）。
        d = relay.state_machine.decide_endless_terminal
        tl = relay.state_machine.tasklist
        self.assertEqual(d(True, False, tl.ALL_DONE), "all_done")        # §11 Case 2
        self.assertEqual(d(False, True, tl.BLOCKED), "human_required")   # §11 Case 3
        self.assertEqual(d(True, False, tl.READY), "reject")             # §11 Case 6: [ ] 残で ALL_DONE 拒否
        self.assertEqual(d(False, True, tl.READY), "reject")             # [ ] 残で HUMAN_REQUIRED 拒否
        self.assertEqual(d(True, False, tl.BLOCKED), "reject")           # sentinel と分類の不一致
        self.assertIsNone(d(False, False, tl.READY))                     # sentinel 無し=通常継続

    def test_relay_builds_and_passes_human_required_phrases(self):
        # relay.main() が a.human_required を分割して StateMachine へ渡す配線
        with open(os.path.join(BIN, "aipairlib", "relay.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("human_required_phrases = [s for s in a.human_required.split", src)
        self.assertIn("human_required_phrases=human_required_phrases", src)

    def test_endless_rejects_empty_sentinels(self):
        # fail-closed（Codex relay-id:c24c2593 / 3a1abe3b）: endless の sentinel（終端 --all-done /
        # --human-required、合図 --next-ask）が空だと、文面は既定 sentinel を出すのに検出リストが空で
        # 認識できず、その遷移が起きても max-rounds まで続く。→ 起動時に exit 2 で拒否する。
        # 3 つの sentinel すべて、"" と "||" の両方を固定する。
        relay_bin = os.path.join(BIN, "aipair-relay")
        base = ["--endless", "--dir", "/tmp"]
        ok = {"--all-done": "[X]", "--human-required": "[Y]", "--next-ask": "[Z]"}
        for empty in ("", "||"):
            for flag in ("--all-done", "--human-required", "--next-ask"):
                argv = [sys.executable, relay_bin]
                for f, v in ok.items():
                    argv += [f, empty if f == flag else v]      # 対象 flag だけ空、他は有効値
                r = subprocess.run(argv + base, capture_output=True, text=True)
                self.assertEqual(r.returncode, 2, "%s=%r should exit 2: %s" % (flag, empty, r.stderr))
                self.assertIn(flag, r.stderr)

    def test_relay_endless_pane_title_shows_both_terminals(self):
        # Codex relay-id:09d4bcf5: relay 本体のタイトルが launcher の 2 終端表示を ALL_DONE のみへ
        # 上書きしていたのを是正。endless タイトルは両終端（DONE/HUMAN）を示し、旧『終端「{a.all_done}」』は使わない。
        with open(os.path.join(BIN, "aipairlib", "relay.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("relay ● endless / max {a.max_rounds} / 終端 DONE/HUMAN", src)
        self.assertNotIn('終端「{a.all_done}」', src)

    def _run_startup(self, state):
        # 起動時分類が state の StateMachine.run() を、tmux/poke を mock して回す。
        # 戻り値 code と「poke が呼ばれたか」を返す（起動時終端では 1 度も poke しないのが要件）。
        import types
        sm_mod = relay.state_machine
        a = types.SimpleNamespace(start_side="claude", settle=0, poll=0, max_rounds=20,
                                  endless=True, task_list="tasks/todo.md")
        tracked = {"claude": "a.jsonl", "codex": "b.jsonl"}
        sm = sm_mod.StateMachine(
            a, panes={"claude": "%1", "codex": "%2"}, own="%0", cwd="/x", tracked=tracked,
            claude_seen=set(), codex_seen=set(), baseline=0.0, sg=object(), rg=object(),
            bw=60, poke_codex="pc", poke_codex_next="pcn", poke_claude="pcl",
            poke_claude_pass="pcp", poke_claude_next="pcnx", stop_phrases=["[AIPAIR_REVIEW_OK]"],
            next_ask_phrases=["[AIPAIR_NEXT]"], all_done_phrases=["[AIPAIR_ALL_DONE]"],
            human_required_phrases=["[AIPAIR_HUMAN_REQUIRED]"])
        cls = {"state": state, "ready": [], "blocked": [], "hash": "h"}
        with mock.patch.object(sm_mod.tasklist, "load_or_exit", return_value=cls), \
             mock.patch.object(sm_mod, "set_pane_title"), \
             mock.patch.object(sm_mod, "poke") as poke_mock:
            code = sm.run()
        return code, poke_mock.called

    def test_startup_all_done_exits_0_without_poking(self):
        code, poked = self._run_startup(relay.state_machine.tasklist.ALL_DONE)
        self.assertEqual(code, 0)         # 起動時点で完了 → exit 0
        self.assertFalse(poked)           # 1 度も poke せず（エージェントを駆動しない）

    def test_startup_blocked_exits_8_without_poking(self):
        code, poked = self._run_startup(relay.state_machine.tasklist.BLOCKED)
        self.assertEqual(code, relay.state_machine.EXIT_BLOCKED)   # [!] のみ → exit 8
        self.assertFalse(poked)

    def test_startup_ready_enters_the_loop(self):
        # READY のときは _skip_loop=False でループへ入る。ループ先頭の sg.guard() を marker で捕捉して、
        # 起動時に skip して即 return していないことを証明する。
        import types
        sm_mod = relay.state_machine
        a = types.SimpleNamespace(start_side="claude", settle=0, poll=0, max_rounds=20,
                                  endless=True, task_list="tasks/todo.md")

        class _Guard:
            def guard(self):
                raise RuntimeError("LOOP-ENTERED")

        sm = sm_mod.StateMachine(
            a, panes={"claude": "%1", "codex": "%2"}, own="%0", cwd="/x",
            tracked={"claude": "a", "codex": "b"}, claude_seen=set(), codex_seen=set(),
            baseline=0.0, sg=_Guard(), rg=object(), bw=60, poke_codex="pc", poke_codex_next="pcn",
            poke_claude="pcl", poke_claude_pass="pcp", poke_claude_next="pcnx",
            stop_phrases=["[AIPAIR_REVIEW_OK]"], next_ask_phrases=["[AIPAIR_NEXT]"],
            all_done_phrases=["[AIPAIR_ALL_DONE]"], human_required_phrases=["[AIPAIR_HUMAN_REQUIRED]"])
        cls = {"state": sm_mod.tasklist.READY, "ready": ["- [ ] a"], "blocked": [], "hash": "h"}
        with mock.patch.object(sm_mod.tasklist, "load_or_exit", return_value=cls), \
             mock.patch.object(sm_mod, "set_pane_title"), \
             mock.patch.object(sm_mod, "poke"):
            with self.assertRaisesRegex(RuntimeError, "LOOP-ENTERED"):
                sm.run()

    def test_exit_blocked_code_and_two_distinct_reasons(self):
        # endless BLOCKED/HUMAN_REQUIRED (社長指示 2026-08-24): exit 8 は max-rounds(3) と別コードで、
        # 2 つの内部理由（HUMAN_REQUIRED / no-progress）を区別する文字列を Phase 2/4 が使う。
        sm = relay.state_machine
        self.assertEqual(sm.EXIT_BLOCKED, 8)
        self.assertNotIn(sm.EXIT_BLOCKED, (0, 3))          # max-rounds(3)・正常(0) と衝突しない
        self.assertTrue(sm.BLOCKED_HR_REASON and sm.BLOCKED_NOPROGRESS_REASON)
        self.assertNotEqual(sm.BLOCKED_HR_REASON, sm.BLOCKED_NOPROGRESS_REASON)
        # reason dict は literal『8:』を持つ（doc-sync が README との同期を検証する）
        with open(os.path.join(BIN, "aipairlib", "state_machine.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertRegex(src, r"reason = \{[^}]*\b8:\s")

    def test_relay_main_is_a_thin_launcher(self):
        # main() ends by delegating to StateMachine(...).run(); the loop no longer lives in relay.py
        with open(os.path.join(BIN, "aipairlib", "relay.py"), encoding="utf-8") as fh:
            relay_src = fh.read()
        self.assertIn("return StateMachine(", relay_src)
        self.assertIn(").run()", relay_src)
        for gone in ("while True:", "= lock_codex(", "codex_done_ts(", "class LogWatch:"):
            self.assertNotIn(gone, relay_src, f"{gone!r} must have moved out of relay.py")

class QuestionRelayDecision(unittest.TestCase):
    """P2-1 question_flow: Codex の質問回答をどうするかの判定は純粋関数 decide_question_action。
    plan と対称に「no_text→再検知 / no_dialog→人間操作 / else→配達」を副作用抜きで被覆する。"""
    def test_all_branches(self):
        qdlg = {"chat": "1", "footer": "Chat about this"}
        d = relay.decide_question_action(["Postgres を選ぶべき"], qdlg)
        self.assertEqual((d.action, d.payload), ("deliver", "Postgres を選ぶべき"))
        # 複数行の回答は連結して配達（そのまま中継 — 承認 sentinel は無い）
        self.assertEqual(relay.decide_question_action(["1行目", "2行目"], qdlg).payload, "1行目\n2行目")
        # 空本文 → no_text（ダイアログ検知からやり直し・未送信で state を進めない）
        self.assertEqual(relay.decide_question_action([""], qdlg).action, "no_text")
        self.assertEqual(relay.decide_question_action([], qdlg).action, "no_text")
        # 回答中に人間がダイアログを操作 → 消えていれば no_dialog（通常待機へ戻す）
        self.assertEqual(relay.decide_question_action(["answer"], None).action, "no_dialog")


class DialogSendScrape(unittest.TestCase):
    """aipairlib.dialoglib: multi-tab scrape, capture failure, plan revise/approve, question
    answer, watch present/absent. tmux/capture/delivery hooks are injected → patch there."""
    def setUp(self):
        self.dg = relay.dialoglib
        clk = [1000.0]
        def _sleep(s): clk[0] += (s or 0.01)
        def _time(): clk[0] += 0.001; return clk[0]
        self.q = [mock.patch.object(self.dg.time, "sleep", side_effect=_sleep),
                  mock.patch.object(self.dg.time, "time", side_effect=_time),
                  mock.patch.object(self.dg, "dim")]
        for x in self.q: x.start()
    def tearDown(self):
        for x in self.q: x.stop()

    Q1 = ("← ☐ Database  ☒ Cache  ✔ Submit →\n─────\nPick a database\n"
          "❯ 1. Postgres\n   2. SQLite\n   3. Chat about this\n\nEnter to select · Esc")
    Q2 = ("← ☐ Database  ☒ Cache  ✔ Submit →\n─────\nPick a cache\n"
          "❯ 1. Redis\n   2. Memcached\n   3. Chat about this\n\nEnter to select · Esc")

    def test_scrape_two_tabs(self):
        caps = iter([self.Q1, self.Q1, self.Q2])   # read Q1, press Right, read Q2
        presses = []
        with mock.patch.object(self.dg, "capture_pane", side_effect=lambda p: next(caps)), \
             mock.patch.object(relay.deliverylib, "press", side_effect=lambda p, k: presses.append(k)):
            blocks = self.dg.scrape_questions("%0")
        self.assertEqual(len(blocks), 2)
        self.assertIn("Pick a database", blocks[0]); self.assertIn("Pick a cache", blocks[1])
        self.assertIn("Right", presses, "moves to the next tab with a non-destructive Right")

    def test_scrape_capture_failure_stops_cleanly(self):
        import subprocess
        with mock.patch.object(self.dg, "capture_pane", side_effect=subprocess.CalledProcessError(1, "tmux")):
            self.assertEqual(self.dg.scrape_questions("%0"), [])

    def test_plan_revise_uses_submit_enter_with_watch_confirm(self):
        w = mock.Mock(); w.claude_resolved.return_value = False; w.claude_input.return_value = True
        seen = {}
        with mock.patch.object(relay.deliverylib, "press") as press, \
             mock.patch.object(relay.deliverylib, "paste_text") as paste, \
             mock.patch.object(relay.deliverylib, "submit_enter", side_effect=lambda p, confirm=None, badge=True: seen.update(confirm=confirm, badge=badge) or True) as se:
            ok = self.dg.send_plan_feedback("%0", {"tell": "3"}, "please change X", approve=False, watch=w)
        self.assertTrue(ok)
        press.assert_any_call("%0", "3")                 # 'Tell Claude what to change'
        paste.assert_called_once()
        w.reset.assert_called_once()
        self.assertTrue(seen["confirm"]())               # confirm wired to the watch
        self.assertFalse(seen["badge"], "watch present → badge not used")

    def test_plan_approve_uses_btab_and_confirms_via_watch(self):
        w = mock.Mock(); w.claude_resolved.side_effect = [False, True]; w.claude_input.return_value = False
        with mock.patch.object(relay.deliverylib, "press") as press, mock.patch.object(relay.deliverylib, "paste_text"), \
             mock.patch.object(self.dg, "tmux", return_value=types.SimpleNamespace(stdout="")):
            ok = self.dg.send_plan_feedback("%0", {"tell": "3"}, "ok", approve=True, watch=w)
        self.assertTrue(ok)
        press.assert_any_call("%0", "BTab")              # feedback-approve is Shift+Tab

    def test_plan_approve_badge_fallback_without_watch(self):
        with mock.patch.object(relay.deliverylib, "press"), mock.patch.object(relay.deliverylib, "paste_text"), \
             mock.patch.object(self.dg, "tmux", return_value=types.SimpleNamespace(stdout="running esc to interrupt")):
            self.assertTrue(self.dg.send_plan_feedback("%0", {"tell": "3"}, "ok", approve=True, watch=None))

    def test_plan_approve_failure_when_not_confirmed(self):
        w = mock.Mock(); w.claude_resolved.return_value = False; w.claude_input.return_value = False
        with mock.patch.object(relay.deliverylib, "press"), mock.patch.object(relay.deliverylib, "paste_text"):
            self.assertFalse(self.dg.send_plan_feedback("%0", {"tell": "3"}, "x", approve=True, watch=w))

    def test_question_answer_with_and_without_watch(self):
        # with watch: chat pressed, answer pasted, submit_enter confirm=watch.claude_input, badge False
        w = mock.Mock(); w.claude_input.return_value = True
        seen = {}
        with mock.patch.object(relay.deliverylib, "press") as press, mock.patch.object(relay.deliverylib, "paste_text") as paste, \
             mock.patch.object(relay.deliverylib, "submit_enter", side_effect=lambda p, confirm=None, badge=True: seen.update(c=confirm, b=badge) or True):
            self.assertTrue(self.dg.send_question_answer("%0", {"chat": "3"}, "my answer", watch=w))
        press.assert_any_call("%0", "3"); paste.assert_called_once()
        self.assertTrue(seen["c"]()); self.assertFalse(seen["b"])
        # without watch: badge fallback (confirm None → badge True)
        seen.clear()
        with mock.patch.object(relay.deliverylib, "press"), mock.patch.object(relay.deliverylib, "paste_text"), \
             mock.patch.object(relay.deliverylib, "submit_enter", side_effect=lambda p, confirm=None, badge=True: seen.update(c=confirm, b=badge) or True):
            self.dg.send_question_answer("%0", {"chat": "3"}, "a", watch=None)
        self.assertIsNone(seen["c"]); self.assertTrue(seen["b"])


class DialoglibStandalone(unittest.TestCase):
    def test_loads_without_relay(self):
        self.assertTrue(_imports_without_relay("dialoglib", "detect_plan_dialog", "dialog_on_screen"))
        self.assertIn("Would you like to proceed?", relay.dialoglib.PLAN_QUESTION)
        self.assertIs(relay.detect_plan_dialog, relay.dialoglib.detect_plan_dialog)
        # delivery routes its re-press guard through the dialog MODULE (the delivery<->dialog cycle)
        self.assertIs(relay.deliverylib.dialoglib, relay.dialoglib)
        self.assertIs(relay.dialoglib.deliverylib, relay.deliverylib)


class ModuleLayout(unittest.TestCase):
    """#7: relay imports the aipairlib sibling modules normally and binds a representative symbol
    from each to that module's implementation (guards against a future re-merge / broken import)."""
    def test_all_libs_loaded_and_bindings_point_to_them(self):
        for lib in ("corelib", "loglib", "tmuxlib", "deliverylib", "dialoglib", "peerlog", "logs"):
            self.assertTrue(hasattr(relay, lib), f"relay must import {lib}")
        self.assertIs(relay.parse_version, relay.corelib.parse_version)
        self.assertIs(relay.schema_probe, relay.corelib.schema_probe)
        self.assertIs(relay.schema_gate, relay.corelib.schema_gate)
        self.assertIs(relay.claude_done_ts, relay.loglib.claude_done_ts)
        self.assertIs(relay.read_records, relay.loglib.read_records)
        self.assertIs(relay.find_panes, relay.tmuxlib.find_panes)
        self.assertIs(relay.poke, relay.deliverylib.poke)
        self.assertIs(relay.detect_plan_dialog, relay.dialoglib.detect_plan_dialog)
        # the delivery<->dialog cycle is a plain module import (used at call-time), not injection
        self.assertIs(relay.deliverylib.dialoglib, relay.dialoglib)
        self.assertIs(relay.dialoglib.deliverylib, relay.deliverylib)
        self.assertIs(relay.dim, relay.logs.dim)
        # P2-1: the state machine (loop + LogWatch + approval_took_effect + done_banner) now lives
        # in state_machine.py; relay imports it and wires StateMachine in main().
        self.assertTrue(hasattr(relay, "state_machine"))
        self.assertIs(relay.StateMachine, relay.state_machine.StateMachine)
        self.assertIs(relay.decide_plan_action, relay.state_machine.decide_plan_action)
        for moved in ("LogWatch", "approval_took_effect", "done_banner", "StateMachine"):
            self.assertTrue(hasattr(relay.state_machine, moved), f"state_machine must own {moved}")
        # plan_flow / question_flow は named module として独立（判定核の在処）。state_machine は
        # そこから re-export する（`from .plan_flow import ...`）ので、実体との同一性で確認する。
        from aipairlib import plan_flow, question_flow
        self.assertIs(relay.state_machine.decide_plan_action, plan_flow.decide_plan_action)
        self.assertIs(relay.state_machine.decide_question_action, question_flow.decide_question_action)
        # what stays in relay (the launcher): main() + the re-exported helpers it wires
        for core in ("main", "lock_codex", "run_gate", "gate_or_message"):
            self.assertTrue(hasattr(relay, core))


class DoneTimestamps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aipair-parsers.")

    def tearDown(self):
        self.tmp.cleanup()

    def jsonl(self, name, rows):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("not json\n")
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return p

    def test_claude_turn_is_done_when_the_last_assistant_row_is_not_tool_use(self):
        p = self.jsonl("c.jsonl", [
            {"type": "assistant", "timestamp": "2026-08-21T00:00:10Z", "message": {"stop_reason": "tool_use"}},
            {"type": "user", "timestamp": "2026-08-21T00:00:15Z"},
            {"type": "assistant", "timestamp": "2026-08-21T00:00:20Z", "message": {"stop_reason": "end_turn"}},
        ])
        self.assertEqual(relay.claude_done_ts(p, 0), epoch("2026-08-21T00:00:20Z"))
        self.assertIsNone(relay.claude_done_ts(p, epoch("2026-08-21T00:00:20Z")), "not after `since`")
        busy = self.jsonl("busy.jsonl", [
            {"type": "assistant", "timestamp": "2026-08-21T00:00:20Z", "message": {"stop_reason": "tool_use"}}])
        self.assertIsNone(relay.claude_done_ts(busy, 0))
        self.assertIsNone(relay.claude_done_ts(os.path.join(self.tmp.name, "missing"), 0))

    def test_codex_turn_is_done_on_task_complete_after_the_last_task_started(self):
        p = self.jsonl("x.jsonl", [
            {"type": "event_msg", "timestamp": "2026-08-21T00:00:10Z", "payload": {"type": "task_started"}},
            {"type": "event_msg", "timestamp": "2026-08-21T00:00:30Z", "payload": {"type": "task_complete"}},
        ])
        self.assertEqual(relay.codex_done_ts(p, 0), epoch("2026-08-21T00:00:30Z"))
        again = self.jsonl("y.jsonl", [
            {"type": "event_msg", "timestamp": "2026-08-21T00:00:30Z", "payload": {"type": "task_complete"}},
            {"type": "event_msg", "timestamp": "2026-08-21T00:00:40Z", "payload": {"type": "task_started"}},
        ])
        self.assertIsNone(relay.codex_done_ts(again, 0), "a new turn started after the completion")


class TranscriptParsers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aipair-parsers.")

    def tearDown(self):
        self.tmp.cleanup()

    def jsonl(self, name, rows):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write((json.dumps(r) if not isinstance(r, str) else r) + "\n")
        return p

    def test_claude(self):
        p = self.jsonl("c.jsonl", [
            "garbage line",
            {"type": "user", "timestamp": "t1", "message": {"content": "<system-reminder>hidden</system-reminder>"}},
            {"type": "user", "timestamp": "t2", "message": {"content": "please review"}},
            {"type": "assistant", "timestamp": "t3", "message": {"content": [
                {"type": "text", "text": "looking"}, {"type": "tool_use", "name": "Bash"}]}},
            {"type": "progress", "timestamp": "t4"},
            {"type": "assistant", "timestamp": "t5", "message": {"content": [{"type": "text", "text": ""}]}},
        ])
        self.assertEqual(peerlog.parse_claude(p, False), [("t2", "user", "please review"), ("t3", "assistant", "looking")])
        self.assertEqual(peerlog.parse_claude(p, True)[1], ("t3", "assistant", "looking\n· tool:Bash"))

    def test_codex(self):
        p = self.jsonl("x.jsonl", [
            {"timestamp": "t0", "type": "session_meta", "payload": {"cwd": "/x"}},
            {"timestamp": "t1", "type": "response_item", "payload": {"type": "message", "role": "user",
                                                                     "content": [{"type": "input_text", "text": "<environment_context>…"}]}},
            {"timestamp": "t2", "type": "response_item", "payload": {"type": "message", "role": "user",
                                                                     "content": [{"type": "input_text", "text": "review this"}]}},
            {"timestamp": "t3", "type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}},
            {"timestamp": "t4", "type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                                     "content": [{"type": "output_text", "text": "完了です"}]}},
            {"timestamp": "t5", "type": "response_item", "payload": {"type": "message", "role": "system", "content": "x"}},
        ])
        self.assertEqual(peerlog.parse_codex(p, False), [("t2", "user", "review this"), ("t4", "assistant", "完了です")])
        self.assertEqual(peerlog.parse_codex(p, True)[1], ("t3", "assistant", "· tool:exec_command"))

    def test_ts_key_and_meta_filter(self):
        self.assertEqual(peerlog._ts_key("2026-08-21T00:00:20Z"), epoch("2026-08-21T00:00:20Z"))
        self.assertEqual(peerlog._ts_key("nope"), 0.0)
        self.assertTrue(peerlog._is_meta("  <SYSTEM-reminder>x", peerlog.CLAUDE_META_PREFIXES))
        self.assertFalse(peerlog._is_meta("system reminder text", peerlog.CLAUDE_META_PREFIXES))


class CorelibStandalone(unittest.TestCase):
    """aipairlib.corelib must load with NO aipair-relay dependency (that decoupling is the point
    of the split): a fresh SourceFileLoader of just corelib exposes the pure helpers."""
    def test_corelib_loads_and_works_without_relay(self):
        loader = importlib.machinery.SourceFileLoader("corelib_standalone", os.path.join(BIN, "aipairlib", "corelib.py"))
        core = importlib.util.module_from_spec(importlib.util.spec_from_loader("corelib_standalone", loader))
        loader.exec_module(core)   # would raise if it referenced relay-only globals
        self.assertEqual(core.parse_version("2.1.238 (Claude Code)"), "2.1.238")
        self.assertTrue(core.hit_stop(["[AIPAIR_REVIEW_OK]"], ["[AIPAIR_REVIEW_OK]"]))   # head-exact
        self.assertFalse(core.hit_stop(["まだ [AIPAIR_REVIEW_OK] ではない"], ["[AIPAIR_REVIEW_OK]"]))
        self.assertEqual(core.scrub_output("a\x00b"), "a b")
        self.assertEqual(core.schema_probe("claude", [])[0], "unverified")   # pure, no relay needed
        self.assertEqual(core.TESTED_VERSIONS, relay.TESTED_VERSIONS)
        self.assertEqual(core.oneline("a   b\n c", 100), "a b c")   # generic text helper (moved from relay)
        self.assertEqual(core.oneline("x" * 10, 5), "xxxx…")
        self.assertIs(relay.oneline, relay.corelib.oneline)          # relay re-exports the corelib helper

    def test_relay_reexports_are_the_corelib_objects(self):
        # relay.X is bound to the corelib implementation (not a stale copy)
        self.assertIs(relay.parse_version, relay.corelib.parse_version)
        self.assertIs(relay.version_gate, relay.corelib.version_gate)
        self.assertIs(relay.schema_probe, relay.corelib.schema_probe)
        self.assertIs(relay.schema_gate, relay.corelib.schema_gate)


class LoglibStandalone(unittest.TestCase):
    """aipairlib.loglib loads with only peer-log (no aipair-relay), and relay re-exports it."""
    def test_loglib_loads_and_works_without_relay(self):
        self.assertTrue(_imports_without_relay("loglib", "claude_done_ts", "turn_texts", "read_records"))
        self.assertEqual(relay.loglib.read_records("/no/such/file"), [])
        self.assertEqual(relay.loglib.make_fragment("hello world", 5), relay.make_fragment("hello world", 5))

    def test_relay_reexports_are_the_loglib_objects(self):
        self.assertIs(relay.claude_done_ts, relay.loglib.claude_done_ts)
        self.assertIs(relay.turn_texts, relay.loglib.turn_texts)
        self.assertIs(relay.read_records, relay.loglib.read_records)

    def test_cli_module_is_standalone(self):
        # P2-1 増分6: argparse + AIPAIR_* env defaults live in a standalone cli module; relay
        # re-exports _env_* / ENV_USED / build_parser so EnvHelpers and the launch forms are
        # unchanged.
        self.assertTrue(_imports_without_relay("cli", "build_parser", "_env_str", "_env_int"))
        self.assertIs(relay._env_str, relay.cli._env_str)
        self.assertIs(relay.ENV_USED, relay.cli.ENV_USED)
        # build_parser produces the real parser (defaults resolve from AIPAIR_* / sentinels)
        a = relay.build_parser("x").parse_args([])
        self.assertEqual(a.stop, "[AIPAIR_REVIEW_OK]")
        self.assertEqual(a.plan_ok, "[AIPAIR_PLAN_APPROVED]")
        # endless BLOCKED/HUMAN_REQUIRED sentinel (社長指示 2026-08-24 / _reference/new-task.md)
        self.assertEqual(a.human_required, "[AIPAIR_HUMAN_REQUIRED]")

    def test_human_required_sentinel_default_and_env_override(self):
        # 既定は [AIPAIR_HUMAN_REQUIRED]、env AIPAIR_HUMAN_REQUIRED で上書きできる（他 sentinel と同経路）。
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIPAIR_HUMAN_REQUIRED", None)
            self.assertEqual(relay.build_parser("x").parse_args([]).human_required,
                             "[AIPAIR_HUMAN_REQUIRED]")
        with mock.patch.dict(os.environ, {"AIPAIR_HUMAN_REQUIRED": "[CUSTOM_HR]"}):
            self.assertEqual(relay.build_parser("x").parse_args([]).human_required, "[CUSTOM_HR]")
        # 明示フラグは env より優先
        with mock.patch.dict(os.environ, {"AIPAIR_HUMAN_REQUIRED": "[CUSTOM_HR]"}):
            self.assertEqual(
                relay.build_parser("x").parse_args(["--human-required", "[FLAG_HR]"]).human_required,
                "[FLAG_HR]")

    def test_log_lock_module_is_standalone(self):
        # P2-1 増分4: the log-locking cluster is a standalone module; relay re-exports the lock/
        # refresh functions so codex-follow's relay.lock_codex / relay.refresh_codex_lock etc.
        # are unchanged.
        self.assertTrue(_imports_without_relay("log_lock", "lock_codex", "lock_claude",
                                               "refresh_claude_lock", "read_codex_since"))
        self.assertIs(relay.lock_codex, relay.log_lock.lock_codex)
        self.assertIs(relay.refresh_claude_lock, relay.log_lock.refresh_claude_lock)

    def test_gate_module_is_standalone(self):
        # P2-1 増分3: the stop-gate runner is a standalone module (no relay dependency); relay
        # re-exports run_gate so call sites / GateRunner tests are unchanged.
        self.assertTrue(_imports_without_relay("gate", "run_gate"))
        self.assertIs(relay.run_gate, relay.gate.run_gate)

    def test_review_protocol_is_standalone_and_instructs_sentinel_at_head(self):
        # P2-1 増分2: the poke templates are a standalone pure module (no relay dependency), and
        # every stop/next/all-done/plan template instructs the sentinel ALONE on the first line.
        self.assertTrue(_imports_without_relay("review_protocol", "default_poke_codex",
                                               "endless_poke_codex_next", "plan_poke_codex",
                                               "plan_extra_comment"))
        rp = relay.review_protocol
        self.assertIn("【1行目】", rp.default_poke_codex("[AIPAIR_REVIEW_OK]"))
        self.assertIn("[AIPAIR_REVIEW_OK]", rp.default_poke_codex("[AIPAIR_REVIEW_OK]"))
        _codex_next = rp.endless_poke_codex_next("tasks/todo.md", "[AIPAIR_ALL_DONE]", "[AIPAIR_HUMAN_REQUIRED]")
        self.assertIn("[AIPAIR_ALL_DONE]", _codex_next)
        self.assertIn("[AIPAIR_HUMAN_REQUIRED]", _codex_next)          # §4: [!] のみ残なら HUMAN_REQUIRED
        self.assertIn("【1行目】", _codex_next)                          # シグナルは 1行目に単独
        self.assertIn("再指示しない", _codex_next)                       # [!] は AI では進められない→再指示禁止
        _pass = rp.endless_poke_claude_pass("tasks/todo.md", "[AIPAIR_NEXT]")
        _next = rp.endless_poke_claude_next("tasks/todo.md", "[AIPAIR_NEXT]")
        # §5: 実行不能を検出したら [ ]→[!]＋blocker、その後「他の [ ] 続行 or 手詰まりシグナル」まで
        # 両 Claude 文面が含む（_next も blocker 更新後に次選択へ遷移できる）。
        for txt in (_pass, _next):
            self.assertIn("[!]", txt)
            self.assertIn("blocker:", txt)
            self.assertIn("[AIPAIR_NEXT]", txt)
        # §9: endless 文面は「人間に伝言を頼むな」が [!]/HUMAN_REQUIRED エスカレーションを妨げないと明記。
        for txt in (_pass, _next, _codex_next):
            self.assertIn("妨げない", txt)
        # 通常レビュー往復の文面（§9 の対象外）は従来どおり明確化を足さない。
        self.assertNotIn("妨げない", rp.default_poke_codex("[AIPAIR_REVIEW_OK]"))
        self.assertIn("[AIPAIR_PLAN_APPROVED]", rp.plan_poke_codex("p.md", "[AIPAIR_PLAN_APPROVED]"))
        # relay re-exports the same objects (call sites unchanged)
        self.assertIs(relay.default_poke_codex, rp.default_poke_codex)
        self.assertIs(relay.plan_extra_comment, rp.plan_extra_comment)



class ResponseGateBehavior(unittest.TestCase):
    """P2-1: the poke/attribution gate — formerly the nested closures response_done / poke_noshow
    inside main(), which no test could reach — is now ResponseGate in state_machine.py, owning the
    probe lifecycle with injected pure deps (find_poke_ts / codex_response_complete /
    claude_response_attributed) + a fake clock. So the fail-closed attribution invariants (P1-2 /
    P1-3) get direct unit coverage: a turn_id-less codex turn is rejected, never position-guessed."""
    def _gate(self, *, find=None, codex=None, claude=None, now=1000.0):
        from aipairlib.state_machine import ResponseGate
        self.dims = []
        self.warns = []
        self.clock = [now]
        tracked = {"claude": "/c.jsonl", "codex": "/x.jsonl"}
        self.tracked = tracked
        return ResponseGate(
            tracked,
            find or (lambda *a: 100.0),
            codex or (lambda *a: (None, None)),
            claude or (lambda *a: True),
            lambda m: self.dims.append(m),
            lambda m, bell=False: self.warns.append((m, bell)),
            clock=lambda: self.clock[0],
        )

    def test_standalone_import_and_relay_reexport(self):
        self.assertTrue(_imports_without_relay("state_machine", "ResponseGate", "decide_plan_action",
                                               "decide_question_action"))
        # P2-1: 判定核は plan_flow / question_flow の named module に在り、state_machine が再エクスポート
        self.assertTrue(_imports_without_relay("plan_flow", "decide_plan_action"))
        self.assertTrue(_imports_without_relay("question_flow", "decide_question_action"))
        from aipairlib.state_machine import ResponseGate, decide_plan_action, decide_question_action
        from aipairlib.plan_flow import decide_plan_action as pf
        from aipairlib.question_flow import decide_question_action as qf
        self.assertIs(decide_plan_action, pf)          # state_machine は plan_flow から re-export
        self.assertIs(decide_question_action, qf)
        self.assertIs(relay.ResponseGate, ResponseGate)
        self.assertIs(relay.decide_plan_action, pf)
        self.assertIs(relay.decide_question_action, qf)

    def test_arm_sets_lifecycle_and_clear_disables(self):
        g = self._gate(now=42.0)
        g.arm("NONCE")
        self.assertEqual((g.probe, g.probe_ts_cache, g.probe_sent_at), ("NONCE", None, 42.0))
        g.clear()
        self.assertIsNone(g.probe)

    def test_no_probe_passes_raw_done_through(self):
        # dialog-path delivery (clear()) → gate disabled → raw completion flows unchanged; and an
        # un-armed gate never fires a no-show.
        g = self._gate()
        g.clear()
        self.assertEqual(g.response_done("codex", "/x.jsonl", 500.0), 500.0)
        self.assertFalse(g.noshow("codex"))

    def test_codex_turn_id_returns_response_completion_not_raw(self):
        # turn_id pairs → the RETURNED ts is the response task's own completion (comp), and the
        # texts-window anchor advances to the response task start — never raw_done (a later task).
        g = self._gate(find=lambda *a: 100.0, codex=lambda *a: (150.0, 175.0))
        g.arm("NONCE")
        self.assertEqual(g.response_done("codex", "/x.jsonl", 999.0), 175.0)
        self.assertEqual(g.probe_ts_cache, 150.0)

    def test_codex_missing_turn_id_is_fail_closed(self):
        # anchor None (turn_id absent / nonce user-item not found) → reject, and NEVER a position
        # fallback. This is the P1-3 invariant that keeps an unattributable turn out of auto stop.
        g = self._gate(find=lambda *a: 100.0, codex=lambda *a: (None, None))
        g.arm("NONCE")
        self.assertIsNone(g.response_done("codex", "/x.jsonl", 999.0))
        self.assertTrue(any("自律判定に使わず" in d for d in self.dims))

    def test_codex_response_task_incomplete_rejects(self):
        g = self._gate(find=lambda *a: 100.0, codex=lambda *a: (150.0, None))
        g.arm("NONCE")
        self.assertIsNone(g.response_done("codex", "/x.jsonl", 999.0))

    def test_nonce_not_yet_delivered_waits(self):
        # find_poke_ts None → the nonce has not landed in the log yet → None (noshow owns the
        # deadline); no reject log, because nothing is wrong yet.
        g = self._gate(find=lambda *a: None)
        g.arm("NONCE")
        self.assertIsNone(g.response_done("codex", "/x.jsonl", 999.0))
        self.assertEqual(self.dims, [])

    def test_claude_ancestry_and_ordering(self):
        g = self._gate(find=lambda *a: 100.0, claude=lambda *a: True)
        g.arm("NONCE")
        self.assertEqual(g.response_done("claude", "/c.jsonl", 200.0), 200.0)
        g.probe_ts_cache = 100.0    # completion at/under the nonce ts → reject
        self.assertIsNone(g.response_done("claude", "/c.jsonl", 100.0))

    def test_claude_unattributed_rejects(self):
        g = self._gate(find=lambda *a: 100.0, claude=lambda *a: False)
        g.arm("NONCE")
        self.assertIsNone(g.response_done("claude", "/c.jsonl", 200.0))

    def test_reject_dedups_per_completion_value(self):
        g = self._gate(find=lambda *a: 100.0, claude=lambda *a: False)
        g.arm("NONCE")
        g.response_done("claude", "/c.jsonl", 200.0)
        g.response_done("claude", "/c.jsonl", 200.0)     # same value → not dimmed twice
        self.assertEqual(sum("棄却" in d for d in self.dims), 1)
        g.response_done("claude", "/c.jsonl", 300.0)     # new value → dimmed again
        self.assertEqual(sum("棄却" in d for d in self.dims), 2)

    def test_noshow_silent_when_unarmed_or_within_deadline(self):
        g = self._gate(now=1000.0)
        self.assertFalse(g.noshow("codex"))              # not armed
        g.arm("NONCE")
        self.clock[0] = 1000.0 + g.POKE_NOSHOW - 1       # just under the deadline
        self.assertFalse(g.noshow("codex"))
        self.assertEqual(self.warns, [])

    def test_noshow_fires_past_deadline_when_nonce_absent(self):
        g = self._gate(now=1000.0, find=lambda *a: None)
        g.arm("NONCE")
        self.clock[0] = 1000.0 + g.POKE_NOSHOW + 1
        self.assertTrue(g.noshow("codex"))
        self.assertEqual(len(self.warns), 1)
        self.assertTrue(self.warns[0][1])                # bell=True
        self.assertIn("未配達", self.warns[0][0])

    def test_noshow_stands_down_if_nonce_appears_late(self):
        # past the deadline but the nonce has since landed → not a no-show; it caches the ts.
        g = self._gate(now=1000.0, find=lambda *a: 1234.0)
        g.arm("NONCE")
        self.clock[0] = 1000.0 + g.POKE_NOSHOW + 1
        self.assertFalse(g.noshow("codex"))
        self.assertEqual(g.probe_ts_cache, 1234.0)
        self.assertEqual(self.warns, [])

class ResponseAttribution(unittest.TestCase):
    """The loglib functions that decide whether a completed turn is the answer to OUR poke —
    the source of truth for delivery. Fixtures are real jsonl / rollout shapes."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aipair-attrib.")
    def tearDown(self):
        self.tmp.cleanup()
    def w(self, name, rows):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        return p

    # ---- Codex: turn_id pairing -------------------------------------------------
    def _cx_user(self, ts, text, turn=None):
        p = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
        if turn is not None:
            p["internal_chat_message_metadata_passthrough"] = {"turn_id": turn}
        return {"timestamp": ts, "type": "response_item", "payload": p}
    def _ev(self, ts, kind, turn):
        return {"timestamp": ts, "type": "event_msg", "payload": {"type": kind, "turn_id": turn}}

    def test_codex_turn_id_match(self):
        p = self.w("cx.jsonl", [
            self._ev("2026-08-21T00:00:01Z", "task_started", "T1"),
            self._cx_user("2026-08-21T00:00:02Z", "review relay-id:abcd", "T1"),
            self._ev("2026-08-21T00:00:09Z", "task_complete", "T1")])
        anchor, comp = relay.codex_response_complete(p, "relay-id:abcd")
        self.assertEqual(anchor, epoch("2026-08-21T00:00:01Z"))
        self.assertEqual(comp, epoch("2026-08-21T00:00:09Z"))

    def test_codex_ignores_completion_of_a_different_turn(self):
        p = self.w("cx.jsonl", [
            self._ev("2026-08-21T00:00:01Z", "task_started", "T1"),
            self._cx_user("2026-08-21T00:00:02Z", "review relay-id:abcd", "T1"),
            self._ev("2026-08-21T00:00:05Z", "task_complete", "T0")])   # a foreign turn's completion
        anchor, comp = relay.codex_response_complete(p, "relay-id:abcd")
        self.assertEqual(anchor, epoch("2026-08-21T00:00:01Z")); self.assertIsNone(comp)

    def test_codex_not_yet_complete(self):
        p = self.w("cx.jsonl", [
            self._ev("2026-08-21T00:00:01Z", "task_started", "T1"),
            self._cx_user("2026-08-21T00:00:02Z", "x relay-id:abcd", "T1")])
        anchor, comp = relay.codex_response_complete(p, "relay-id:abcd")
        self.assertEqual(anchor, epoch("2026-08-21T00:00:01Z")); self.assertIsNone(comp)

    def test_codex_metadata_missing_is_fail_closed_by_default(self):
        # P1-3: no turn_id on the nonce user → attribution can't be confirmed. The position
        # heuristic can misattribute a queued turn, so by DEFAULT it is NOT used — fail-closed.
        p = self.w("cx.jsonl", [
            self._ev("2026-08-21T00:00:01Z", "task_started", "T1"),
            self._cx_user("2026-08-21T00:00:03Z", "x relay-id:abcd"),   # no turn_id
            self._ev("2026-08-21T00:00:09Z", "task_complete", "T1")])
        self.assertEqual(relay.codex_response_complete(p, "relay-id:abcd"), (None, None),
                         "default = fail-closed (no position heuristic for autonomous decisions)")
        # only an EXPLICIT compatibility mode opts into the position fallback
        anchor, comp = relay.codex_response_complete(p, "relay-id:abcd", allow_position_fallback=True)
        self.assertEqual(anchor, epoch("2026-08-21T00:00:01Z"))
        self.assertEqual(comp, epoch("2026-08-21T00:00:09Z"))

    def test_codex_nonce_absent(self):
        p = self.w("cx.jsonl", [self._ev("2026-08-21T00:00:01Z", "task_started", "T1")])
        self.assertEqual(relay.codex_response_complete(p, "relay-id:zzz"), (None, None))

    def test_response_done_gate_never_uses_the_position_fallback(self):
        # P1-3 (integration guard): the AUTONOMOUS attribution gate (ResponseGate.response_done,
        # P2-1: extracted to state_machine.py) must call codex_response_complete WITHOUT the
        # position fallback — so a turn_id-less, unattributable codex turn can never be accepted as
        # a completion and drive an auto stop / review-forward / question-answer / plan
        # auto-approval. The fallback stays opt-in for diagnostics only. Neither the gate module nor
        # the relay that wires it may ever enable the fallback.
        with open(os.path.join(BIN, "aipairlib", "state_machine.py"), encoding="utf-8") as fh:
            gate_src = fh.read()
        with open(os.path.join(BIN, "aipairlib", "relay.py"), encoding="utf-8") as fh:
            relay_src = fh.read()
        self.assertIn("anchor, comp = self._codex_response_complete(path, self.probe)", gate_src,
                      "the gate must call it plainly (no fallback)")
        for src in (gate_src, relay_src):
            self.assertNotIn("allow_position_fallback=True", src, "the fallback must never be enabled")
            self.assertNotIn("allow_position_fallback=(", src, "the fallback must never be enabled")

    # ---- Claude: parentUuid ancestry -------------------------------------------
    def _cl_user(self, uuid, parent, text, ts="2026-08-21T00:00:05Z"):
        return {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts, "message": {"content": text}}
    def _cl_asst(self, uuid, parent, text="ok"):
        return {"type": "assistant", "uuid": uuid, "parentUuid": parent,
                "message": {"content": [{"type": "text", "text": text}]}}

    def test_claude_ancestor_match(self):
        p = self.w("cl.jsonl", [
            self._cl_user("u1", None, "review relay-id:abcd"),
            self._cl_asst("a1", "u1"), self._cl_asst("a2", "a1")])   # a2's chain a2→a1→u1
        self.assertTrue(relay.claude_response_attributed(p, "relay-id:abcd"))

    def test_claude_unrelated_last_assistant(self):
        p = self.w("cl.jsonl", [
            self._cl_user("u1", None, "review relay-id:abcd"),
            self._cl_asst("a1", "u1"),
            self._cl_user("u2", None, "a different, later human turn"),
            self._cl_asst("a2", "u2")])   # a2's chain a2→u2 (not u1)
        self.assertFalse(relay.claude_response_attributed(p, "relay-id:abcd"))

    def test_claude_cyclic_chain_terminates(self):
        # a1→a2→a1 cycle, nonce not in it → returns False without hanging
        p = self.w("cl.jsonl", [
            self._cl_user("u1", None, "review relay-id:abcd"),
            self._cl_asst("a1", "a2"), self._cl_asst("a2", "a1")])
        self.assertFalse(relay.claude_response_attributed(p, "relay-id:abcd"))

    def test_claude_nonce_absent(self):
        p = self.w("cl.jsonl", [self._cl_asst("a1", None)])
        self.assertFalse(relay.claude_response_attributed(p, "relay-id:zzz"))

    def _compact(self, uuid, logical):
        # Claude Code's context-compaction root: parentUuid is None (the chain is SEVERED), but
        # logicalParentUuid points to the TRUE pre-compaction ancestor. Missing → fail-closed.
        d = {"type": "system", "subtype": "compact_boundary", "uuid": uuid, "parentUuid": None,
             "timestamp": "2026-08-21T00:10:00Z"}
        if logical is not None:
            d["logicalParentUuid"] = logical
        return d

    def test_claude_attribution_bridges_a_compaction_boundary(self):
        # poke → compaction → response. parentUuid is severed at the boundary; logicalParentUuid
        # points back into the pre-compaction thread, so the ancestry walk still reaches the
        # nonce. Real 2026-08-23 loop-stall bug.
        p = self.w("cl.jsonl", [
            self._cl_user("u1", None, "next task relay-id:abcd"),      # nonce (pre-compaction)
            self._cl_asst("pre", "u1"),                                # pre-compaction response
            self._compact("BND", "pre"),                               # logicalParentUuid → pre → u1
            self._cl_user("u2", "BND", "<summary continuation>"),
            self._cl_asst("a1", "u2"), self._cl_asst("a2", "a1")])     # a2→a1→u2→BND→(logical)pre→u1
        self.assertTrue(relay.claude_response_attributed(p, "relay-id:abcd"),
                        "a response after compaction reaches a pre-boundary poke via logicalParentUuid")

    def test_claude_bridge_follows_logicalParentUuid_not_line_position(self):
        # The nonce is on an OLD SIBLING branch that PRECEDES the boundary but is NOT its logical
        # ancestor. A position heuristic ("nonce before boundary → attributed") would wrongly say
        # True; following logicalParentUuid (→ the 'main' branch) correctly says False. This is
        # the mis-attribution Codex flagged in the first (line-position) implementation.
        p = self.w("cl.jsonl", [
            self._cl_user("sib", None, "an unrelated old branch relay-id:abcd"),  # sibling nonce
            self._cl_asst("main", None),                               # the real logical ancestor
            self._compact("BND", "main"),                              # logicalParentUuid → main (not sib)
            self._cl_user("u2", "BND", "<summary continuation>"),
            self._cl_asst("a1", "u2")])                                # a1→u2→BND→(logical)main→None
        self.assertFalse(relay.claude_response_attributed(p, "relay-id:abcd"),
                         "a sibling-branch nonce not on the logicalParentUuid chain is NOT attributed")

    def test_claude_bridge_fail_closed_without_logicalParentUuid(self):
        # A compact_boundary with NO logicalParentUuid severs the chain irrecoverably → the walk
        # stops at the boundary (fail-closed) rather than guessing.
        p = self.w("cl.jsonl", [
            self._cl_user("u1", None, "poke relay-id:abcd"),
            self._compact("BND", None),                                # no logicalParentUuid
            self._cl_user("u2", "BND", "<summary continuation>"),
            self._cl_asst("a1", "u2")])                                # a1→u2→BND→None (stops)
        self.assertFalse(relay.claude_response_attributed(p, "relay-id:abcd"),
                         "a boundary without logicalParentUuid fails closed, not open")

    def test_claude_no_boundary_means_no_bridge(self):
        # Without a compaction boundary, a severed chain (root parentUuid None, non-boundary)
        # does NOT bridge — the old strict behaviour is preserved.
        p = self.w("cl.jsonl", [
            self._cl_user("u1", None, "poke relay-id:abcd"),
            {"type": "system", "uuid": "S", "parentUuid": None, "timestamp": "2026-08-21T00:10:00Z"},  # NOT a compact_boundary
            self._cl_user("u2", "S", "later"),
            self._cl_asst("a1", "u2")])                                    # a1→u2→S (S is not a boundary)
        self.assertFalse(relay.claude_response_attributed(p, "relay-id:abcd"))

    # ---- find_poke_ts / turn_texts ---------------------------------------------
    def test_find_poke_ts_per_agent_and_missing(self):
        cl = self.w("cl.jsonl", [self._cl_user("u1", None, "hi relay-id:abcd", ts="2026-08-21T00:00:05Z")])
        self.assertEqual(relay.find_poke_ts("claude", cl, "relay-id:abcd"), epoch("2026-08-21T00:00:05Z"))
        cx = self.w("cx.jsonl", [self._cx_user("2026-08-21T00:00:07Z", "hi relay-id:abcd", "T1")])
        self.assertEqual(relay.find_poke_ts("codex", cx, "relay-id:abcd"), epoch("2026-08-21T00:00:07Z"))
        self.assertIsNone(relay.find_poke_ts("codex", cx, "relay-id:nope"))

    def test_turn_texts_agent_and_time_window(self):
        p = self.w("cx.jsonl", [
            {"timestamp": "2026-08-21T00:00:01Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "too early"}]}},
            {"timestamp": "2026-08-21T00:00:10Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "in window"}]}},
            {"timestamp": "2026-08-21T00:00:30Z", "type": "response_item",
             "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "not assistant"}]}}])
        since = epoch("2026-08-21T00:00:05Z"); until = epoch("2026-08-21T00:00:12Z")
        self.assertEqual(relay.turn_texts("codex", p, since, until), ["in window"])
        # the +2s grace at the upper bound is included
        self.assertIn("in window", relay.turn_texts("codex", p, since, epoch("2026-08-21T00:00:08Z")))


class EndlessScenarios(unittest.TestCase):
    """§11 Case1-6（社長指示 2026-08-24 / `_reference/new-task.md`）を **scripted fake で単一
    `StateMachine.run()` を実駆動**し、分類推移・sentinel 処理・配達先/文面・最終 exit を固定する。

    `_drive` が sg.guard=False・応答完了・turn_texts・task-list 分類・poke を scenario 順に返し、
    各ターン（Claude/Codex）を実際のループで処理させる。poke は (pane, 文面) の履歴で検証する。"""

    def _cls(self, body):
        return relay.state_machine.tasklist.classify(body)

    def _drive(self, start_side, startup_cls, turns):
        """turns = [(agent, [texts], cls_or_None), ...] を順に処理。cls はその Codex ターンの分類呼び出しが
        返す値（None は据え置き）。startup 分類は startup_cls。run() の code と poke の (pane, msg) 履歴を返す。"""
        import types
        sm = relay.state_machine
        a = types.SimpleNamespace(start_side=start_side, settle=0, poll=0, max_rounds=20, endless=True,
                                  task_list="tasks/todo.md", stop_side="codex", no_plan_review=True,
                                  no_question_relay=True, plan_rounds=5, question_rounds=5, plan_ok="[P]")
        rg = types.SimpleNamespace(response_done=lambda who, path, done: done, noshow=lambda who: False,
                                   arm=lambda nonce: None, probe=None, probe_ts_cache=0.0)
        sg = types.SimpleNamespace(guard=lambda: False)
        box = {"cls": startup_cls}
        turn_iter = iter(turns)

        def _turn_texts(who, *aa, **kk):
            agent, texts, cls = next(turn_iter)
            self.assertEqual(agent, who, "turn script mismatch")
            if cls is not None:
                box["cls"] = cls
            return list(texts)

        machine = sm.StateMachine(
            a, panes={"claude": "%1", "codex": "%2"}, own="%0", cwd="/x",
            tracked={"claude": "c.jsonl", "codex": "x.jsonl"}, claude_seen=set(), codex_seen=set(),
            baseline=0.0, sg=sg, rg=rg, bw=60, poke_codex="POKE_CODEX", poke_codex_next="POKE_CODEX_NEXT",
            poke_claude="POKE_CLAUDE", poke_claude_pass="POKE_CLAUDE_PASS", poke_claude_next="POKE_CLAUDE_NEXT",
            stop_phrases=["[AIPAIR_REVIEW_OK]"], next_ask_phrases=["[AIPAIR_NEXT]"],
            all_done_phrases=["[AIPAIR_ALL_DONE]"], human_required_phrases=["[AIPAIR_HUMAN_REQUIRED]"])
        with mock.patch.object(sm.tasklist, "load_or_exit", side_effect=lambda *a, **k: box["cls"]), \
             mock.patch.object(sm, "set_pane_title"), \
             mock.patch.object(sm, "claude_done_ts", return_value=100.0), \
             mock.patch.object(sm, "codex_done_ts", return_value=100.0), \
             mock.patch.object(sm, "claude_matches_pane", return_value=True), \
             mock.patch.object(sm, "turn_texts", side_effect=_turn_texts), \
             mock.patch.object(sm, "poke", return_value=object()) as poke_mock, \
             mock.patch("time.sleep"):
            code = machine.run()
        pokes = [(c.args[0], c.args[1]) for c in poke_mock.call_args_list]
        return code, pokes

    # 配達先/文面の定数（_drive の poke_* に対応）
    TO_CODEX_NEXT = ("%2", "POKE_CODEX_NEXT")     # Claude→Codex（次タスク依頼）
    TO_CLAUDE_NEXT = ("%1", "POKE_CLAUDE_NEXT")   # Codex→Claude（次タスク着手）

    def test_case1_ready_selects_and_continues_then_all_done(self):
        # [ ]+[x]（READY）: Claude [AIPAIR_NEXT] → Codex が `- [ ] A` を選択 → Claude へ POKE_CLAUDE_NEXT
        # → 継続。次ラウンドで ALL_DONE → exit 0。
        ready = self._cls("- [ ] A\n- [x] b\n")
        alldone = self._cls("- [x] A\n- [x] b\n")
        code, pokes = self._drive("claude", ready, [
            ("claude", ["[AIPAIR_NEXT]"], None),
            ("codex", ["- [ ] A"], ready),          # Codex が着手可 A を選択（no-progress round1・継続）
            ("claude", ["[AIPAIR_NEXT]"], None),
            ("codex", ["[AIPAIR_ALL_DONE]"], alldone),   # → exit 0
        ])
        self.assertEqual(code, 0)
        self.assertEqual(pokes, [self.TO_CODEX_NEXT, self.TO_CLAUDE_NEXT, self.TO_CODEX_NEXT])

    def test_case2_all_done_through_loop_exits_0_no_poke(self):
        ready = self._cls("- [ ] A\n")
        code, pokes = self._drive("codex", ready, [("codex", ["[AIPAIR_ALL_DONE]"], self._cls("- [x] A\n"))])
        self.assertEqual(code, 0)
        self.assertEqual(pokes, [])                  # 終端の前に poke しない

    def test_case3_human_required_through_loop_exits_8_no_poke(self):
        ready = self._cls("- [ ] A\n")
        blocked = self._cls("- [!] human\n  - blocker: repo admin\n")
        code, pokes = self._drive("codex", ready, [("codex", ["[AIPAIR_HUMAN_REQUIRED]"], blocked)])
        self.assertEqual(code, relay.state_machine.EXIT_BLOCKED)
        self.assertEqual(pokes, [])
        self.assertIn("再指示しない", relay.review_protocol.endless_poke_codex_next(
            "t.md", "[AIPAIR_ALL_DONE]", "[AIPAIR_HUMAN_REQUIRED]"))

    def test_case4_ready_then_human_required_same_run(self):
        # 同一 run() 内で READY→BLOCKED 遷移: round1 は A を選択して継続、round2 は A 完了で BLOCKED →
        # [AIPAIR_HUMAN_REQUIRED] を分類が支持 → exit 8。
        ready = self._cls("- [ ] A\n- [!] human\n  - blocker: approval\n")
        blocked = self._cls("- [x] A\n- [!] human\n  - blocker: approval\n")
        code, pokes = self._drive("claude", ready, [
            ("claude", ["[AIPAIR_NEXT]"], None),
            ("codex", ["- [ ] A"], ready),            # READY: A を選択・継続
            ("claude", ["[AIPAIR_NEXT]"], None),
            ("codex", ["[AIPAIR_HUMAN_REQUIRED]"], blocked),   # BLOCKED: → exit 8
        ])
        self.assertEqual(code, relay.state_machine.EXIT_BLOCKED)
        self.assertEqual(pokes, [self.TO_CODEX_NEXT, self.TO_CLAUDE_NEXT, self.TO_CODEX_NEXT])

    def test_case5_no_progress_through_loop_exits_8(self):
        # 毎ラウンド Claude [AIPAIR_NEXT]→Codex が同じ `- [ ] A` を割当（task-list 不変）を 3 ラウンド実駆動。
        # 同一識別子＋snapshot hash 不変が 3 回連続で no-progress → exit 8。配達履歴（pane/文面の順序）を固定。
        ready = self._cls("- [ ] A\n- [x] b\n")
        code, pokes = self._drive("claude", ready, [
            ("claude", ["[AIPAIR_NEXT]"], None), ("codex", ["- [ ] A"], ready),   # round1
            ("claude", ["[AIPAIR_NEXT]"], None), ("codex", ["- [ ] A"], ready),   # round2
            ("claude", ["[AIPAIR_NEXT]"], None), ("codex", ["- [ ] A"], ready),   # round3 → stop
        ])
        self.assertEqual(code, relay.state_machine.EXIT_BLOCKED)
        # Codex 向け 3 回（POKE_CODEX_NEXT）・Claude 向け 2 回（POKE_CLAUDE_NEXT）＝3 回目以降 Claude へ配達しない
        self.assertEqual(pokes, [self.TO_CODEX_NEXT, self.TO_CLAUDE_NEXT,
                                 self.TO_CODEX_NEXT, self.TO_CLAUDE_NEXT,
                                 self.TO_CODEX_NEXT])

    def test_case6_ready_rejects_human_required_then_reselects(self):
        # [!] があっても着手可 [ ] があれば READY: Codex が [AIPAIR_HUMAN_REQUIRED] を出しても分類 READY が
        # 支持せず reject。**Claude へは送らず Codex に選択を再要求**し、Codex が `- [ ] A` を選択して初めて
        # Claude へ配達する。誤 sentinel で exit 8 にしない。
        ready = self._cls("- [ ] A\n- [!] human\n  - blocker: x\n")
        alldone = self._cls("- [x] A\n- [x] done\n")
        code, pokes = self._drive("claude", ready, [
            ("claude", ["[AIPAIR_NEXT]"], None),
            ("codex", ["[AIPAIR_HUMAN_REQUIRED]"], ready),   # READY → reject → Codex へ再要求（Claude へ送らない）
            ("codex", ["- [ ] A"], ready),                   # Codex が着手可 A を選択 → Claude へ配達
            ("claude", ["[AIPAIR_NEXT]"], None),
            ("codex", ["[AIPAIR_ALL_DONE]"], alldone),       # → exit 0
        ])
        self.assertEqual(code, 0)                    # 誤 HUMAN_REQUIRED では exit 8 にしない
        # reject は Codex へ再要求（TO_CODEX_NEXT）、選択後に初めて Claude へ配達（TO_CLAUDE_NEXT）
        self.assertEqual(pokes, [self.TO_CODEX_NEXT, self.TO_CODEX_NEXT, self.TO_CLAUDE_NEXT, self.TO_CODEX_NEXT])


if __name__ == "__main__":
    unittest.main(verbosity=2)
