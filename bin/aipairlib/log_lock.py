"""aipair log locking — 各エージェントのペインが所有するトランスクリプトの《特定と lock/refresh》
（D3 relay 分割 / P2-1 増分4）。同一 cwd に複数の Claude/Codex セッションがあり得るため、mtime 最新
＝ペインのセッションとは限らない。画面内容との突き合わせ（claude_matches_pane）や /proc プロセス
実体・起動 since での固定で、relay が読むログをペアの相手へ確実にピンする。peer-log の索引
（peerlog）と tmux（tmuxlib.tmux / capture）に依存し、それ以外の状態は引数で受け取る純関数群。
tests/codex-follow.py で被覆。
"""
import glob
import math
import os
import re
import subprocess
import time

from . import peerlog, tmuxlib, dialoglib
from .logs import dim

tmux = tmuxlib.tmux
PLAN_QUESTION = dialoglib.PLAN_QUESTION


# --- log file locking (lock onto the loop's freshly-spawned sessions) ------- #
def claude_glob(cwd):
    # Claude Code のプロジェクトdir名は非英数字をすべて '-' に置換する
    # （日本語・'_' を含むパスで glob が空振りしてセッションを永遠に発見できないバグの修正）
    enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(peerlog.CLAUDE_PROJECTS, enc, "*.jsonl")


def codex_all():
    return peerlog.codex_all()


def codex_cwd_matches(path, cwd):
    # peerlog caches each rollout's first line, so this no longer opens the file every poll.
    cw = peerlog.codex_cwd(path)
    return bool(cw) and cw == peerlog._norm(cwd)


_MD_STRIP = str.maketrans("", "", "*`#_~|")


def _norm_text(s):
    """空白と markdown 記号を落とす（jsonl の生テキストと TUI のレンダリング済み画面を比較可能にする）。"""
    return "".join(s.split()).translate(_MD_STRIP)


def _last_assistant_entry(path):
    """jsonl の最後の assistant エントリ（dict）を返す。"""
    last = None
    try:
        for line in open(path, "r", encoding="utf-8", errors="replace"):
            try:
                d = peerlog.json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "assistant":
                last = d
    except OSError:
        return None
    return last


def claude_matches_pane(path, pane):
    """候補 jsonl がそのペインのセッションか、画面内容との突き合わせで照合する。
    同一プロジェクト dir に複数の Claude セッション（ワーカー + 別ペインのアシスタント会話等）が
    あると、mtime 最新 = ペインのセッションとは限らない（2026-07-20 実バグ: --adopt が
    アシスタント側を誤ピンし、その返答を「実装完了」と誤認して幽霊ラウンドが進行）。

    1) 通常時: 直近 assistant テキスト断片が画面に見えるか（TUI は代替スクリーンで
       スクロールバックが無いため、可視画面 = 直近の transcript）
    2) プランダイアログ表示中: 画面はダイアログに占有され transcript が見えないため、
       「最後の assistant エントリが ExitPlanMode の tool_use で終わっている」=
       承認待ち状態のペインと整合するログか、で照合する"""
    try:
        cap = tmux("capture-pane", "-p", "-t", pane, "-S", "-300", capture=True).stdout
    except subprocess.CalledProcessError:
        return False
    hay = _norm_text(cap)
    msgs = [t for (_ts, role, t) in peerlog.parse_claude(path, False) if role == "assistant"]
    for m in reversed(msgs[-3:]):
        norm = _norm_text(m)
        for probe in (norm[:40], norm[-40:]):
            if len(probe) >= 16 and probe in hay:
                return True
    if PLAN_QUESTION in cap:
        # ダイアログが参照するプランファイルと、ログ末尾の tool_use の対象を突き合わせる。
        # プランモードは「プランファイルへの Edit/Write → ダイアログ表示」で止まるため、
        # 承認待ちセッションの最終エントリはそのプランファイルを指す（2026-07-20 実測）。
        m = re.search(r"[^\s·]*\.claude/plans/[^\s]+\.md", cap)
        plan_base = os.path.basename(m.group(0)) if m else None
        last = _last_assistant_entry(path)
        content = ((last or {}).get("message") or {}).get("content")
        if isinstance(content, list):
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                if b.get("name") == "ExitPlanMode":
                    # ExitPlanMode の存在だけでは同一 cwd の別セッション（別ペインで
                    # 承認待ちの別プラン等）と区別できない（2026-07-21 Codex レビュー）。
                    # 入力のプラン本文が画面のダイアログに見えている場合のみ一致とする
                    # （ダイアログはプラン本文を表示するため、正しいペインなら断片が写る）
                    plan_text = _norm_text((b.get("input") or {}).get("plan") or "")
                    for probe in (plan_text[:40], plan_text[-40:]):
                        if len(probe) >= 16 and probe in hay:
                            return True
                    continue
                fp = (b.get("input") or {}).get("file_path") or ""
                if plan_base and os.path.basename(fp) == plan_base:
                    return True
    return False


def lock_claude(cwd, seen, pane, baseline):
    files = sorted(glob.glob(claude_glob(cwd)), key=os.path.getmtime, reverse=True)
    freshset = {f for f in files if f not in seen}
    # 照合候補を「新規ファイル」に限定しない: /resume は relay 起動前から存在する旧 jsonl
    # へ「追記」するため、新規ファイル監視では永遠に発見できない（2026-07-21 実バグ:
    # tmux 再起動→ペイン内 resume で、resume 前の空セッションを誤ピンし
    # 「Claude の応答待ち」のまま永久停止）。relay 起動後に書き込みのあった既存ファイルは
    # resume 追記の可能性があるので照合候補に含める（照合ゲートが誤ピンを防ぐ）。
    live = [f for f in files if f in freshset or os.path.getmtime(f) > baseline]
    for f in live[:10]:
        if claude_matches_pane(f, pane):
            return f
    # 照合できる候補が無ければ従来どおり新規ファイルの mtime 最新（新規セッションはまだ
    # assistant テキストが無く照合不能のため。誤ピンしても refresh_claude_lock が自己回復する）
    fresh = sorted(freshset, key=os.path.getmtime, reverse=True)
    return fresh[0] if fresh else None


# The pair's launch epoch (peer's AIPAIR_CODEX_SINCE), read once from the session option
# @aipair-codex-since. Lets the relay share peer's EXACT fallback picker (codex_since) when
# /proc identity is unavailable (macOS), so the two never diverge onto different Codex sessions.
_CODEX_SINCE_EPOCH = None   # valid launch epoch, or None when the option is genuinely unset (legacy)
_CODEX_SINCE_BAD = False    # True when the option is PRESENT but invalid / unreadable → fail-closed


def read_codex_since(session):
    """Read @aipair-codex-since with the SAME validation peer-log applies to AIPAIR_CODEX_SINCE
    (peerlog._NUM_RE + finite, non-negative). Three outcomes, kept distinct so a corrupt value
    can never masquerade as 'legacy, use the heuristic': unset → epoch=None (heuristic allowed);
    valid → the epoch; present-but-invalid or query failure → BAD (codex_fallback fails closed)."""
    global _CODEX_SINCE_EPOCH, _CODEX_SINCE_BAD
    _CODEX_SINCE_EPOCH, _CODEX_SINCE_BAD = None, False
    try:
        v = tmuxlib.tmux("show-options", "-t", session, "-qv", "@aipair-codex-since",
                         capture=True).stdout.strip()
    except subprocess.CalledProcessError:
        _CODEX_SINCE_BAD = True        # metadata present but unreadable → do not guess
        return
    if not v:
        return                         # genuinely unset → legacy session, heuristic allowed
    if not peerlog._NUM_RE.match(v):
        _CODEX_SINCE_BAD = True; return
    val = float(v)
    if not math.isfinite(val) or val < 0:
        _CODEX_SINCE_BAD = True; return
    _CODEX_SINCE_EPOCH = val


def codex_fallback(cwd, tracked_or_none, seen=None):
    """Non-/proc Codex picker. A present-but-invalid launch epoch fails CLOSED (None) — never the
    mtime heuristic, which could mis-pin a same-cwd Codex. A valid epoch uses codex_since — the
    SAME pick peer makes, so peer and relay agree on macOS. Only a genuinely unset epoch (legacy
    session) keeps the old behaviour (lock: newest-unseen; refresh: follow-newer)."""
    if _CODEX_SINCE_BAD:
        return None
    if _CODEX_SINCE_EPOCH is not None:
        return peerlog.codex_since(cwd, _CODEX_SINCE_EPOCH)
    if seen is not None:
        return peerlog.codex_newest(cwd, exclude=seen)
    return peerlog.codex_follow(cwd, tracked_or_none)


def lock_codex(cwd, seen, pane):
    """Newest rollout for cwd that did not exist when the relay started (`seen`).
    Polled every second while waiting for the pair's Codex to show up, so it goes
    through peerlog's incremental index rather than re-walking the archive. `pane` is the
    relay's OWN resolved codex pane, so the /proc identity looks at THIS pair (never the
    caller's session). identity-capable but momentarily unresolved → return None and keep
    waiting; NEVER fall to the mtime heuristic there (it could mis-pin a same-cwd Codex from
    the very first lock — 2026-08-22 review)."""
    ident = peerlog.codex_via_pane(cwd, pane)
    if ident:
        return ident
    if peerlog.codex_identity_capable(pane):
        return None
    return codex_fallback(cwd, None, seen=seen)


def refresh_codex_lock(tracked_path, cwd, pane):
    """Codex CLI の再起動/新セッションで rollout がローテートしたら追従する。
    第一は /proc identity（`pane` の Codex プロセスが今開いている rollout）。identity が
    「一瞬 None（再起動中）」の時に mtime heuristic へ落ちると、同一 cwd の別 Codex へ誤って
    乗り換え得るため、identity 対応環境（codex_identity_capable）で既に追跡中なら現 path を維持し、
    heuristic への fallback は /proc 非対応・旧セッションの時だけに限定する（2026-08-22 レビュー）。
    非 identity 環境では従来どおり codex_follow（追跡中より新しい cwd 一致 rollout を照合）。"""
    ident = peerlog.codex_via_pane(cwd, pane)
    if ident:
        newest = ident
    elif peerlog.codex_identity_capable(pane):
        return tracked_path        # identity は使えるが今この瞬間は未解決 → drift させず現状維持
    else:
        newest = codex_fallback(cwd, tracked_path)
    if newest and newest != tracked_path:
        dim(f"codex rollout ローテート検知 → 追従: {os.path.basename(newest)}")
        return newest
    return tracked_path


_RELOCK_EVERY = 15  # 秒。claude 側ローテート走査の下限間隔（照合は jsonl 全 parse を伴うため）
_relock_at = 0.0


def refresh_claude_lock(tracked_path, cwd, pane, force=False):
    """Claude セッションのローテート（/resume・/clear・CLI 再起動）に追従する。
    codex 側 refresh_codex_lock と同趣旨。resume は旧 jsonl へ追記するため新規ファイル
    監視では捕まらず、resume 前の空セッションを誤ピンしたまま応答待ちで永久停止する
    （2026-07-21 実バグ）。追跡中ファイルより新しい mtime の jsonl がペイン内容と
    照合一致したら乗り換える。照合ゲートがあるので同プロジェクトで並走する別セッション
    （ワーカー・アシスタント会話等）には乗らない。

    誤ピン自己回復（2026-07-21 Codex レビュー）: 追跡中ログ自体がペイン照合に失敗する
    場合は「より新しい mtime」条件を外して直近候補を走査する。mtime 最新の誤ピンから
    より古い正解ログへは newer 条件では永遠に戻れないため。force=True は rate-limit を
    無視する（done 判定直前の妥当性確認用）。"""
    global _relock_at
    now = time.time()
    if not force and now - _relock_at < _RELOCK_EVERY:
        return tracked_path
    _relock_at = now
    files = sorted(glob.glob(claude_glob(cwd)), key=os.path.getmtime, reverse=True)
    tracked_ok = bool(tracked_path) and os.path.exists(tracked_path) \
        and claude_matches_pane(tracked_path, pane)
    if tracked_ok:
        tracked_mtime = os.path.getmtime(tracked_path)
        candidates = [f for f in files
                      if f != tracked_path and os.path.getmtime(f) > tracked_mtime][:5]
        label = "claude セッションローテート検知 → 追従"
    else:
        # 誤ピン疑い（追跡中ログがペインと照合不一致）: mtime 条件なしで走査
        candidates = [f for f in files if f != tracked_path][:10]
        label = "claude 誤ピン検知 → ペイン一致ログへ乗り換え"
    for f in candidates:
        if claude_matches_pane(f, pane):
            dim(f"{label}: {os.path.basename(f)}")
            return f
    return tracked_path
