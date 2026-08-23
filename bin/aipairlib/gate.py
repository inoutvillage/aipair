"""aipair stop-gate runner — `--gate` シェルコマンドの実行（D3 relay 分割 / P2-1 増分3）。

停止ワード検知後にゲートコマンドを《作業ディレクトリで独立プロセスグループ》として実行し、
成功（exit 0）した時だけ停止／次タスクへ進めるための純ロジック。timeout / Ctrl-C / `&`
バックグラウンドジョブ残留のいずれでも**グループごと確実に kill** し reader を join するので何も
orphan しない。出力は固定サイズの tail バッファへ drain（`yes` 等の無限出力でも OOM せず、満杯
パイプでも deadlock しない）、control 文字を scrub してペインへ誤入力もしない。tmux/画面には
一切依存せず tests/relay-parsers.py の GateRunner で被覆。
"""
import collections
import os
import signal
import subprocess
import threading
import time

from .corelib import GATE_OUTPUT_CAP, scrub_output, _oneline_cap, gate_message, oneline
from .logs import log, c


def run_gate(cmd, cwd, timeout):
    """Run the --gate shell command in cwd. Returns (ok, scrubbed output text).

    The command runs in its OWN process group (start_new_session): a timeout, a
    KeyboardInterrupt, or a shell that exits while leaving background jobs — all end with
    the WHOLE group killed and the reader thread joined, so nothing is orphaned. Output is
    drained on a thread into a fixed-size tail buffer (an unbounded producer like `yes`
    cannot OOM the relay, a full pipe cannot deadlock the wait); the buffer is read only
    after the reader has joined. Bytes are decoded errors="replace" and scrubbed of
    control characters so non-UTF-8 / ANSI output can neither crash the relay nor be typed
    into the pane as keystrokes."""
    try:
        proc = subprocess.Popen(cmd, shell=True, cwd=cwd,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return False, f"exec error: {e}"
    try:
        pgid = os.getpgid(proc.pid)          # captured now: valid while any group member lives
    except OSError:
        pgid = proc.pid
    buf, kept, dropped = collections.deque(), [0], [False]

    def drain():
        for chunk in iter(lambda: proc.stdout.read(65536), b""):
            buf.append(chunk); kept[0] += len(chunk)
            while kept[0] > GATE_OUTPUT_CAP and len(buf) > 1:
                kept[0] -= len(buf.popleft()); dropped[0] = True
        proc.stdout.close()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    raised = None
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
    except BaseException as e:                # Ctrl-C etc.: clean up, then re-raise below
        raised = e
    finally:
        # Always tear the whole group down: timeout, interrupt, or a shell that exited
        # leaving `&` background jobs still writing. Then join the reader so the buffer is
        # not mutated while we read it.
        _kill_group(proc, pgid, signal)
        reader.join(timeout=5)
    out = scrub_output(b"".join(buf).decode("utf-8", "replace"))
    if dropped[0]:
        out = "…(truncated to the last ~256KB)… " + out
    if raised is not None:
        raise raised
    if timed_out:
        return False, out + f"\n[gate] timeout after {timeout}s (process group killed): {_oneline_cap(cmd, 200)}"
    return proc.returncode == 0, out


def _kill_group(proc, pgid, signal):
    """Terminate the gate's process group `pgid`. TERM, a short grace for a clean exit,
    then KILL unconditionally (a child ignoring TERM whose parent shell already exited
    must still die — escalation is not gated on the group looking dead, since a reaped or
    zombie leader confuses liveness probes), then reap the shell so it leaves no zombie.
    pgid is captured at launch, not re-derived here, to avoid signalling a recycled pid."""
    def killpg(sig):
        try:
            os.killpg(pgid, sig)
        except OSError:
            try:
                proc.send_signal(sig)
            except OSError:
                pass
    killpg(signal.SIGTERM)
    deadline = time.time() + 0.5
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.05)
    killpg(signal.SIGKILL)            # unconditional: SIGKILL can't be ignored by any group member
    try:
        proc.wait(timeout=3)          # reap the shell (no lingering zombie keeps the pgid "alive")
    except subprocess.TimeoutExpired:
        pass


def gate_or_message(a, gate_state, cwd):
    """At a stop point, run the gate (in `cwd`, the normalised working dir) if one is set.
    (True, None)      no gate, or it passed → stop / move on as usual
    (False, message)  it failed → send Claude back with `message`
    (False, None)     it failed --gate-rounds times → give up (caller exits 6)"""
    if not a.gate:
        return True, None
    log("◆ 停止ゲート実行: " + _oneline_cap(a.gate, 200))
    ok, out = run_gate(a.gate, cwd, a.gate_timeout)
    if ok:
        log("◆ " + c("ok", "停止ゲート通過"))
        gate_state["fails"] = 0
        return True, None
    gate_state["fails"] += 1
    n = gate_state["fails"]
    print(c("warn", f"│ ■ 停止ゲート失敗（{n}/{a.gate_rounds}）: ") + oneline(out, 300), flush=True)
    if n >= a.gate_rounds:
        print(c("warn", f"│ ■ 停止ゲートが {a.gate_rounds} 回失敗。人間の判断が必要です。停止します。"), flush=True)
        print("\a", end="", flush=True)
        return False, None
    return False, gate_message(a.gate, out, n, a.gate_rounds)
