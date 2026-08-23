"""aipair-corelib — pure helpers extracted from aipair-relay (D3 relay 分割・案A 増分1).

tmux / ログ / 画面キャプチャに一切依存しない自己完結関数群（停止ワード判定・版ゲート・
停止ゲートの出力整形）。aipair-relay が SourceFileLoader で読み込み、名前を自分の名前空間に
束ねて使う。全関数は tests/relay-parsers.py で被覆されている。
"""
import re
import subprocess
import unicodedata


# --- version gate ----------------------------------------------------------- #
# The plan-approval / question dialogs are read by scraping the CLIs' TUI, so they are
# tied to specific claude/codex versions. Keep this in sync with README「検証済みバージョン」.
TESTED_VERSIONS = {"claude": "2.1.238", "codex": "0.149.0"}


def parse_version(text):
    """The first version token in `text`, captured WHOLE so nothing that differs from a
    tested version can be truncated onto it: 2.1.238 → '2.1.238', but 2.1.238.1 /
    2.1.238rc1 / 2.1.238-beta.1 keep their full form and therefore mismatch. Starts at a
    `d.d` not glued to a preceding number, and runs to the last version char (never ends
    on a separator). None if there is no such token."""
    m = re.search(r"(?<![0-9.+-])\d+\.\d+(?:[0-9A-Za-z.+-]*[0-9A-Za-z])?", text or "")
    return m.group(0) if m else None


def detect_version(binary):
    """`binary --version` → version string, or None if it can't be run, exits non-zero, or
    prints nothing parseable. errors='replace' keeps the decode from RAISING; if that
    replacement actually fired (U+FFFD present) the output is not valid UTF-8, so it is
    treated as unknown (None) rather than parsed — 'unknown' is the safe side of the
    version gate, and half-decoded bytes must not read as a tested version."""
    try:
        p = subprocess.run([binary, "--version"], capture_output=True, text=True,
                           errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    out = (p.stdout or "") + (p.stderr or "")
    if "\ufffd" in out:
        return None
    return parse_version(out)


def version_gate(a, detected, tested=TESTED_VERSIONS):
    """Compare detected claude/codex versions to the tested ones. Returns (rows, bad):
    rows = [(name, detected, tested, status)] with status in ok/mismatch/unknown; bad =
    names not known-good. On any bad entry, dialog automation (plan review + question
    relay) is switched OFF — unless --allow-untested-dialogs — while poke/transcript
    relaying continues unchanged."""
    rows, bad = [], []
    for name in ("claude", "codex"):
        det, tst = detected.get(name), tested.get(name)
        if det is None:
            status = "unknown"; bad.append(name)
        elif det == tst:
            status = "ok"
        else:
            status = "mismatch"; bad.append(name)
        rows.append((name, det, tst, status))
    if bad and not a.allow_untested_dialogs:
        a.no_plan_review = True
        a.no_question_relay = True
    return rows, bad


# --- JSONL schema feature-probe --------------------------------------------- #
# version_gate above only compares --version strings, but the CORE relay reads the agents'
# JSONL/rollout transcripts BY KEY: claude → type=="assistant" carrying message.stop_reason +
# timestamp, and a uuid/parentUuid chain; codex → type=="event_msg" carrying
# payload.type in {task_started, task_complete} + timestamp. A CLI update can keep a "tested"
# version string yet move those keys, and then turn-detection silently breaks (the relay just
# never sees a completed turn). schema_probe looks at REAL decoded records and reports whether
# the keyed shape the relay depends on is actually present, so the gate can extend past the TUI.

def schema_probe(agent, records):
    """Feature-probe decoded JSONL records for the schema markers the core relay reads.
    Returns (status, reason):
      "ok"         — positive evidence the expected keyed shape is present.
      "unverified" — no record of the relevant kind yet (empty/nascent log, or only unrelated
                     types): can't tell, and must NOT block a just-started pair.
      "mismatch"   — a record of the RIGHT KIND exists but LACKS the sub-field the relay keys
                     on: positive evidence of drift → safe side.
    Only positive drift trips "mismatch"; the mere ABSENCE of a kind stays "unverified", so a
    fresh session never false-alarms. `records` is a list of decoded dicts (see loglib.read_records)."""
    records = [d for d in (records or []) if isinstance(d, dict)]
    if agent == "claude":
        return _schema_probe_claude(records)
    if agent == "codex":
        return _schema_probe_codex(records)
    return ("unverified", "")


def _schema_probe_claude(records):
    # An "assistant-ish" record = top-level type=="assistant" OR inner message.role=="assistant"
    # (the latter catches a renamed top-level type). One FULLY shaped assistant turn — the exact
    # shape claude_done_ts + claude_response_attributed read — proves the schema in a single line.
    drift = None
    for d in records:
        msg = d.get("message") if isinstance(d.get("message"), dict) else None
        inner_role = msg.get("role") if msg else None
        if d.get("type") != "assistant" and inner_role != "assistant":
            continue
        if (d.get("type") == "assistant" and msg is not None and "stop_reason" in msg
                and d.get("timestamp") is not None and d.get("uuid") is not None
                and "parentUuid" in d):
            return ("ok", "assistant+stop_reason+timestamp+uuid+parentUuid 確認")
        # assistant-ish but not the shape the relay reads → remember the first concrete drift.
        # claude_response_attributed walks BOTH uuid and parentUuid, so both are required.
        if d.get("type") != "assistant":
            drift = drift or "assistant メッセージだが type!='assistant'（型名ドリフト）"
        elif msg is None or "stop_reason" not in msg:
            drift = drift or "assistant に message.stop_reason が無い（完了検知不能）"
        elif d.get("timestamp") is None:
            drift = drift or "assistant に timestamp が無い"
        elif d.get("uuid") is None:
            drift = drift or "assistant に uuid が無い（応答帰属チェーン不能）"
        else:
            drift = drift or "assistant に parentUuid が無い（応答帰属チェーン不能）"
    return ("mismatch", drift) if drift else ("unverified", "完了 assistant ターン未確認")


def _schema_probe_codex(records):
    # codex_done_ts keys on type=="event_msg" + payload.type in {task_started, task_complete} +
    # timestamp. A well-formed task event (started OR complete — same envelope) proves it; a
    # task event NOT under event_msg, or without a timestamp, is positive drift.
    drift = None
    for d in records:
        p = d.get("payload") if isinstance(d.get("payload"), dict) else None
        ptype = p.get("type") if p else None
        if ptype not in ("task_started", "task_complete"):
            continue
        if d.get("type") == "event_msg" and d.get("timestamp") is not None:
            return ("ok", "event_msg/%s+timestamp 確認" % ptype)
        drift = drift or ("%s だが type!='event_msg'（型名ドリフト）" % ptype
                          if d.get("type") != "event_msg" else "%s に timestamp が無い" % ptype)
    return ("mismatch", drift) if drift else ("unverified", "task_started/task_complete 未出現")


def schema_gate(a, probes):
    """probes = {agent: (status, reason)}. Returns (rows, bad): rows = [(name, status, reason)],
    bad = agents whose status is "mismatch". A mismatch means the JSONL/rollout that the core relay
    parses for turn-completion / response-attribution has drifted from what the relay knows how to
    read — continuing to drive permission-bypassed agents off a mis-parsed log is not safe, so the
    default is FAIL-CLOSED: the relay stops (exit 7). Only an explicit --allow-untested-schema /
    AIPAIR_ALLOW_UNTESTED_SCHEMA opts into FAIL-OPEN, and even then dialog automation (which most
    depends on exact schema) is turned OFF. Either way a.schema_mismatch is set so the relay warns
    loudly. "unverified" never trips the gate (a fresh log with no turns yet is normal)."""
    rows, bad = [], []
    for name in ("claude", "codex"):
        status, reason = probes.get(name, ("unverified", ""))
        rows.append((name, status, reason))
        if status == "mismatch":
            bad.append(name)
    if bad:
        a.schema_mismatch = True
        if getattr(a, "allow_untested_schema", False):
            # fail-open override: keep running but degrade the schema-sensitive dialog automation.
            a.no_plan_review = True
            a.no_question_relay = True
        # without the override the caller (relay) fails closed and exits 7 — it does NOT continue.
    return rows, bad


def schema_fail_closed(a, bad):
    """True when a schema mismatch must STOP the relay (exit 7): there is drift (`bad` non-empty)
    and no --allow-untested-schema / AIPAIR_ALLOW_UNTESTED_SCHEMA override. This is the single
    place the default-fail-closed / explicit-fail-open policy is decided."""
    return bool(bad) and not getattr(a, "allow_untested_schema", False)


def head_line(text):
    """メッセージ本文の《先頭の非空行》を strip して返す（無ければ ""）。制御 sentinel は
    この先頭行との《完全一致》で判定する（部分一致・文中言及・同一行の後続テキストは不成立）。
    先頭の空行はスキップするが、非空行が現れたらそこで確定する（2行目以降は見ない）。"""
    for raw in (text or "").splitlines():
        s = raw.strip()
        if s:
            return s
    return ""


def hit_stop(texts, phrases):
    """停止／状態遷移の判定。制御信号は自然言語から分離した専用 sentinel
    （例 [AIPAIR_REVIEW_OK] / [AIPAIR_ALL_DONE]）を使い、**最終 assistant メッセージの
    先頭の非空行が sentinel と完全一致**した時だけ成立する。

    旧実装は最終メッセージ冒頭100字の substring 一致で、否定文・引用・指示文中の言及
    （「まだ [AIPAIR_REVIEW_OK] とは言えない」「"[AIPAIR_REVIEW_OK]" と回答してください」）でも
    誤成立し得た。権限バイパス下で自律運転する relay では誤停止・誤遷移を避けるため、先頭行の
    完全一致に限定する（＝制御信号が単独で先頭行に置かれた時だけ成立）。進捗ナレーションを
    複数メッセージ吐くため、連結全文ではなく最終メッセージ texts[-1] で判定する点は従来どおり。"""
    if not texts:
        return False
    line = head_line(texts[-1])
    return any(line == p for p in phrases if p)


# --- stop gate (--gate): a mechanical check on top of the agents' say-so -------- #
GATE_OUTPUT_CAP = 256 * 1024   # bytes of gate output kept in memory (tail); the rest is dropped


# ANSI CSI/OSC escapes and a lone ESC; scrubbed from gate output before it reaches a
# pane (ESC would be read as keystrokes) or a log.
_ANSI_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_][0-9;?]*[ -/]*[@-~]|\x1b")


def scrub_output(s):
    """Make command output safe to type into a tmux pane and to print: drop ANSI escapes,
    then replace every control char except newline/tab with a space. This covers C0
    (incl. NUL, which makes `tmux send-keys` raise 'embedded null byte', and ESC, read as
    keys), DEL (0x7f, a real control input for send-keys) and C1 (0x80-0x9f) — i.e. the
    whole Unicode "Cc" category, not just ord < 32."""
    s = _ANSI_RE.sub("", s)
    return "".join(ch if (ch in "\n\t" or unicodedata.category(ch) != "Cc") else " " for ch in s)


def gate_tail(out, lines=40, limit=1500):
    """Last `lines` non-empty lines of a command's output, folded into ONE line (pokes are
    typed into the composer, where a newline would submit), capped at `limit` chars."""
    tail = [" ".join(l.split()) for l in out.strip().splitlines() if l.strip()][-lines:]
    text = " ⏎ ".join(tail)
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


def _oneline_cap(s, limit):
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


def gate_message(cmd, out, n, limit):
    """What Claude gets when the review passed but the gate did not. cmd is folded to one
    line and capped: a multi-line AIPAIR_GATE must not put a newline in the poke body
    (the composer submits on newline)."""
    return (f"【自動レビューループ】レビューは合格しましたが、停止ゲート `{_oneline_cap(cmd, 200)}` が失敗しました"
            f"（{n}/{limit} 回目）。以下の出力を読んで原因を直し、何を直したか簡潔に述べてターンを終えてください。"
            "あなたの返答は自動でCodexに共有されます—人間に伝言を頼まないでください。"
            f" ── gate output (tail): {gate_tail(out) or '(no output)'}")
