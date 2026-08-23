"""aipair schema guard — relay のランタイム JSONL schema 監視（D3 relay 分割 / P2-1 増分1）。

モノリシックな relay メインループから《schema_watch / schema_guard》を切り出してクラス化した。
各エージェントの (agent, ログ世代) 単位の latch と identity を《このオブジェクトが所有》し、poll
ごとに追跡ログのスキーマがドリフトしたかを判定して、fail-open（degrade：ダイアログ自動操作 OFF）
または fail-closed（停止：a.schema_stop=True → relay が exit 7）へ倒す。純関数
(corelib.schema_should_reprobe / schema_latch_step) と probe 関数を注入で受け取り、tmux/画面には
一切依存しないので tests/relay-parsers.py で単体被覆できる（従来は nested closure でテスト不能だった）。

入力（コンストラクタ）:
  a                        argparse.Namespace（読み: no_schema_probe / allow_untested_schema、
                           書き: schema_stop / schema_mismatch / no_plan_review / no_question_relay）
  tracked                  {"claude": path|None, "codex": path|None}（relay と共有する可変 dict）
  probe_log_schema(agent, path) -> (status, reason)   実ログを読む probe（tail-seek）
  latest_compact_boundary(path) -> marker|None        claude compaction 世代 marker
  dim(msg) / warn(msg, bell=False)                    出力フック（relay 側の配色・ベル）

出力: watch() は副作用（a.* の更新・dim/warn）。guard() は「fail-closed 停止すべきか」を bool で返す。
"""
import os

from .corelib import schema_should_reprobe, schema_latch_step


class SchemaGuard:
    def __init__(self, a, tracked, probe_log_schema, latest_compact_boundary, dim, warn):
        self.a = a
        self.tracked = tracked
        self._probe = probe_log_schema
        self._latest_compact_boundary = latest_compact_boundary
        self._dim = dim
        self._warn = warn
        # None→"ok-seen"（監視継続）／"mismatch"（同一世代で終端）。identity は最後に見たログ世代
        # ((path,dev,ino), size, marker)。世代切替/縮小/compaction で latch をリセットし再 probe。
        self.latched = {"claude": None, "codex": None}
        self.ident = {"claude": None, "codex": None}

    def _identity(self, agent, path):
        """追跡ログの世代 identity ((path, dev, ino), size, marker)。pathname だけだと同一 path の
        inode 置換や truncate を見逃すため stat まで見る。claude は compaction（同一 path/inode に
        compact_boundary 追記）の marker も混ぜる。size が変わった時だけ末尾を読み、変化なしは前回
        marker を再利用して無駄読みを避ける（codex は marker なし）。"""
        try:
            st = os.stat(path)
            gen, size = (path, st.st_dev, st.st_ino), st.st_size
        except OSError:
            gen, size = (path, None, None), None
        prev = self.ident[agent]
        prev_size = prev[1] if prev else None
        prev_marker = prev[2] if prev else None
        if agent == "claude" and size != prev_size:
            marker = self._latest_compact_boundary(path)
        else:
            marker = prev_marker
        return (gen, size, marker)

    def watch(self):
        """追跡ログを probe し、ドリフトなら degrade（fail-open）／停止要求（fail-closed）へ倒す。
        `aipair loop` は起動時ログが無いため、実ドリフトはここ（実行時・最初にターンが出た時）で
        捕まえる。"mismatch" はその世代の終端、"ok-seen"/None は監視継続（veto-only の delivery/
        dialog が後からドリフトするのを捕捉するため ok でも latch しない）。"""
        a = self.a
        if a.no_schema_probe:
            return
        for agent in ("claude", "codex"):
            path = self.tracked[agent]
            if not path:
                continue
            ident = self._identity(agent, path)
            reset, skip = schema_should_reprobe(self.latched[agent], self.ident[agent], ident)
            if reset:
                self.latched[agent] = None
            self.ident[agent] = ident
            if skip:
                continue
            status, reason = self._probe(agent, path)
            self.latched[agent], action = schema_latch_step(self.latched[agent], status, a.allow_untested_schema)
            if action == "ok":
                self._dim(f"{agent} ログschema OK（{reason}）")   # 一度だけ通知。以降も監視は続ける
            elif action in ("stop", "degrade"):
                self._warn(f"│ ■ {agent} のログ JSONL schema がコア relay の依存と不一致（{reason}）。"
                           "ターン検出／応答帰属が誤動作する可能性があります。claude/codex の版と"
                           " TESTED schema を確認してください。", bell=True)
                a.schema_mismatch = True
                if action == "degrade":
                    a.no_plan_review = True       # fail-open: continue, degrade dialog automation
                    a.no_question_relay = True
                else:
                    a.schema_stop = True          # fail-closed: stop the loop (exit 7)
            # "unverified" → 監視継続

    def guard(self):
        """freshly (re)ロックしたログを《完了判定・poke の前に》probe し、override 無しの schema
        mismatch なら fail-closed 停止を要求する（True を返す）。ロック直後に必ず呼ぶことで
        「同一ループ内で probe 前に1回自動操作される」経路を塞ぐ。"""
        self.watch()
        if self.a.schema_stop:
            self._warn("│ ■ ログschema不一致を検出 → fail-closed で停止します（exit 7）。")
            return True
        return False
