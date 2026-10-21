"""aipair-tmuxlib — the tmux runner and pane helpers extracted from aipair-relay
(D3 relay 分割・案A 増分3 = A3). Self-contained (stdlib only); aipair-relay loads it via
SourceFileLoader and binds the names. Covered by tests/relay-parsers.py.
"""
import os
import subprocess
import time


# --- tmux helpers ----------------------------------------------------------- #
def tmux(*args, check=True, capture=False):
    return subprocess.run(["tmux", *args], check=check,
                          capture_output=capture, text=True)


def current_session():
    try:
        return tmux("display-message", "-p", "#{session_name}", capture=True).stdout.strip()
    except subprocess.CalledProcessError:
        return None


def session_option(session, opt):
    """A session-scoped user option value; "" iff genuinely unset. A tmux query FAILURE is NOT
    coerced to "" (that would be fail-open — a corrupt read must not read as "no stamp"); it
    raises CalledProcessError for the caller to treat as unresolved. Bare -t: show-options does
    not accept the "=name" exact form, but `session` here is already exact (tmux prefers exact)."""
    return tmux("show-options", "-t", session, "-qv", opt, capture=True).stdout.strip()


def find_panes(session):
    """Identify the claude/codex panes. Prefer the pane ids aipair stamped
    (@aipair-claude-pane / @aipair-codex-pane) — deterministic, no title/command guessing
    (Codex runs as `node`, titles get overwritten). Rules, per role:
      - a live, unique stamp that is not the relay's own pane → RESOLVED (locked);
      - a genuinely UNSET stamp → inferred from the remaining panes by the title/command/order
        heuristic below (keeps old codex-only pairs working across an upgrade, and pure-legacy
        sessions where neither stamp is set);
      - a stamp that is dead / duplicate / points at self / unreadable → UNRESOLVED, and NOT
        inferred, so the relay refuses to start rather than guess (it checks both roles present).
    Applying self_pane to stamps too stops a relay wrongly started from an agent pane from
    resolving that pane as an agent and then monitoring / poking itself."""
    out = tmux("list-panes", "-t", "=" + session,   # "=" = exact match: a prefix (aipair-ai)
               "-F", "#{pane_id}\t#{pane_current_command}\t#{pane_title}",   # must not hit aipair-aipair
               capture=True).stdout
    rows = [(ln.split("\t", 2) + ["", ""])[:3] for ln in out.splitlines() if ln]
    live = {pid for pid, _, _ in rows}
    self_pane = os.environ.get("TMUX_PANE")

    resolved, used, unset_roles = {}, set(), []
    for role in ("claude", "codex"):
        try:
            pid = session_option(session, "@aipair-" + role + "-pane")
        except subprocess.CalledProcessError:
            continue                       # query failed → unresolved (never inferred, never "unset")
        if not pid:
            unset_roles.append(role)       # genuinely unset → infer from the remaining panes
        elif pid in live and pid != self_pane and pid not in used:
            resolved[role] = pid           # live, unique, not self → locked
            used.add(pid)
        # else: dead / self / duplicate → unresolved (skipped; not inferred)

    if unset_roles:
        heur, leftovers = {}, []
        for pid, cmd, title in rows:
            if pid in used or pid == self_pane:
                continue
            t, cl = title.strip().lower(), cmd.strip().lower()
            if "claude" in unset_roles and "claude" not in heur and (t.startswith("claude") or cl == "claude"):
                heur["claude"] = pid
            elif "codex" in unset_roles and "codex" not in heur and (t.startswith("codex") or cl == "codex"):
                heur["codex"] = pid
            elif cl not in ("bash", "zsh", "sh", "fish", "python3", "python"):
                leftovers.append(pid)
        for role in ("claude", "codex"):
            if role in unset_roles and role not in heur and leftovers:
                heur[role] = leftovers.pop(0)
        resolved.update(heur)
    return resolved


def own_pane(session):
    """自分（relay）が走っている tmux ペインを返す。
    タイトルは `aipair` が起動時に一度セットするだけなので、`aipair-relay-here` で
    張り直すと実態と食い違ったまま残る（2026-08-17 実例: endless で走っているのに
    タイトルは "auto review loop" のまま）。relay 自身が名乗れば起動経路によらず正しくなる。
    TMUX_PANE が対象セッションに属さない場合は None（--session を明示して外から
    回した時に、無関係なペインを改名しないため）。"""
    pid = os.environ.get("TMUX_PANE")
    if not pid:
        return None
    try:
        out = tmux("list-panes", "-t", "=" + session, "-F", "#{pane_id}", capture=True).stdout
    except subprocess.CalledProcessError:
        return None
    return pid if pid in out.split() else None


def set_pane_title(pane, title):
    if pane:
        tmux("select-pane", "-t", pane, "-T", title, check=False)


def cancel_copy_mode(pane):
    """A pane left in copy-mode (e.g. the human scrolled it with the mouse)
    silently eats send-keys input — cancel it before injecting anything."""
    if tmux("display-message", "-p", "-t", pane, "#{pane_in_mode}",
            capture=True).stdout.strip() != "0":
        tmux("send-keys", "-t", pane, "-X", "cancel", check=False)
        time.sleep(0.2)


def pane_busy(pane):
    """Target TUI is mid-turn (streaming)?
    速判定: 'esc to interrupt'（Codex は常時表示・Claude Code は間欠表示）。
    フォールバック: 1秒あけた2回キャプチャの差分が3行以上 — 実行中は転写が流れて
    複数行が動く。アイドルの TUI にも毎秒その場で動く行（完了バッジのアニメ・
    ヒント行・ステータスライン等）があるため、全一致比較だとアイドルの Claude
    ペインを延々 busy と誤判定する（2026-08-14 実バグ: 60分の busy-wait 満了で
    poke が配達不可となり relay 停止）。"""
    try:
        first = tmux("capture-pane", "-p", "-t", pane, capture=True).stdout
    except subprocess.CalledProcessError:
        return False
    if "esc to interrupt" in first.lower():
        return True
    time.sleep(1.0)
    try:
        second = tmux("capture-pane", "-p", "-t", pane, capture=True).stdout
    except subprocess.CalledProcessError:
        return False
    if first == second:
        return False
    a = first.splitlines()
    b = second.splitlines()
    if len(a) < len(b):
        a += [""] * (len(b) - len(a))
    elif len(b) < len(a):
        b += [""] * (len(a) - len(b))
    return sum(1 for x, y in zip(a, b) if x != y) >= 3


def capture_pane(pane):
    return tmux("capture-pane", "-p", "-t", pane, capture=True).stdout
