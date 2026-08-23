"""aipair-loglib — Claude/Codex トランスクリプト（jsonl / rollout）からターン完了・応答帰属を
読み取る関数群（D3 relay 分割・案A 増分2 = A2）。tmux/画面には一切依存せず、peer-log の
パーサ（parse_claude / parse_codex / _ts_key）だけを使う。aipair-relay が SourceFileLoader で
読み込み、名前を自分の名前空間に束ねて使う。tests/relay-parsers.py で被覆されている。
"""
from . import peerlog   # normal package import (was a SourceFileLoader-by-path)


# --- schema feature-probe input --------------------------------------------- #
def read_records(path, tail_lines=800, tail_bytes=1_000_000):
    """Decode the last `tail_lines` JSON objects from the TAIL of a jsonl/rollout transcript
    (skipping unparseable/empty lines). To stay cheap on a HUGE log, it seeks to at most the last
    `tail_bytes` and reads only that window — the whole file is NOT scanned (a 40MB+ rollout was
    re-read in full every few seconds otherwise). A possibly-partial first line of the window is
    dropped. Returns a list of dicts; [] on a missing path or any OSError. Feeds
    corelib.schema_probe (the relay composes them in probe_log_schema)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            start = max(0, size - tail_bytes) if tail_bytes else 0
            fh.seek(start)
            data = fh.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").split("\n")
    if start > 0 and lines:
        lines = lines[1:]                         # window began mid-line → drop the partial head
    lines = [ln for ln in lines if ln.strip()]    # non-empty only …
    if tail_lines:
        lines = lines[-tail_lines:]               # … then the last N of those
    out = []
    for line in lines:
        try:
            d = peerlog.json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


# --- turn-completion detection --------------------------------------------- #
def claude_done_ts(path, since):
    """Epoch of the latest completed Claude turn after `since`, else None."""
    last_ts = last_sr = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
          for line in fh:
            try:
                d = peerlog.json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "assistant":
                last_ts = d.get("timestamp")
                last_sr = (d.get("message") or {}).get("stop_reason")
    except OSError:
        return None
    if last_ts and last_sr and last_sr != "tool_use":
        te = peerlog._ts_key(last_ts)
        return te if te > since else None
    return None


def codex_done_ts(path, since):
    """Epoch of the latest completed Codex turn (task_complete) after `since`, else None."""
    last_kind = last_ts = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
          for line in fh:
            try:
                d = peerlog.json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "event_msg":
                t = (d.get("payload") or {}).get("type")
                if t == "task_started":
                    last_kind, last_ts = "start", d.get("timestamp")
                elif t == "task_complete":
                    last_kind, last_ts = "complete", d.get("timestamp")
    except OSError:
        return None
    if last_kind == "complete" and last_ts:
        te = peerlog._ts_key(last_ts)
        return te if te > since else None
    return None


def turn_texts(agent, path, since, until):
    """Assistant messages (list) produced in (since, until] for the agent's completed turn."""
    msgs = peerlog.parse_claude(path, False) if agent == "claude" else peerlog.parse_codex(path, False)
    return [t for (ts, role, t) in msgs
            if role == "assistant" and since < peerlog._ts_key(ts) <= until + 2]


def find_poke_ts(agent, path, probe):
    """poke の nonce（relay-id:xxxx）を含む user メッセージのタイムスタンプ(epoch)を
    対象ログから探す。無ければ None。

    配達確認と応答帰属の source of truth。busy 中のキュー注入を許した結果、
    画面ベースの送信検証は「既に実行中バッジが出ている」ため無効化され、
    poke 直後の壁時計 since では「注入時に走っていた先行ターンの完了」を
    応答と誤検知する（Codex レビュー指摘）。nonce はキュー配達されて初めて
    ログに user メッセージとして現れるため、
      ・nonce がログに在る = 配達成立（Enter 失敗ならキューにも入らず現れない）
      ・完了 ts > nonce ts = その完了は poke より後に始まった応答
    が画面状態と無関係に判定できる。"""
    try:
        msgs = peerlog.parse_claude(path, False) if agent == "claude" else peerlog.parse_codex(path, False)
    except OSError:
        return None
    for ts, role, text in msgs:
        if role == "user" and probe in text:
            return peerlog._ts_key(ts)
    return None


def make_fragment(text, n=48):
    """送信検証用に本文の最初の非空行（先頭 n 字）を返す。改行は経路によって \\r 化する
    ため複数行は使わない。極端に短ければ None（= 任意の入力行の追記で確認）。"""
    for line in (text or "").splitlines():
        s = line.strip()[:n].strip()
        if s:
            return s if len(s) >= 3 else None
    return None


def _is_compact_boundary(d):
    return d.get("type") == "system" and d.get("subtype") == "compact_boundary"


def _boundary_marker(d):
    """compact_boundary レコードの一意 marker。uuid 優先。uuid 欠落境界が連続しても識別できる
    よう、固定値ではなく timestamp / logicalParentUuid / レコード hash から一意値を作る（Codex）。"""
    uid = d.get("uuid")
    if uid:
        return uid
    import hashlib
    blob = peerlog.json.dumps(d, sort_keys=True, default=str, ensure_ascii=False)
    return "cb:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def latest_compact_boundary(path, tail_bytes=1_000_000):
    """Claude の context compaction は同じ JSONL に `{type:"system", subtype:"compact_boundary"}`
    を追記する（path/inode 不変・size 増加）。その最新境界の一意 marker を返す（無ければ None）。
    schema latch の世代 identity に混ぜて「同一ログでも compaction 後は未確認へ戻す」ために使う
    （P1-4 / Codex）。末尾のみ読むので巨大ログでも安い。"""
    marker = None
    for d in read_records(path, tail_lines=0, tail_bytes=tail_bytes):
        if _is_compact_boundary(d):
            marker = _boundary_marker(d)
    return marker


def records_since_compaction(records):
    """レコード列を《最新 compact_boundary 以降（境界行自体を含む）》へ切り詰めて返す。境界が
    無ければそのまま。compaction 後の新世代 schema probe が境界前の drift を引きずらないため
    （P1-4 / Codex）。"""
    for i in range(len(records) - 1, -1, -1):
        if _is_compact_boundary(records[i]):
            return records[i:]
    return records


def codex_response_complete(path, probe, allow_position_fallback=False):
    """rollout から nonce の user メッセージを含むタスク（turn_id）を特定し、
    (そのタスクの task_started ts, 同 turn_id の task_complete ts or None) を返す。
    nonce 未発見・所属タスク不明なら (None, None)。

    実ログの並び（2026-08-15 実測）は
        task_started → response_item{message, role=user, nonce} → … → task_complete
    で、アイドル注入でも nonce は task_started の「後」に記録される。よって
    「nonce 後の task_started」を応答開始とみなす前提は idle 注入を全棄却する
    （実バグ）。正しくは Codex 指摘のとおり turn_id ペアリング:
      nonce の user アイテム自身が internal_chat_message_metadata_passthrough.turn_id
      を持つ（実ログ確認済み）ので、それをキーに同 turn_id の task_started /
      task_complete を対応付ける。キュー投入時に「時刻上の直前 start」が先行タスク
      を指す誤帰属は、位置推定ではなく ID 対応でそもそも起こらない。
    turn_id メタデータが欠落した nonce では帰属が確定できない。既定は fail-closed で
    (None, None) を返す（位置推定は queue 投入時に先行タスクを誤帰属し得る）。
    allow_position_fallback=True に限り従来の「直前の task_started」への位置フォールバックを
    行うが、これは **診断/表示用途専用**（P1-3）。自律運転の応答帰属ゲート response_done は
    このフォールバックを《一切使わない》— 誤帰属した応答で停止 sentinel 判定・レビュー転送・
    プラン自動承認へ進むのは危険なため、turn_id で確定できなければ compat mode でも帰属不能の
    まま fail-closed（待機＝人間判断）にする。"""
    starts, completes = [], []
    nonce_ts = None
    nonce_turn = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
          for line in fh:
            try:
                d = peerlog.json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            p = d.get("payload") or {}
            if t == "event_msg" and p.get("type") == "task_started":
                starts.append((peerlog._ts_key(d.get("timestamp")), p.get("turn_id")))
            elif t == "event_msg" and p.get("type") == "task_complete":
                completes.append((peerlog._ts_key(d.get("timestamp")), p.get("turn_id")))
            elif nonce_ts is None and t == "response_item" \
                    and p.get("type") == "message" and p.get("role") == "user":
                if any(probe in (b.get("text") or "")
                       for b in (p.get("content") or []) if isinstance(b, dict)):
                    nonce_ts = peerlog._ts_key(d.get("timestamp"))
                    meta = p.get("internal_chat_message_metadata_passthrough") or {}
                    nonce_turn = meta.get("turn_id")
    except OSError:
        return (None, None)
    if nonce_ts is None:
        return (None, None)
    if nonce_turn:
        anchor = next((ts for ts, tid in starts if tid == nonce_turn), nonce_ts)
        comp = next((ts for ts, tid in completes if tid == nonce_turn), None)
        return (anchor, comp)
    # メタデータ（turn_id）欠落: 既定は帰属不能として fail-closed（(None, None)）。位置推定は
    # 明示的な compatibility mode でのみ許す（P1-3）。
    if not allow_position_fallback:
        return (None, None)
    # フォールバック（compatibility mode）: 時刻上の直前 start を所属タスクとみなす
    turn = None
    for ts, tid in starts:
        if ts <= nonce_ts:
            turn = (ts, tid)
        else:
            break
    if turn is None:
        return (None, None)
    comp = next((ts for ts, tid in completes
                 if tid == turn[1] and ts > nonce_ts), None)
    return (turn[0], comp)


def claude_response_attributed(path, probe):
    """最後の assistant エントリの祖先チェーンに nonce の user エントリが含まれるか
    （= その完了ターンは poke を読んで走った応答か）。

    Claude Code は busy 中の投入を「実行中ターンへのターン内注入」または「次ターンの入力」
    として扱うが、どちらでも応答側のエントリチェーンは nonce エントリを祖先に持つ。
    タイムスタンプ比較と違いキュー順序に依存しない（Codex レビュー指摘の UUID 対応付け）。

    ★ context compaction の橋渡し（2026-08-23 実機バグ）: Claude Code は文脈圧縮時に
    `{type:"system", subtype:"compact_boundary", parentUuid:None}` の新ルートを書き、
    parentUuid チェーンを切断する。「poke → 圧縮 → 応答」の順だと応答側チェーンが境界ルートで
    止まり pre-compaction の nonce に到達できず、正当な応答が永久に棄却されてループが停止する
    （本セッションで実発生）。境界エントリは切れた parentUuid の代わりに **正確な圧縮前祖先**を
    指す `logicalParentUuid` を持つ（実ログの全 compact_boundary で確認）。よって境界では
    logicalParentUuid を親として通常の祖先探索を続ければ、位置ヒューリスティックのように別
    ブランチの nonce を誤帰属せず、真の祖先だけを辿れる。logicalParentUuid が無い境界は
    **fail-closed**（そこで打ち切り＝False 側）にする。"""
    parent = {}           # uuid -> 次に辿る uuid（境界では logicalParentUuid、通常は parentUuid）
    last_assist_uuid = None
    nonce_uuid = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
          for line in fh:
            try:
                d = peerlog.json.loads(line)
            except ValueError:
                continue
            u = d.get("uuid")
            if u:
                if d.get("type") == "system" and d.get("subtype") == "compact_boundary":
                    # 圧縮は parentUuid を None にする。logicalParentUuid が真の祖先。
                    # 欠落時は None のまま＝この境界で探索が止まる（fail-closed）。
                    parent[u] = d.get("logicalParentUuid")
                else:
                    parent[u] = d.get("parentUuid")
            t = d.get("type")
            if t == "assistant":
                last_assist_uuid = u
            elif t == "user" and nonce_uuid is None:
                content = ((d.get("message") or {}).get("content"))
                if isinstance(content, str):
                    hit = probe in content
                else:
                    hit = any(probe in (b.get("text") or "")
                              for b in (content or []) if isinstance(b, dict))
                if hit:
                    nonce_uuid = u
    except OSError:
        return False
    if not (last_assist_uuid and nonce_uuid):
        return False
    seen = set()
    u = last_assist_uuid
    while u and u not in seen:
        if u == nonce_uuid:
            return True
        seen.add(u)
        u = parent.get(u)
    return False
