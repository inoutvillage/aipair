"""aipair CLI / 引数解析 — argparse パーサ構築と AIPAIR_* 環境変数の既定値読み
（D3 relay 分割 / P2-1 増分6）。relay.py を「引数解析／依存構築／起動／exit」へ寄せるための
CLI 層。env 由来で採用した既定値は ENV_USED に記録し、起動バナーが「無言で効かせない」よう可視化
する。tests/relay-parsers.py の EnvHelpers / argparse defaults（launch-cmds 経由）で被覆。
"""
import argparse
import os
import sys

from . import __version__
from .review_protocol import DEFAULT_POKE_CLAUDE

ENV_USED = []          # 起動ログで「env 由来」を可視化する（無言で効かせない）


def _env_str(name, default):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    ENV_USED.append(f"{name}={v}")
    return v


def _env_int(name, default):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        n = int(v)
    except ValueError:
        n = -1
    if n < 1:
        # 設定ミスを黙って既定値に落とすと「指定したのに効かない」が起きる → 即エラー
        print(f"aipair-relay: {name} は 1 以上の整数で指定してください（実際の値: {v!r}）",
              file=sys.stderr)
        sys.exit(2)
    ENV_USED.append(f"{name}={n}")
    return n


def _env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    on = v.strip().lower() not in ("0", "false", "no", "off")
    ENV_USED.append(f"{name}={v}" + ("" if on else "（off）"))
    return on


def build_parser(description=""):
    ap = argparse.ArgumentParser(description=description,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version="aipair-relay " + __version__)
    ap.add_argument("--session", help="tmux session (default: current)")
    ap.add_argument("--dir", default=os.getcwd(), help="working directory (default: cwd)")
    ap.add_argument("--stop", default=_env_str("AIPAIR_STOP", "[AIPAIR_REVIEW_OK]"),
                    help="制御 sentinel（'||'-separated）。最終メッセージの先頭行が完全一致で成立。"
                         "default [AIPAIR_REVIEW_OK] / env AIPAIR_STOP")
    ap.add_argument("--stop-side", default=_env_str("AIPAIR_STOP_SIDE", "codex"),
                    choices=["codex", "claude", "both"],
                    help="whose message ends the loop (default codex / env AIPAIR_STOP_SIDE)")
    ap.add_argument("--max-rounds", type=int, default=_env_int("AIPAIR_MAX_ROUNDS", 20),
                    help="safety cap (default 20 / env AIPAIR_MAX_ROUNDS)")
    ap.add_argument("--endless", action="store_true", default=_env_bool("AIPAIR_ENDLESS"),
                    help="停止ワードで終了せず「次のタスクへ」を促し続ける連続モード。"
                         "終端は Codex の --all-done 宣言のみ（env AIPAIR_ENDLESS）")
    ap.add_argument("--no-endless", action="store_true",
                    help="AIPAIR_ENDLESS が効いている環境で、この1本だけ連続モードを切る")
    ap.add_argument("--next-ask", default=_env_str("AIPAIR_NEXT_ASK", "[AIPAIR_NEXT]"),
                    help="endless: Claude 側の手持ちが尽きた合図 sentinel（default [AIPAIR_NEXT] / env AIPAIR_NEXT_ASK）")
    ap.add_argument("--all-done", default=_env_str("AIPAIR_ALL_DONE", "[AIPAIR_ALL_DONE]"),
                    help="endless: Codex 側が残タスク無しを宣言する終端 sentinel（default [AIPAIR_ALL_DONE] / env AIPAIR_ALL_DONE）")
    ap.add_argument("--human-required",
                    default=_env_str("AIPAIR_HUMAN_REQUIRED", "[AIPAIR_HUMAN_REQUIRED]"),
                    help="endless: Codex 側が『残りは人間対応・外部依存の [!] のみ』を宣言する終端 sentinel。"
                         "relay の task-list 分類が BLOCKED の時のみ honor し exit 8 で停止する"
                         "（default [AIPAIR_HUMAN_REQUIRED] / env AIPAIR_HUMAN_REQUIRED）")
    ap.add_argument("--task-list", default=_env_str("AIPAIR_TASK_LIST", "tasks/todo.md"),
                    help="endless: 次タスクの唯一の根拠にするタスクリスト（default tasks/todo.md / env AIPAIR_TASK_LIST）")
    ap.add_argument("--gate", default=_env_str("AIPAIR_GATE", None),
                    help="停止ゲート: 停止ワード検出後にこのシェルコマンドを --dir で実行し、成功（exit 0）した時だけ"
                         "停止／次タスクへ進む。失敗なら出力を添えて Claude に差し戻す（env AIPAIR_GATE。既定: 無し）")
    ap.add_argument("--gate-timeout", type=int, default=_env_int("AIPAIR_GATE_TIMEOUT", 600),
                    help="ゲートコマンドのタイムアウト秒（既定 600 / env AIPAIR_GATE_TIMEOUT）")
    ap.add_argument("--gate-rounds", type=int, default=_env_int("AIPAIR_GATE_ROUNDS", 3),
                    help="ゲート失敗で差し戻す上限回数。到達で exit 6（既定 3 / env AIPAIR_GATE_ROUNDS）")
    ap.add_argument("--start-side", default="claude", choices=["claude", "codex"],
                    help="who acts first (default claude)")
    ap.add_argument("--poll", type=float, default=3.0, help="poll seconds (default 3)")
    ap.add_argument("--busy-wait", type=int, default=90,
                    help="poke 前に相手ペインのアイドルを待つ上限秒。超過後は中止せず注入を続行する（既定90）")
    ap.add_argument("--settle", type=float, default=1.2, help="settle seconds before relaying")
    ap.add_argument("--poke-claude", default=DEFAULT_POKE_CLAUDE)
    ap.add_argument("--poke-codex", default=None, help="default references the stop phrase")
    ap.add_argument("--plan-rounds", type=int, default=5,
                    help="max plan-review rounds per plan (default 5)")
    ap.add_argument("--plan-ok", default=_env_str("AIPAIR_PLAN_OK", "[AIPAIR_PLAN_APPROVED]"),
                    help="Codex のプラン承認 sentinel。先頭行完全一致でのみ承認（default [AIPAIR_PLAN_APPROVED] / env AIPAIR_PLAN_OK）")
    ap.add_argument("--allow-untested-dialogs", action="store_true",
                    default=_env_bool("AIPAIR_ALLOW_UNTESTED_DIALOGS"),
                    help="claude/codex の版が検証済みと違ってもプラン承認・質問リレーの自動操作を続ける"
                         "（既定: 不一致なら自動 OFF / env AIPAIR_ALLOW_UNTESTED_DIALOGS）")
    ap.add_argument("--no-version-gate", action="store_true", default=_env_bool("AIPAIR_NO_VERSION_GATE"),
                    help="起動時の claude/codex 版チェックをしない（env AIPAIR_NO_VERSION_GATE）")
    ap.add_argument("--allow-untested-schema", action="store_true",
                    default=_env_bool("AIPAIR_ALLOW_UNTESTED_SCHEMA"),
                    help="ログ JSONL schema がコア relay の依存と不一致でも継続（fail-open・ダイアログ自動操作は OFF）。"
                         "既定は不一致なら fail-closed で停止（exit 7） / env AIPAIR_ALLOW_UNTESTED_SCHEMA")
    ap.add_argument("--no-schema-probe", action="store_true",
                    default=_env_bool("AIPAIR_NO_SCHEMA_PROBE"),
                    help="起動時/実行時の JSONL schema feature-probe をしない（env AIPAIR_NO_SCHEMA_PROBE）")
    ap.add_argument("--no-plan-review", action="store_true",
                    help="ignore the plan-approval dialog (pre-existing behavior)")
    ap.add_argument("--question-rounds", type=int, default=5,
                    help="連続質問リレーの上限（Claudeのターン完了でリセット、default 5）")
    ap.add_argument("--no-question-relay", action="store_true",
                    help="AskUserQuestionダイアログの自動リレーを無効化")
    ap.add_argument("--claude-log", help="pin Claude session jsonl (adopt an existing session)")
    ap.add_argument("--codex-log", help="pin Codex rollout jsonl (adopt an existing session)")
    ap.add_argument("--adopt", action="store_true",
                    help="既存セッションを自動採用: 最新のClaude jsonl と cwd一致の最新Codex rollout を"
                         "ピン留めする（同プロジェクトでClaudeが複数動いていると最新更新のものが選ばれる。"
                         "確実に指定したい場合は --claude-log/--codex-log で明示ピン）")
    ap.add_argument("--no-color", action="store_true")
    return ap
