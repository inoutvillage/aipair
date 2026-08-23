# セキュリティポリシー / Threat Model

aipair は **Claude Code と Codex CLI を tmux 上で並走させ、相互レビューを自走させる**ローカルツールです。
本書は aipair 自体が持つ攻撃対象面（attack surface）と、その境界・緩和策・安全な使い方を明文化します。

> **English:** To report a vulnerability, use GitHub's **private vulnerability reporting**
> (repo → *Security* → *Report a vulnerability*). Please do **not** open a public issue for
> security bugs. A threat-model summary is at the end of this file.

---

## 脆弱性の報告

- **公開 issue を開かないでください。** GitHub の **Security → “Report a vulnerability”**（private advisory）で報告してください。
- 再現手順・影響範囲・想定される攻撃者モデルを添えてください。
- aipair は個人開発の OSS です。SLA はありませんが、妥当な範囲で速やかに対応します。修正は main へマージし、影響が大きいものは advisory を公開します。

## 対象バージョン

- セキュリティ修正は **`main` の最新**に対してのみ提供します（タグ付きリリース運用は今のところありません）。
- Claude Code / Codex CLI の**検証済みバージョン**は README「必要環境」を参照。未検証版では版ゲートが安全側（TUI 自動操作 OFF）に倒れ、**ログ schema が不一致なら既定で fail-closed 停止（exit 7）**します（`--allow-untested-schema` で継続可）。

---

## 信頼モデル（前提）

aipair は次を前提に設計されています。**この前提が崩れる使い方は攻撃対象面が一気に広がります。**

1. **単一ユーザーのローカルマシン。** tmux ソケットはユーザー専用（`/tmp/tmux-<uid>/`, 0700）。同一 uid の別プロセスはあなたの権限をすでに持っている、とみなします。
2. **リポジトリ／タスクの内容は、`--unsafe`・`aipair loop` を使う時点であなたが信頼している。** aipair は**サンドボックスを提供しません**。エージェントはあなたの権限で動きます。
3. **上流 CLI（Claude Code / Codex CLI）自体は信頼できる。** それらの脆弱性は上流の責任範囲です。

## 攻撃対象面と緩和策

### 1. 権限バイパス実行（最大のリスク）
`aipair loop` は `--unsafe`（`AIPAIR_UNSAFE=1`）を**必須**とし、`claude --dangerously-skip-permissions` /
`codex --dangerously-bypass-approvals-and-sandbox` で両エージェントを起動します。この状態では**エージェントは
許可プロンプト無しに任意のシェルコマンド実行・ファイル編集・git 操作**を行えます。

- **脅威:** 信頼できないリポジトリやタスク説明に対して `aipair loop` を回すことは、**あなたの権限での任意コード実行**と等価です。プロンプトインジェクション（リポジトリ内の悪意ある指示・issue 本文・依存パッケージの README 等）が、そのまま破壊的操作や外部送信につながり得ます。
- **境界／緩和:**
  - 権限バイパスは **opt-in**。既定の対話起動（`aipair`）は通常の許可プロンプトを維持します。
  - `aipair loop` は `--unsafe` を付けないと**起動を拒否**します（無言でバイパスしません）。
  - aipair は追加の隔離を提供しません。**信頼できない作業は使い捨て環境（コンテナ／VM／専用ユーザー）で。**

### 2. トランスクリプト読取（`peer` / relay）
`peer` と relay は `~/.claude/projects/**/<uuid>.jsonl` と `~/.codex/sessions/**/rollout-*.jsonl` を読みます。
これらには**会話の全履歴**（貼り付けた秘密・PII を含む）が入っています。

- **脅威:** ペアの相手（および同一 uid でログを読めるプロセス）は、もう一方の全履歴を参照できます。Claude に貼った秘密を Codex 側の relay/peer が読めます。
- **⚠ クロスプロバイダー境界（重要）:** aipair の**プロセス自体**はログを外部へ送信しません（ネットワーク接続を開きません）。しかし `peer`/relay の出力は**相手エージェントのツール結果**になり、そのエージェントの CLI が**自社クラウドへアップロード**します。つまり——
  - Codex が `peer`（＝Claude のトランスクリプト）を読むと、その内容は **OpenAI** へ送られ得る。
  - Claude が `peer`（＝Codex のトランスクリプト）を読むと、その内容は **Anthropic** へ送られ得る。
  - 結果として、**一方のプロバイダーに紐づく会話履歴・秘密が、もう一方のプロバイダーへ渡り得ます**。これは aipair が「両者を突き合わせる」設計上、不可避の情報フローです。
- **境界／緩和:**
  - peer/relay は起動ペアのセッションに **pin**（Claude=`--session-id`、Codex=`/proc` のプロセス実体、フォールバックは起動 epoch）し、無関係な別セッションを誤読しないようにしています。ただし**同一 uid のログは読める**前提です。
  - **対策:** ペア稼働中に秘密を貼らない（どちらのプロバイダーにも渡り得る）。規制・契約でプロバイダーを跨げないデータは aipair のペアに載せない。`~/.claude` / `~/.codex` を第三者と共有しない。

### 3. tmux キーストローク注入（relay の poke）
relay はエージェントのペインへキー入力（poke）を送ってターンを駆動します。

- **脅威:** tmux ソケットに書ける主体はペインへ注入できます（ソケットはユーザー専用 0700）。また relay の**ターン検出・停止ワード判定はトランスクリプトの内容を読む**ため、内容に紛れ込んだ停止ワードや偽ダイアログ文字列が relay を誤誘導（偽の停止／偽の前進）し得ます。
- **境界／緩和:**
  - relay は自ペアの解決済みペイン（`@aipair-*-pane` / ヒューリスティック）だけを poke し、`TMUX_PANE` 除外で自ペインを対象にしません。
  - 停止・承認は**専用 sentinel（`[AIPAIR_REVIEW_OK]` 等）が最終メッセージの先頭行に単独で**置かれた時のみ成立（否定文・引用・文中言及で誤停止/誤承認しない）。
  - **版ゲート**が UI 変更を検知して TUI 自動操作を OFF に倒し、**schema ゲート**はログ形状ドリフトを検知すると既定で **fail-closed 停止（exit 7）**します（誤帰属したログでの自律運転を止めるため。`--allow-untested-schema` で fail-open 継続）。
  - **停止ゲート**（`--gate`）で「停止ワード検知後に機械的検査（例: テスト）を通った時だけ停止／次へ」を強制できます。

### 4. 自律的な git / ネットワーク
バイパスモードでは、エージェントが**自律的に commit / push** し得ます。

- **脅威:** 秘密・PII を含む push（履歴は不可逆）、サプライチェーン操作、外部への送信。
- **境界／緩和:** aipair 自体は push しません（**エージェントの判断**です）。**aipair が導入する周知ブロック（`templates/claude-md-block.md` / `templates/codex-agents-block.md`）は push や PII の確認を強制しません**——ペア連携の説明だけです。push を止めたいなら**利用者側**で用意してください: リモートを絞る、`pre-push` フックで push をブロック、レビュー後のみ push を許す運用など。

### 5. グローバル指示の注入（インストーラ）
`aipair-install.sh` は `~/.claude/CLAUDE.md` と `~/.codex/AGENTS.md` にマーカー境界ブロックを書き込みます。

- **脅威:** 全プロジェクトのエージェント挙動を変える。
- **境界／緩和:** **マーカー境界のみ置換**（他の行はバイト一致を検証）、タイムスタンプ付きバックアップ、`--no-global-instructions`（`AIPAIR_NO_GLOBAL_INSTRUCTIONS=1`）で完全スキップ。

### 6. テストハーネスの tmux
`tests/` のうち **tmux を実際に起動するもの**（`install-global-optout` / `install-upgrade` /
`env-forward` / `launch-cmds` / `session-name` / `relay-here-libcheck`）は、実 `tmux` を**一意な
`-L <test-socket>`（専用サーバー）へ転送する wrapper を PATH に置き**、`#{socket_path}` が専用ソケット
であることを**検証してから**実行し、後始末で `kill-server` ＋ソケットファイル削除まで行います。
これにより**実ユーザーの既定 tmux サーバー（本番ペア）を作成も kill もしません**。`broadcast-blocks` は
tmux を起動せず（テンプレの文字列検査のみ）、`run-all` も同様です。

## スコープ外（aipair が守らないもの）

- **エージェントのサンドボックス化。** OS レベルの隔離（コンテナ／VM／専用ユーザー）を使ってください。
- **同一 uid の悪意あるローカルユーザー。** 既にあなたの権限を持っています。
- **上流 CLI（Claude Code / Codex CLI）の脆弱性。**
- **プロンプトインジェクションの完全防御。** 信頼できない内容に対する `--unsafe`/`loop` は本質的に危険です。

## 安全な使い方（推奨）

1. **信頼できるリポジトリ・タスクにだけ `aipair loop`（`--unsafe`）を使う。** 不明な内容は使い捨て環境で。
2. 迷ったら**対話起動（`aipair`）**を使い、許可プロンプトを残す。
3. **ペア稼働中に秘密を貼らない**（相手が読める）。
4. **push は要レビュー運用**にする（`pre-push` フック等）。
5. `~/.claude` / `~/.codex` の**ログを私有**に保つ（全履歴が入っている）。
6. `aipair loop` は放置運転になり得るので、**停止ゲート（`--gate`）**でマージ前の機械検査を挟む。

---

## Threat model (English summary)

aipair orchestrates two permission-bypassed AI CLIs in tmux and drives them autonomously.
Its trust model assumes a single-user local machine, user-private tmux sockets, and **trusted
repository/task content** when `--unsafe`/`aipair loop` is used. aipair **adds no sandboxing**:
agents run with your full privileges. Principal risks: (1) arbitrary code execution via
permission-bypass on untrusted content / prompt injection; (2) full conversation transcripts are
readable by the paired agent and same-uid processes — and **crucially, `peer`/relay output
becomes the OTHER agent's tool result, which its CLI uploads to that provider's cloud**, so
Claude-side history can reach OpenAI and Codex-side history can reach Anthropic (a cross-provider
data flow inherent to pairing; aipair's own process opens no network connection); (3) the relay
reads transcript **content** for turn/stop detection, so crafted text can mislead it (mitigated
by head-line-exact sentinel matching for stop/approval, version/schema gates (schema drift fails closed), and the optional `--gate`);
(4) autonomous git push (aipair's notice blocks do NOT gate it — use a `pre-push` hook);
(5) marker-bounded edits to global `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md` (opt-out via
`--no-global-instructions`). Out of scope: sandboxing the agents, a malicious same-uid local
user, and upstream CLI vulnerabilities. Run untrusted work in a disposable container/VM, keep
secrets out of paired sessions, and gate pushes behind review.
