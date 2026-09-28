#!/usr/bin/env python3
"""Regression tests for the pane layout a freshly started pair gets.

`aipair` splits its window by percentage (bridge height / codex width). tmux applies a percentage to
the window as it is at that moment, and `new-session -d` does not size the window after the terminal
that is about to show it (80x24, or the size of whichever OTHER terminal is attached to the server,
or the pane `aipair` was typed in — it depends on the tmux version). Stretching the window afterwards
does not keep the proportions, so the pair came up with e.g. codex at 20%..40% instead of 28%
(2026-09-29). `aipair` therefore sizes the window to the terminal BEFORE splitting; these tests start
a real pair from a pseudo-terminal of a known size and read the result back from tmux.

Every test runs against its own PRIVATE tmux server (a unique -L socket, forced by a `tmux` shim on
PATH that is verified via #{socket_path} first), so a live pair is never touched. The agents are
started with `--version`, so no TUI comes up.

    python3 tests/pane-layout.py        (exit 0 = all passed)
"""
import fcntl
import os
import pty
import random
import re
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
AIPAIR = os.path.join(REPO, "bin", "aipair")
REAL_TMUX = shutil.which("tmux")


def split_percentages():
    """(bridge height %, codex width %) as written in bin/aipair — the test follows the source."""
    src = open(AIPAIR, encoding="utf-8").read()
    v = re.search(r"^BRIDGE=\$\(tmux split-window -v -l (\d+)% ", src, re.M)
    h = re.search(r"^CODEX=\$\(tmux split-window -h -l (\d+)% ", src, re.M)
    assert v and h, "split-window lines not found in bin/aipair"
    return int(v.group(1)), int(h.group(1))


BRIDGE_PCT, CODEX_PCT = split_percentages()


class Server:
    """One private tmux server + the shim that makes `aipair` use it."""

    def __init__(self, conf_lines=()):
        self.work = tempfile.mkdtemp(prefix="aipair-layout.")
        self.socket = "aipair-layout-%d-%d" % (os.getpid(), random.randint(0, 999999))
        self.proj = os.path.join(self.work, "proj")
        shim_dir = os.path.join(self.work, "bin")
        os.makedirs(self.proj)
        os.makedirs(shim_dir)
        self.conf = os.path.join(self.work, "tmux.conf")
        with open(self.conf, "w") as f:
            f.write("".join(line + "\n" for line in conf_lines))
        self.base = [REAL_TMUX, "-L", self.socket, "-f", self.conf]
        shim = os.path.join(shim_dir, "tmux")
        with open(shim, "w") as f:
            f.write("#!/usr/bin/env bash\nexec %s \"$@\"\n" % " ".join("'%s'" % a for a in self.base))
        os.chmod(shim, 0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TMUX", "TMUX_PANE", "AI_SELF", "AI_PEER", "BASH_ENV", "ENV")
                    and not k.startswith("AIPAIR_")}
        self.env.update(PATH=shim_dir + os.pathsep + os.path.join(REPO, "bin") + os.pathsep + self.env["PATH"],
                        TERM="xterm-256color", AIPAIR_CLAUDE_FLAGS="--version", AIPAIR_CODEX_FLAGS="--version")
        self.fds = []
        self.pids = []
        # Guardrail: prove the shim reaches the PRIVATE socket before anything else runs.
        self.tmux("new-session", "-d", "-s", "shim-probe", stdin=subprocess.DEVNULL)
        want = self.tmux("display-message", "-p", "-t", "shim-probe", "#{socket_path}")
        got = subprocess.run([shim, "display-message", "-p", "-t", "shim-probe", "#{socket_path}"],
                             capture_output=True, universal_newlines=True, env=self.env).stdout.strip()
        if not got or got != want or os.path.basename(got) != self.socket:
            self.close()
            raise RuntimeError("tmux shim not effective (got %r, want %r) — refusing to go on" % (got, want))
        self.default_window = self.window("shim-probe")    # what `new-session -d` gives without a terminal
        self.name = subprocess.run([AIPAIR, "name", self.proj], capture_output=True, universal_newlines=True,
                                   env=self.env, check=True).stdout.strip()

    def tmux(self, *args, **kw):
        r = subprocess.run(self.base + list(args), capture_output=True, universal_newlines=True, env=self.env, **kw)
        return r.stdout.strip()

    def window(self, session):
        out = self.tmux("list-windows", "-t", "=" + session, "-F", "#{window_width} #{window_height}")
        return tuple(int(x) for x in out.split()) if out else None

    def panes(self):
        out = self.tmux("list-panes", "-t", "=" + self.name, "-F", "#{pane_title}\t#{pane_width}\t#{pane_height}")
        res = {}
        for line in out.splitlines():
            title, w, h = line.split("\t")
            res[title.split()[0]] = (int(w), int(h))      # "bridge  (claude × codex …)" → "bridge"
        return res

    def attached(self):
        return self.tmux("list-clients", "-t", "=" + self.name, "-F", "#{client_name}") != ""

    def terminal(self, argv, cols, rows):
        """Run argv on a new pseudo-terminal of cols x rows; returns the master fd."""
        size = struct.pack("HHHH", rows, cols, 0, 0)
        pid, fd = pty.fork()
        if pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, size)
            os.chdir(self.work)
            os.execve(argv[0], argv, self.env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
        self.fds.append(fd)
        self.pids.append(pid)
        return fd

    def wait(self, cond, what, secs=30):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return
            self.drain(0.1)                      # keep the terminals drained, or the clients block
        raise AssertionError("timed out waiting for: %s (panes=%r window=%r)"
                             % (what, self.panes(), self.window(self.name)))

    def drain(self, secs):
        end = time.time() + secs
        while time.time() < end:
            ready, _, _ = select.select(self.fds, [], [], 0.1) if self.fds else ([], [], [])
            for fd in ready:
                try:
                    os.read(fd, 65536)
                except OSError:
                    self.fds.remove(fd)

    def pair_is_up(self):
        return len(self.panes()) == 3 and {"claude", "codex", "bridge"} <= set(self.panes())

    def close(self):
        subprocess.run(self.base + ["kill-server"], capture_output=True)      # -L: the private server only
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        for pid in self.pids:
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass
        try:
            os.remove(os.path.join(os.environ.get("TMUX_TMPDIR", "/tmp"), "tmux-%d" % os.getuid(), self.socket))
        except OSError:
            pass
        shutil.rmtree(self.work, ignore_errors=True)


class PaneLayout(unittest.TestCase):
    def setUp(self):
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.close()

    def server(self, conf_lines=()):
        s = Server(conf_lines)
        self.servers.append(s)
        return s

    def other_terminal(self, s, cols, rows):
        """A terminal of another size attached to ANOTHER session, and the most recently active one."""
        fd = s.terminal(s.base + ["new-session", "-s", "other", "-c", s.work], cols, rows)
        s.wait(lambda: s.window("other") is not None, "the other session")
        os.write(fd, b"true\r")
        s.drain(0.5)

    def assert_layout(self, s, cols, rows, status_lines=1):
        win = (cols, rows - status_lines)
        self.assertEqual(s.window(s.name), win, "window is the terminal minus the status line(s)")
        p = s.panes()
        self.assertEqual(p["codex"][0], win[0] * CODEX_PCT // 100, "codex width is %d%% of the window" % CODEX_PCT)
        self.assertEqual(p["bridge"][1], win[1] * BRIDGE_PCT // 100, "bridge height is %d%% of the window" % BRIDGE_PCT)
        self.assertEqual(p["bridge"][0], win[0], "bridge spans the window")
        self.assertEqual(p["claude"][0] + 1 + p["codex"][0], win[0], "claude | codex fill the width")
        self.assertEqual(s.tmux("show-options", "-w", "-t", s.name + ":", "window-size"), "",
                         "the window is not left pinned to a manual size")

    def start_from_terminal(self, s, cols, rows):
        fd = s.terminal([AIPAIR, s.proj], cols, rows)
        s.wait(lambda: s.pair_is_up() and s.attached(), "the pair to come up and be attached")
        return fd

    # --- started from a terminal -------------------------------------------------------------
    def test_terminal_184x46(self):
        s = self.server()
        self.start_from_terminal(s, 184, 46)
        self.assert_layout(s, 184, 46)

    def test_terminal_220x54(self):
        s = self.server()
        self.start_from_terminal(s, 220, 54)
        self.assert_layout(s, 220, 54)

    def test_smaller_terminal_attached_to_another_session(self):
        s = self.server()
        self.other_terminal(s, 120, 40)
        self.start_from_terminal(s, 184, 46)
        self.assert_layout(s, 184, 46)
        self.assertEqual(s.window("other"), (120, 39), "the other session is left alone")

    def test_larger_terminal_attached_to_another_session(self):
        s = self.server()
        self.other_terminal(s, 250, 60)
        self.start_from_terminal(s, 184, 46)
        self.assert_layout(s, 184, 46)
        self.assertEqual(s.window("other"), (250, 59), "the other session is left alone")

    # --- tmux's status line -------------------------------------------------------------------
    def test_status_line_of_two_rows(self):
        s = self.server(["set -g status 2"])
        self.other_terminal(s, 120, 40)
        self.start_from_terminal(s, 184, 46)
        self.assert_layout(s, 184, 46, status_lines=2)

    def test_status_line_off(self):
        s = self.server(["set -g status off"])
        self.other_terminal(s, 120, 40)
        self.start_from_terminal(s, 184, 46)
        self.assert_layout(s, 184, 46, status_lines=0)

    # --- the window still follows the terminal afterwards -------------------------------------
    def test_window_follows_a_later_terminal_resize(self):
        s = self.server()
        fd = self.start_from_terminal(s, 184, 46)
        self.assert_layout(s, 184, 46)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
        s.wait(lambda: s.window(s.name) == (200, 49), "the window to follow the terminal to 200x50")

    # --- started from inside tmux (switch-client) ---------------------------------------------
    def test_started_from_inside_tmux(self):
        s = self.server()
        s.terminal(s.base + ["new-session", "-s", "host", "-c", s.work], 184, 46)
        s.wait(lambda: s.window("host") is not None, "the host session")
        s.tmux("split-window", "-h", "-t", "host")        # the pane aipair is typed in is NOT the terminal's size
        line = "env 'PATH=%s' AIPAIR_CLAUDE_FLAGS=--version AIPAIR_CODEX_FLAGS=--version '%s' '%s'" % (
            s.env["PATH"], AIPAIR, s.proj)
        s.tmux("send-keys", "-t", "host", line, "C-m")
        s.wait(lambda: s.pair_is_up() and s.attached(), "the pair to come up and the client to switch to it")
        self.assert_layout(s, 184, 46)

    # --- no terminal: nothing is resized, exactly as before ------------------------------------
    def test_without_a_terminal_nothing_is_resized(self):
        s = self.server()
        self.other_terminal(s, 120, 40)
        before = s.window("other")
        r = subprocess.run([AIPAIR, s.proj], env=s.env, stdin=subprocess.DEVNULL, capture_output=True,
                           universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 1, "aipair still ends with tmux's own attach failure")
        self.assertIn("not a terminal", r.stderr)
        s.wait(s.pair_is_up, "the pair to come up")
        plain = "plain-%d" % os.getpid()                  # what tmux itself does for a detached session, right now
        s.tmux("new-session", "-d", "-s", plain, stdin=subprocess.DEVNULL)
        win = s.window(s.name)
        self.assertEqual(win, s.window(plain), "the window has the size tmux gives any detached session")
        p = s.panes()
        self.assertEqual(p["codex"][0], win[0] * CODEX_PCT // 100)
        self.assertEqual(p["bridge"][1], win[1] * BRIDGE_PCT // 100)
        self.assertEqual(s.tmux("show-options", "-w", "-t", s.name + ":", "window-size"), "")
        self.assertEqual(s.window("other"), before, "the other session is left alone")


if __name__ == "__main__":
    if not REAL_TMUX:
        raise SystemExit("tmux not found")
    unittest.main(verbosity=2)
