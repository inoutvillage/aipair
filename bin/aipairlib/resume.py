"""aipair の起動時に「最新の会話を再開」する相手を決める（Claude / Codex）。

「最新」= その dir の会話のうち、最終更新（ログの mtime）が一番新しいもの。履歴を選ばせる機能ではない。
- Claude: ~/.claude/projects/*/<uuid>.jsonl。dir 名の符号化は別の dir と衝突しうる（/a-b と /a/b）ので、
  記録の `cwd` で照合する。非対話（`claude -p` / SDK: entrypoint が sdk-*）の会話は除く。
- Codex: rollout の session_meta の `cwd` で照合し、`codex exec`（source=exec）は除く（`codex resume --last`
  と同じ）。
- 今ほかのプロセスが使っている会話は除く（同じログを 2 つのプロセスが書くと会話が混ざる）。Linux の /proc
  で判定する: Claude は ~/.claude/sessions/<pid>.json（peerlog.claude_live_session）、Codex は開いている fd。
  /proc が無い環境では除外しない。

bin/aipair が `python3 - <bin> claude|codex <dir>` の形で呼び、見つかれば 1 行
`<id>\\t<開始時刻 epoch>\\t<表示用の最終更新時刻>` を出す（見つからなければ何も出さない）。
"""
import glob
import json
import os
import sys
import time

from . import peerlog


def _same_dir(a, b):
    if peerlog._norm(a) == peerlog._norm(b):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _claude_meta(path, max_lines=200):
    """(cwd, entrypoint) of a Claude session log — from the first record that carries a cwd. Earlier lines
    can be bookkeeping records with neither (mode, summary, queue-operation). (None, None) if none found."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and isinstance(d.get("cwd"), str):
                    ep = d.get("entrypoint")
                    return d["cwd"], (ep if isinstance(ep, str) else None)
    except OSError:
        pass
    return None, None


def _live_claude_sessions():
    """Session ids a running claude has open right now (empty without /proc)."""
    live = set()
    if not peerlog.proc_available():
        return live
    for f in glob.glob(os.path.join(peerlog.CLAUDE_SESSIONS, "*.json")):
        name = os.path.basename(f)[:-len(".json")]
        if name.isdigit():
            sid = peerlog.claude_live_session(int(name))
            if sid:
                live.add(sid)
    return live


def latest_claude(cwd):
    """(session_id, mtime) of the newest interactive Claude conversation for cwd that no process is using."""
    live = _live_claude_sessions()
    files = []
    for f in glob.glob(os.path.join(peerlog.CLAUDE_PROJECTS, "*", "*.jsonl")):
        sid = os.path.basename(f)[:-len(".jsonl")]
        if not peerlog._UUID_RE.match(sid) or sid in live:
            continue
        try:
            files.append((os.path.getmtime(f), f, sid))
        except OSError:
            continue
    for mtime, f, sid in sorted(files, reverse=True):
        rec_cwd, entry = _claude_meta(f)
        if rec_cwd is None or (entry or "").startswith("sdk"):
            continue
        if _same_dir(rec_cwd, cwd):
            return sid, mtime
    return None


def _codex_meta(path):
    """session_meta payload of a rollout (its first line), or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
        meta = json.loads(first)
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("type") != "session_meta":
        return None
    p = meta.get("payload")
    return p if isinstance(p, dict) else None


def _live_codex_rollouts():
    """Rollouts a running codex holds open right now (empty without /proc)."""
    live = set()
    if not peerlog.proc_available():
        return live
    for name in os.listdir("/proc"):
        if name.isdigit() and peerlog._proc_comm(int(name)) == "codex":
            live.update(os.path.realpath(r) for r in peerlog._open_rollouts(int(name)))
    return live


def latest_codex(cwd):
    """(session_id, start_epoch, mtime) of the newest interactive Codex session for cwd that no process
    is using. start_epoch is what peerlog.codex_since compares (the session_meta timestamp)."""
    live = _live_codex_rollouts()
    files = []
    for f in peerlog.codex_all():
        try:
            files.append((os.path.getmtime(f), f))
        except OSError:
            continue
    for mtime, f in sorted(files, reverse=True):
        p = _codex_meta(f)
        if not p or p.get("source") == "exec" or not isinstance(p.get("cwd"), str):
            continue
        sid = p.get("id")
        if not (isinstance(sid, str) and peerlog._UUID_RE.match(sid)):
            continue
        if not _same_dir(p["cwd"], cwd) or os.path.realpath(f) in live:
            continue
        start = peerlog.codex_start(f)
        if start is None:
            continue
        return sid, start, mtime
    return None


def main(argv):
    if len(argv) != 2 or argv[0] not in ("claude", "codex"):
        print("usage: resume.py claude|codex <dir>", file=sys.stderr)
        return 2
    kind, cwd = argv
    if kind == "claude":
        hit = latest_claude(cwd)
        if hit:
            sid, mtime = hit
            start = 0
    else:
        hit = latest_codex(cwd)
        if hit:
            sid, start, mtime = hit
    if hit:
        print("%s\t%r\t%s" % (sid, start, time.strftime("%m/%d %H:%M", time.localtime(mtime))))
    return 0
