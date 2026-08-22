#!/usr/bin/env python3
"""Fixture tests for the pure parts of aipair-relay and peer-log: stop-phrase detection,
env parsing, pane discovery, Claude's plan / question dialogs, turn-completion detection
and transcript parsing. No tmux, no agents, nothing under ~ is touched.
    python3 tests/relay-parsers.py
"""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, time, types, unittest
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


relay = load_module("relay_parsers_under_test", os.path.join(BIN, "aipair-relay"))
peerlog = relay.peerlog


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class HitStop(unittest.TestCase):
    def test_only_the_head_of_the_last_message_counts(self):
        self.assertTrue(relay.hit_stop(["確認します", "完了です。修正点はありません"], ["完了です"]))
        self.assertFalse(relay.hit_stop(["完了です", "まだ直す点があります"], ["完了です"]),
                         "an earlier narration message must not stop the loop")

    def test_mid_message_mention_beyond_100_chars_does_not_stop(self):
        text = "x" * 120 + " 完了です"
        self.assertFalse(relay.hit_stop([text], ["完了です"]))

    def test_any_of_several_phrases_and_markdown_noise(self):
        self.assertTrue(relay.hit_stop(["**LGTM** — ship it"], ["完了です", "LGTM"]))
        self.assertTrue(relay.hit_stop(["  完了です\n\n詳細…"], ["完了です"]), "whitespace is collapsed")

    def test_empty(self):
        self.assertFalse(relay.hit_stop([], ["完了です"]))
        self.assertFalse(relay.hit_stop(["完了です"], []))


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
    """Fake `tmux list-panes -F '#{pane_id}\\t#{pane_current_command}\\t#{pane_title}'` output."""
    out = "\n".join("\t".join(r) for r in rows) + "\n"
    return mock.patch.object(relay, "tmux", return_value=types.SimpleNamespace(stdout=out))


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


class PlanDialog(unittest.TestCase):
    def detect(self, screen):
        with mock.patch.object(relay, "capture_pane", return_value=screen), \
             mock.patch.object(relay, "newest_plan", return_value=None):
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
        with mock.patch.object(relay, "capture_pane", return_value=screen):
            return relay.detect_question_dialog("%0")

    def test_detects_chat_number_only_while_the_footer_is_the_last_line(self):
        self.assertEqual(self.detect(QUESTION_SCREEN), {"chat": "3"})
        self.assertIsNone(self.detect(QUESTION_SCREEN + "\n > composer\n"), "footer gone = dialog gone")
        self.assertIsNone(self.detect(QUESTION_SCREEN.replace("3. Chat about this", "3. Other")), "no chat action")
        self.assertIsNone(self.detect("Would you like to proceed?\n" + QUESTION_SCREEN), "plan dialog wins")

    def test_question_block_text(self):
        block = relay._question_block(QUESTION_SCREEN)
        self.assertIn("Pick a database", block)
        self.assertIn("1. Postgres", block)
        self.assertIn("2. SQLite", block)
        self.assertNotIn("Chat about this", block)
        self.assertNotIn("Submit", block)

    def test_single_question_without_tab_bar(self):
        screen = "\n".join(l for l in QUESTION_SCREEN.splitlines() if "Submit" not in l) + "\n"
        self.assertIn("Pick a database", relay._question_block(screen))
        self.assertIsNone(relay._question_block("no footer here\n1. a\n2. b\n"))

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
    """aipair-corelib must load with NO aipair-relay dependency (that decoupling is the point
    of the split): a fresh SourceFileLoader of just corelib exposes the pure helpers."""
    def test_corelib_loads_and_works_without_relay(self):
        loader = importlib.machinery.SourceFileLoader("corelib_standalone", os.path.join(BIN, "aipair-corelib"))
        core = importlib.util.module_from_spec(importlib.util.spec_from_loader("corelib_standalone", loader))
        loader.exec_module(core)   # would raise if it referenced relay-only globals
        self.assertEqual(core.parse_version("2.1.238 (Claude Code)"), "2.1.238")
        self.assertTrue(core.hit_stop(["完了です。"], ["完了です"]))
        self.assertEqual(core.scrub_output("a\x00b"), "a b")
        self.assertEqual(core.TESTED_VERSIONS, relay.TESTED_VERSIONS)

    def test_relay_reexports_are_the_corelib_objects(self):
        # relay.X is bound to the corelib implementation (not a stale copy)
        self.assertIs(relay.parse_version, relay.corelib.parse_version)
        self.assertIs(relay.version_gate, relay.corelib.version_gate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
