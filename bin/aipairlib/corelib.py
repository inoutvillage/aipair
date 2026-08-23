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
# JSONL/rollout transcripts BY KEY, probed as SEVERAL schema aspects (P1-2):
#   turn completion   — claude: type=="assistant" + message.stop_reason + timestamp;
#                        codex:  type=="event_msg" + payload.type in {task_started,task_complete} + timestamp
#   response attribution — claude: assistant uuid + parentUuid chain;
#                        codex:  turn_id on the task events AND on the user response_item's
#                                internal_chat_message_metadata_passthrough (codex_response_complete
#                                pairs by turn_id; drift there falls back to a time/position
#                                heuristic that can misattribute a queued turn).
#   delivery confirmation — claude: a user/queue-operation input row with string content (claude_input)
#   dialog resolution     — claude: a type==user row whose content list carries a tool_result block
#                                (claude_resolved). codex delivery is a raw text match (has_raw) with
#                                no keyed shape, so it needs no probe.
# turn-completion + attribution are REQUIRED (exercised every turn — if they drift the loop is
# silently wrong). delivery + dialog resolution are VETO-ONLY: they surface only when that action
# happens, so their absence never blocks "ok", but a positive drift in them still fails closed.
# A CLI update can keep a "tested" version string yet move these keys, silently breaking turn
# detection / attribution. schema_probe reads REAL decoded records and aggregates the aspects: any
# aspect mismatch ⇒ mismatch; "ok" only once EVERY required aspect is positively verified (so a
# task_started-only or user-metadata-less partial log stays "unverified", not a premature "ok").

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
        # required: ターン完了＋応答帰属（毎ターン行使）。veto-only: 配達確認・ダイアログ解決
        # （その動作が起きた時だけ観測できるので未発生で ok を阻害しないが、ドリフトは mismatch）。
        return _combine_aspects(
            [_schema_probe_claude(records)],
            veto=[_claude_delivery(records), _claude_dialog_resolution(records)])
    if agent == "codex":
        return _schema_probe_codex(records)
    return ("unverified", "")


def _claude_delivery(records):
    """配達確認スキーマ（LogWatch.claude_input）: Claude への入力として送信された行＝
    type=='queue-operation'（operation enqueue/None・content が文字列）または type=='user'
    （message.content が文字列）。queue-operation なのに文字列 content が無い等の形状ドリフトを
    mismatch で捕捉する。まだ入力行が無ければ unverified（veto-only）。"""
    ok = False
    drift = None
    for d in records:
        t = d.get("type")
        if t == "queue-operation":
            if d.get("operation") not in (None, "enqueue"):
                continue
            if isinstance(d.get("content"), str):
                ok = True
            else:
                drift = drift or "queue-operation に文字列 content が無い（配達確認不能）"
        elif t == "user":
            msg = d.get("message") if isinstance(d.get("message"), dict) else None
            c = msg.get("content") if msg else None
            if isinstance(c, str):
                ok = True
            elif isinstance(c, list):
                # claude_input は user 行の content を《文字列》として配達確認する。text block 配列の
                # user 行は正常ログにも多い（isMeta の skill 注入・`[Request interrupted by user]`
                # 割り込み行・画像添付・tool_result）ので、これらを drift 扱いすると誤停止する（Codex
                # 指摘・実ログで多数）。drift とみなすのは《実タイプ入力》— origin.kind=="human" or
                # promptSource あり — が isMeta/割り込みでなく、content が text ブロックのみ（画像等
                # 他種ブロックを含まない）に化けたケースだけ。これなら claude_input が読めず配達確認
                # 不能＝真のドリフト。
                origin = d.get("origin")
                is_typed = (d.get("promptSource") is not None
                            or (isinstance(origin, dict) and origin.get("kind") == "human"))
                is_excluded = bool(d.get("isMeta")) or d.get("interruptedMessageId") is not None
                block_types = {b.get("type") for b in c if isinstance(b, dict)}
                if is_typed and not is_excluded and block_types == {"text"}:
                    drift = drift or "typed 入力行の content が文字列でなく text ブロック（claude_input が配達確認不能）"
    if drift:
        return ("mismatch", drift)
    return ("ok", "claude 入力行（文字列 content）確認") if ok else ("unverified", "Claude 入力行 未出現")


def _claude_dialog_resolution(records):
    """ダイアログ解決スキーマ（LogWatch.claude_resolved）: type=='user' で message.content が list、
    その中に type=='tool_result' ブロックを含む行（プラン承認/差し戻し/decline の成立証拠）。
    tool_result を持つのに type!='user' 等のドリフトを mismatch で捕捉する。ダイアログが起きて
    いなければ unverified（veto-only：通常のレビューループでは発生しないので ok を阻害しない）。"""
    ok = False
    drift = None
    for d in records:
        msg = d.get("message") if isinstance(d.get("message"), dict) else None
        c = msg.get("content") if msg else None
        has_tr = isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                             for b in c)
        if not has_tr:
            continue
        if d.get("type") == "user":
            ok = True
        else:
            drift = drift or "tool_result を持つが type!='user'（ダイアログ解決の検出不能）"
    if drift:
        return ("mismatch", drift)
    return ("ok", "tool_result user 行 確認") if ok else ("unverified", "ダイアログ解決行 未出現")


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


def _combine_aspects(required, veto=()):
    """複数のスキーマ側面の probe 結果を安全側に集約する（P1-2）:
      - required / veto のどれか一つでも mismatch なら mismatch（安全側）。
      - required が全て ok で初めて ok（＝自律ループに必須のスキーマが全て確認できて compatible）。
      - それ以外は unverified。
    veto 側面（delivery confirmation / dialog resolution）は「その動作が実際に起きた時にだけ
    観測できる」ため、未発生（unverified）で ok を阻害しない — ただし積極ドリフトは mismatch で
    捕捉する。required は毎ターン必ず行使される側面（ターン完了・応答帰属）。"""
    allsides = list(required) + list(veto)
    for st, r in allsides:
        if st == "mismatch":
            return ("mismatch", r)
    if required and all(st == "ok" for st, _ in required):
        return ("ok", " / ".join(r for _, r in allsides if r))
    return ("unverified", " / ".join(r for _, r in allsides if r))


def _codex_turn_completion(records):
    """ターン完了スキーマ（codex_done_ts は task_complete を、codex_response_complete は
    task_started を anchor に読む）: type=='event_msg' + payload.type in {task_started,
    task_complete} + timestamp。**両方**が well-formed で観測できて初めて ok（片方だけの途中ログ
    では latch せず unverified＝後続イベントの検証機会を残す）。event_msg 外／timestamp 欠落は
    positive drift。"""
    started = complete = False
    drift = None
    for d in records:
        p = d.get("payload") if isinstance(d.get("payload"), dict) else None
        ptype = p.get("type") if p else None
        if ptype not in ("task_started", "task_complete"):
            continue
        if d.get("type") == "event_msg" and d.get("timestamp") is not None:
            if ptype == "task_started":
                started = True
            else:
                complete = True
        else:
            drift = drift or ("%s だが type!='event_msg'（型名ドリフト）" % ptype
                              if d.get("type") != "event_msg" else "%s に timestamp が無い" % ptype)
    if drift:
        return ("mismatch", drift)
    if started and complete:
        return ("ok", "event_msg task_started+task_complete+timestamp 確認")
    return ("unverified", "task_started/task_complete が揃っていない")


def _codex_attribution(records):
    """応答帰属スキーマ（codex_response_complete の turn_id ペアリング）: task_started/task_complete
    が turn_id を持ち、**かつ** user の response_item が
    internal_chat_message_metadata_passthrough.turn_id を持つこと。turn_id が消えると帰属が
    「時刻・位置」ヒューリスティックへ退行し先行タスクを誤帰属し得る（実バグ既知）ため、その
    積極的ドリフトを mismatch で捕捉する。ペアリングは task 側と user 側の turn_id 双方に依存する
    ので、**両方**を観測できて初めて ok（片方だけでは latch せず unverified）。"""
    event_ok = user_ok = False
    drift = None
    for d in records:
        p = d.get("payload") if isinstance(d.get("payload"), dict) else None
        if not p:
            continue
        ptype = p.get("type")
        if d.get("type") == "event_msg" and ptype in ("task_started", "task_complete"):
            if p.get("turn_id") is not None:
                event_ok = True
            else:
                drift = drift or "%s に turn_id が無い（応答帰属の対応付け不能）" % ptype
        elif d.get("type") == "response_item" and ptype == "message" and p.get("role") == "user":
            # codex_response_complete は「nonce を含む TEXT の user response_item」から turn_id を
            # 読む。テキストを持つ実 user メッセージ（＝relay の poke が着地した行）が
            # metadata.turn_id を欠くのは帰属不能の積極ドリフト（passthrough キー自体の有無に
            # かかわらず mismatch）。Codex 指摘: 完全ターンで metadata 欠落を unverified にすると
            # ブロックされず帰属不能スキーマで走り続けてしまう。
            content = p.get("content")
            has_text = isinstance(content, list) and any(
                isinstance(b, dict) and isinstance(b.get("text"), str) for b in content)
            if not has_text:
                continue
            meta = p.get("internal_chat_message_metadata_passthrough")
            tid = meta.get("turn_id") if isinstance(meta, dict) else None
            if tid is not None:
                user_ok = True
            else:
                drift = drift or "text の user response_item に metadata.turn_id が無い（帰属不能）"
    if drift:
        return ("mismatch", drift)
    if event_ok and user_ok:
        return ("ok", "task_event.turn_id + user.metadata.turn_id 確認")
    return ("unverified", "turn_id の対応付け材料（task 側 / user 側）が揃っていない")


def _schema_probe_codex(records):
    # ターン完了 ＋ 応答帰属（turn_id）の両スキーマ側面が《揃って》初めて compatible（P1-2）。
    # 部分証拠（task_started のみ・user metadata 欠如）では latch せず unverified のまま probe を続ける。
    return _combine_aspects([_codex_turn_completion(records), _codex_attribution(records)])


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


def schema_should_reprobe(latch, latched_path, new_path):
    """一エージェントの runtime schema 監視について、追跡ログ path が変わったかを見て
    (reset, skip) を返す（P1-4）。終端 'mismatch' latch は《ひとつのログ》に対するものなので、
    path が切り替わったら（resume/clear/再起動/rotation）latch をリセットして新ログを未確認から
    再 probe しなければ、新ログを一生見ない。
      reset=True … latch/sig を破棄して latched_path を new_path へ更新すべき
      skip=True  … （path 変化を反映した後の）latch が終端 'mismatch' なので今回の probe は skip"""
    reset = new_path != latched_path
    latch_after = None if reset else latch
    return (reset, latch_after == "mismatch")


def schema_latch_step(current, status, allow_untested):
    """一エージェントの runtime schema 監視を進める1ステップ。`current` は latch 状態
    (None=未確認 / 'ok-seen'=ok を観測済みだが監視継続 / 'mismatch'=確定・終端)、`status` は
    今回の集約 probe 結果。戻り値は (new_latch, action) で action ∈ {'stop','degrade','ok','none'}。

    🔴 P1-b の要点: 'ok' は**終端ではない**。delivery/dialog は veto-only で「その動作が起きた時
    だけ観測できる」ため、ok 到達後も **latch せず probe を続ける**（'ok-seen' は再 probe を許す
    状態）。こうして後から dialog が発生してスキーマがドリフトした時にも mismatch を捕捉できる。
    終端は 'mismatch' のみ（fail-closed なら stop、override なら degrade して継続）。"""
    if current == "mismatch":
        return ("mismatch", "none")
    if status == "mismatch":
        return ("mismatch", "degrade" if allow_untested else "stop")
    if status == "ok":
        # ok を初めて観測した時だけログを出す（'ok'）。以降も監視は続ける（latch しない）。
        return ("ok-seen", "ok" if current != "ok-seen" else "none")
    return (current, "none")   # unverified → 監視継続


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
