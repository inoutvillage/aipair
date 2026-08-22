# aipair — Claude Code × Codex CLI, side by side in tmux

> **Overview (English).** `aipair` launches **Claude Code** and **OpenAI Codex CLI** in the same working
> directory as two tmux panes, plus a third "bridge" pane that merges both transcripts live. Each agent can
> read the other's conversation (`peer`), and an optional relay (`aipair loop`) makes them review each other's
> work autonomously until a stop phrase appears. A one-shot installer and a Claude Code skill
> (`/aipair-setup`) set everything up interactively, including tmux.
>
> **Quick start**
> ```bash
> git clone https://github.com/inoutvillage/aipair && cd aipair
> ./aipair-install.sh --check          # diagnose: OS, tmux >= 3.1, python3 >= 3.8, claude, codex, PATH …
> ./aipair-install.sh                  # install to ~/.local/bin (+ notice blocks, skills, smoke test)
> #   add --install-tmux to let it install tmux (uses sudo unless root / brew)
> #   or, inside this directory:  claude  →  /aipair-setup   (interactive, asks before anything that needs sudo)
> cd /path/to/your/project && aipair   # claude ┃ codex / bridge  (normal permission prompts)
> #   aipair loop --unsafe                # unattended review loop (needs permission-bypass)
> ```
> Requirements: tmux ≥ 3.1, python3 ≥ 3.8 (stdlib only), `claude`, `codex`, a UTF-8 locale.
> ⚠ **Safe by default.** Plain `aipair` starts each agent with its **normal permission prompts**.
> Permission-bypass (`claude --dangerously-skip-permissions`, `codex --dangerously-bypass-approvals-and-sandbox`)
> is opt-in via `--unsafe` (or `AIPAIR_UNSAFE=1`), and **`aipair loop` requires it** (the relay can't answer
> prompts). Override flags entirely with `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS`. Tested on Linux
> (WSL2 / AlmaLinux, Ubuntu and Arch containers); **macOS is untested**. The rest of this README is in Japanese.

---

`aipair` 一発で **Claude Code** と **Codex CLI** を同じ作業ディレクトリに並べて tmux 起動し、
互いの会話ログを参照しあえるようにするツール一式。下段の「bridge」ペインで両者の会話を
時系列マージしてライブ表示するので、人間も会話の流れを俯瞰できる。

```
┌───────────────┬───────────────┐
│    claude     │     codex     │   ← 2エージェント（同じ cwd）
├───────────────┴───────────────┤
│  bridge: peer-log both --watch │   ← 両者の会話をマージしてライブ表示（人間用）
└───────────────────────────────┘
```

すべてグローバル（`~/.local/bin`）に置き、ログは作業ディレクトリ単位で扱うので、
**どのプロジェクトでも同じコマンドが動く。**

---

## 目次

1. [必要環境](#必要環境)
2. [インストール](#インストール)（`aipair-install.sh` ／ `/aipair-setup` スキル）
3. [構成ファイル](#構成ファイル)
4. [使い方](#使い方)
5. [自走ループ（相互レビュー）](#自走ループ相互レビュー)
6. [仕組み](#仕組み)
7. [トラブルシュート](#トラブルシュート)
8. [カスタマイズ](#カスタマイズ)
9. [テスト・CI](#テストci)
10. [制約・既知の限界](#制約既知の限界)
11. [ライセンス](#ライセンス)

---

## 必要環境

| 必要 | 最小 | 検証済みバージョン（2026-08-21 実測） | 備考 |
|---|---|---|---|
| `tmux` | **≥ 3.1** | 3.2a | `split-window -l 30%`（割合指定）が 3.1 から。Ubuntu 20.04 の 3.0a は不可 |
| `python3` | **≥ 3.8** | 3.9.25 | `peer-log` / `aipair-relay` の本体。**標準ライブラリのみ**（pip 不要） |
| `claude` | — | 2.1.238 | Claude Code CLI（`npm install -g @anthropic-ai/claude-code`、要ログイン） |
| `codex` | — | 0.149.0 | OpenAI Codex CLI（`npm install -g @openai/codex`、要ログイン） |
| ロケール | UTF-8 | `en_US.UTF-8` | 停止ワード（日本語）や罫線を扱うため。非 UTF-8 だとインストーラが `[warn]` |

- 検証した OS: **WSL2（AlmaLinux 9.7 / dnf）**、**Ubuntu 24.04 コンテナ（apt）**、**Arch Linux コンテナ（pacman）**。
  **macOS（brew）は実機未検証**（GNU 固有のコマンドは使っていないが、動作保証はしない）。
- Windows ネイティブ（非 WSL）は対象外。

---

## インストール

### 方法 A: `aipair-install.sh`（冪等・非対話）

```bash
git clone https://github.com/inoutvillage/aipair && cd aipair
./aipair-install.sh --check                 # 診断のみ（無変更）。KEY=VALUE で状態を出力
./aipair-install.sh                         # 導入（sudo は一切使わない。tmux が無ければ案内して exit 3）
./aipair-install.sh --install-tmux          # tmux の導入を明示的に許可（root なら sudo 無し、非 root は sudo <pkg> install tmux）
./aipair-install.sh --vscode-tasks <dir>    # 併せて <dir>/.vscode/tasks.json を配置（既存があれば上書きしない）
```

やること（各ステップを `[ok]` / `[skip]` / `[warn]` / `[fail]` の 1 行で必ず出力。無言で飛ばす経路はない）:

1. OS / パッケージマネージャ（`dnf` / `apt` / `pacman` / `brew`）の判定
2. `tmux` の有無と版（≥ 3.1）。無ければ導入コマンドを表示して **exit 3**（`--install-tmux` 時のみ導入）
3. `python3`（≥ 3.8）/ `claude` / `codex` の有無。欠けていれば導入手順を案内して **exit 3**
4. ロケールが UTF-8 でなければ `[warn]`
5. 6 本を **`~/.local/bin` にコピー**（差分があるときだけ上書き。上書き前に `*.bak-<timestamp>` を残す）＋ `chmod +x`。
   配置後に `aipair-relay --help` 等が動くことを確認（同一ディレクトリ依存の検証）
6. スキル 2 つを `~/.claude/skills/aipair-setup/` `~/.claude/skills/aipair-relay/` にコピー（同じ退避ルール）
7. `~/.local/bin` が PATH に無ければ、追記すべき 1 行を表示して `[warn]`（rc ファイルは**書き換えない**）
8. **周知ブロック**を `~/.claude/CLAUDE.md` と `~/.codex/AGENTS.md` に入れる（下記）
9. （任意）`--vscode-tasks <dir>`
10. **疎通確認**: 一時ディレクトリで `aipair` を起動（`--version` フラグで TUI は立てない）→ tmux セッションと 3 ペインができることを確認 → 停止

#### 周知ブロック（`~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md`）

両エージェントに「`peer` で相手のログを読める」ことを教える英語のブロックを、
`<!-- aipair:start -->` 〜 `<!-- aipair:end -->` で囲んで**末尾に追記**する（正本: `templates/claude-md-block.md` / `templates/codex-agents-block.md`）。

- **再実行は既存ブロックを置換**する（二重追記しない）。同じ内容なら `[skip]`（ファイルに触らない）
- 書き換え前に `<file>.aipair-bak-<timestamp>` を作る
- 書き込み後に「ブロックを除いた残りが元ファイルとバイト一致」を検証し、一致しなければバックアップから復元して `[fail]`
- マーカーが片方しか無い／複数組ある場合は壊れた状態に重ねず `[fail]` で停止（手で直してから再実行）
- ファイルが無ければ新規作成（`~/.codex/` も作る）

#### 終了コード

| code | 意味 |
|---|---|
| 0 | 成功（`[warn]` を含むことがある） |
| 1 | 失敗（理由は stderr。ファイル配置・ブロック書き込み・疎通確認のいずれか） |
| 2 | 引数エラー |
| 3 | 依存不足で中断（導入手順を表示済み。何も配置していない）。`--check` も依存不足なら 3 を返す（出力は全部出す） |

### 方法 B: `/aipair-setup` スキル（対話形式）

clone 直後のディレクトリで `claude` を起動し、`/aipair-setup` と打つ（または「aipair をセットアップして」）。
スキルは `aipair-install.sh --check` で診断 → 表で報告 → **不足分だけ**を 1 件ずつ承認を取って導入 → 疎通確認の結果を報告する。
**sudo が要る操作（tmux 導入）は、実行するコマンドを原文で提示してから承認を取る**。`~/.claude/CLAUDE.md` や rc ファイルをスキルが直接編集することはない（すべてインストーラ経由）。
claude / codex / python3 が無い場合は導入手順を案内して終了する（スキルが代わりに入れない）。

### WSL2 の場合

Windows の VS Code（Remote-WSL でない、Windows フォルダとして開く運用）から起動するには、
`templates/vscode-tasks.json` をプロジェクトの `.vscode/tasks.json` に置く（`--vscode-tasks <dir>` で配置できる）。
タスクは `wsl.exe --cd "${workspaceFolder}" bash -ic …` の形で、**wsl.exe 自身が Windows パスを WSL の作業ディレクトリに変換**するため、
無修正で他プロジェクトに流用できる。
（`type:shell` は **PowerShell 経由**で実行されるため、`$(wslpath …)` のような入れ子引用符・`$()`・バックスラッシュ Windows パスは PowerShell に壊される。
`--cd` 方式はそれらを一切使わず回避する。）Linux ネイティブ / macOS で VS Code から使う場合は `wsl.exe --cd … bash -ic` の部分を `bash -ic` に書き換える。

---

## 構成ファイル

| パス | 役割 |
|---|---|
| `~/.local/bin/aipair` | tmux 3 ペイン起動ランチャ（サブコマンドあり） |
| `~/.local/bin/peer-log` | 指定 cwd の最新 Claude/Codex セッションを抽出・整形・ライブ追従 |
| `~/.local/bin/peer` | `$AI_PEER` を見て *相手* のログを表示する短縮版 |
| `~/.local/bin/aipair-relay` | 自走ループの Watcher（ターン完了を検知 → 相手ペインへ自動ポーク） |
| `~/.local/bin/aipair-relay-here` | 走行中のペアに relay を 1 本だけ点火する（どのペインからでも） |
| `~/.claude/skills/aipair-setup/` | 対話型セットアップ（Claude Code スキル） |
| `~/.claude/skills/aipair-relay/` | Claude から relay をオンデマンド点火するスキル |
| `~/.claude/CLAUDE.md` | 末尾に周知ブロック。Claude に `peer` の使い方を周知（全セッションで読まれる） |
| `~/.codex/AGENTS.md` | Codex に同様に周知（グローバル読込） |
| `<project>/.vscode/tasks.json` | VS Code「Tasks: Run Task」から起動（WSL2 向けテンプレ）。**無修正で全プロジェクト共通** |

🔒 **配置先は `~/.local/bin` 固定**: `aipair-relay-here` が `$HOME/.local/bin/aipair-relay` を参照し、
`aipair-relay` は**同じディレクトリ**の `peer-log` と `aipair-corelib`（純粋ヘルパ）を読み込む（`SourceFileLoader`）。
個別に symlink を張ったり別ディレクトリへ分散させたりしないこと（インストーラはコピーで一括配置する）。

---

## 使い方

### 1. コマンドラインから

```bash
aipair                 # カレントディレクトリで起動（既存セッションがあれば再アタッチ）
aipair <dir>           # 指定ディレクトリで起動
aipair loop   [dir]    # 相互レビューの自走ループ（→「自走ループ」節）
aipair attach [dir]    # アタッチのみ（未起動ならエラー）
aipair stop   [dir]    # セッション停止
aipair name   [dir]    # tmux セッション名を表示
```

セッション名は `aipair-<ディレクトリ名>-<正規化パスの sha1 先頭 12 桁>`（例: `…/my-project` → `aipair-my-project-1a2b3c4d5e6f`。正規化 = symlink 解決 + 大小文字を区別しない FS（WSL の `/mnt/*`。macOS の APFS も同じ扱いだが未検証）ではディスク上の綴りに統一。`/mnt/d/Work` と `/mnt/d/work` は同じ名前になる）。同名のセッションが**別ディレクトリ**のものだった場合（hash collision）は attach / stop せずエラー終了する。同名ディレクトリが別の場所にあっても衝突しない。旧形式 `aipair-my-project`（ハッシュ無し）で動いている既存セッションは、**同じディレクトリのもの**に限り `attach` / `stop` / `name` が自動で引き継ぐ。

**安全側が既定**: 素の `aipair` は各エージェントを**通常の許可プロンプト付き**で起動する。
権限バイパス（`claude --dangerously-skip-permissions` ／ `codex --dangerously-bypass-approvals-and-sandbox`）は
`--unsafe`（または `AIPAIR_UNSAFE=1`）で opt-in。**`aipair loop` は必須**（relay が許可プロンプトに答えられないため）。
起動フラグ全体を差し替えるなら `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS`（下の「カスタマイズ」）。

> ⚠️ **既定フラグについて**: 両エージェントは許可確認なしでコマンド実行・ファイル編集を行う。
> 信頼できる作業ディレクトリでだけ使い、不安なら `AIPAIR_CLAUDE_FLAGS= AIPAIR_CODEX_FLAGS= aipair` でフラグ無しで起動すること
> （その場合、自走ループは許可プロンプトで止まりうる）。

### 2. VS Code / Cursor / Antigravity から（WSL2）

`Ctrl+Shift+P` →「**Tasks: Run Task**」→ タスクを選択。
メインタスクは既定ビルドなので `Ctrl+Shift+B` でも一発起動できる（テンプレ: `templates/vscode-tasks.json`）。

| タスク | 動作 |
|---|---|
| 🤝 起動 / 再アタッチ | `aipair`（claude ┃ codex ／ 下段 bridge） |
| 🔁 相互レビュー・ループ | `aipair loop`（自走。Codex が「完了です」で停止） |
| 📜 統合ログ（bridge）だけ表示 | `peer-log both --watch`（アタッチせず会話を俯瞰） |
| 🛑 停止 | `aipair stop` |
| 🤖 claude 単体 / 🤖 codex 単体 | tmux 無しで片方だけ（`peer` 参照可） |

### 3. 相手のログを読む（各エージェントが叩く）

```bash
peer                       # 相手の最近の会話（aipair が AI_PEER を claude↔codex に設定）
peer --watch               # 相手をライブ追従
peer-log codex             # 明示指定（claude / codex / both）
peer-log both              # 両者を時系列マージ
peer-log codex --last 60   # 履歴を多めに（既定 40 件）
peer-log claude --tools    # ツール操作（Bash 実行など）も含める
peer-log codex --full      # セッション全体
```

`peer-log` の主なオプション: `--dir DIR`（既定 cwd）・`--last N`・`--full`・`--tools`・`--watch`・`--no-color`。

---

## 自走ループ（相互レビュー）

`aipair loop` で Claude と Codex が**交互に自動で会話**します（Watcher = `aipair-relay`）。

1. 起動後、**Claude ペインに最初の依頼を入力**（例「○○を実装して」）
2. Claude が実装完了（`end_turn`）→ relay が Codex に自動ポーク「`peer` で読んでレビューして」
3. Codex がレビュー完了（`task_complete`）→ relay が Claude に自動ポーク「`peer` で読んで修正して」
4. 2–3 を繰り返し、**Codex の発言に停止ワード「完了です」が出たら自動停止**（最大 20 往復で打ち切り）
   - 判定はターンの**最終メッセージの冒頭 100 字**（ターン途中の進捗ナレーションは見ない。
     文中の偶発的な「〜が完了です」での誤停止を防ぐため冒頭のみ）

### 日本語の既定値と変更方法

停止ワード・合図は日本語が既定。英語などに変えるには env（または relay のフラグ）で上書きする（**優先順位: CLI フラグ > 環境変数 > 既定値**）:

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `AIPAIR_STOP` | `完了です` | 停止ワード（`\|\|` 区切りで複数可）。例: `AIPAIR_STOP="LGTM\|\|完了です"` |
| `AIPAIR_STOP_SIDE` | `codex` | どちらの発言で止めるか（`codex` / `claude` / `both`） |
| `AIPAIR_MAX_ROUNDS` | `20` | 最大往復数（暴走防止） |
| `AIPAIR_ENDLESS` | （未設定＝off） | `1` で連続モード（→ 次節） |
| `AIPAIR_TASK_LIST` | `tasks/todo.md` | 連続モードの次タスクの根拠ファイル |
| `AIPAIR_NEXT_ASK` | `次のタスクをください` | 連続モード: Claude の手持ちが尽きた合図 |
| `AIPAIR_ALL_DONE` | `全タスク完了` | 連続モード: Codex の終端宣言 |
| `AIPAIR_UNSAFE` | （未設定＝安全） | `1`/`--unsafe` で権限バイパス起動（`aipair loop` は必須）。既定は通常の許可プロンプト |
| `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS` | （安全＝無し／`--unsafe`＝`--dangerously-…`） | 起動フラグ。明示指定は最優先。**ペイン内のシェルが解釈するシェル断片**（`"--model opus"` は 2 引数、`'--append-system-prompt "a b"'` の引用符も有効）。空文字でフラグ無し。**ただし `aipair loop` では危険フラグが必ず付与される**（空/カスタム指定にも追記。relay が許可プロンプトに答えられないため） |
| `AIPAIR_DRY_RUN` | （未設定＝off） | `1` で各ペインに打ち込む起動行を表示するだけで何も起動しない（設定確認・テスト用）。真偽値の読み方は `AIPAIR_ENDLESS` と同じ |
| `AIPAIR_GATE` | （未設定＝無し） | **停止ゲート**: 停止ワード検出後に実行するシェルコマンド（例 `npm test`）。成功した時だけ停止／次タスクへ。失敗は出力を添えて Claude に差し戻す（→ 下の「停止ゲート」） |
| `AIPAIR_GATE_TIMEOUT` / `AIPAIR_GATE_ROUNDS` | `600` / `3` | ゲートのタイムアウト秒／差し戻しの上限回数（到達で relay は exit 6） |
| `AIPAIR_ALLOW_UNTESTED_DIALOGS` | （未設定＝off） | `1` で、claude/codex が検証済み版と違っても**プラン承認・質問リレーの自動操作を続ける**（既定は不一致なら自動 OFF。→「版ゲート」） |
| `AIPAIR_NO_VERSION_GATE` | （未設定＝off） | `1` で起動時の版チェック自体をしない |

`AIPAIR_*_FLAGS` 以外の値は**そのまま 1 引数**として relay に渡る（`'`・空白・`$`・`;` を含んでも壊れない。launcher がシングルクォートで包む）。
不正値（`AIPAIR_MAX_ROUNDS=abc` / `0` / 負数、`AIPAIR_STOP_SIDE=typo`）は**既定へ落とさず exit 2** で即エラー。
`AIPAIR_ENDLESS` / `AIPAIR_DRY_RUN` は `0` / `false` / `no` / `off`（大小文字・前後空白は無視）で明示的に off。環境に残っている時、その 1 本だけ通常モードに戻すには `--no-endless`。

**3 つの起動経路すべてで効きます**:

| 起動 | 効き方 |
|---|---|
| `AIPAIR_MAX_ROUNDS=100 aipair loop <dir>` | ランチャーがフラグへ展開。tmux が env を引き継ぐので**ペア内の各ペインにも残る** |
| relay ペインから直接 `aipair-relay --adopt …` | relay 本体が env を読む（`aipair loop` 時に指定していれば**そのまま継承されている**） |
| 任意のペインから `aipair-relay-here` | `aipair-relay-here` が env をフラグへ展開して渡す（下記の理由で必須） |

> 🔴 **`aipair-relay-here` を Claude/Codex ペインから呼ぶ場合**、relay は bridge ペインへ
> `send-keys` で**コマンド文字列として**投入されるため、呼び出し側シェルの env は relay に届きません。
> そこで `aipair-relay-here` が env を**明示フラグへ展開**します。実際に何が渡るかは `--print` で確認できます。

- 起動バナーに `env 由来の既定値: …` を表示します（黙って効かせない）。
- **relay ペインのタイトルが実際の設定を名乗ります**（relay 本体が自分で書き換える。起動経路によらず正しい）。
  終了後も結果が残るので、何時間も前に終わった relay を「まだ回っている」と誤読しません。

  | 状態 | タイトル |
  |---|---|
  | 通常モード | `relay ● 1タスク / max 20 / 停止「完了です」/ Ctrl-C で停止` |
  | 連続モード | `relay ● endless / max 100 / 終端「全タスク完了」/ Ctrl-C で停止` |
  | 終了後 | `relay ■ 終了(全タスク完了) / 3往復` ／ `■ 終了(キャップ到達)` ／ `■ 終了(配達失敗)` ／ `■ 終了(停止ゲート失敗)` ／ `■ 中断` |

### 連続モード（endless）— 全タスクが尽きるまで止めない

既定は「1 タスク＝1 ループ」で、Codex の「完了です」で終わります。
**`AIPAIR_ENDLESS=1 aipair loop`**（または `aipair-relay --endless`）にすると、
停止ワードを**「このタスクのレビュー合格」**として扱い、ループを止めずに次のタスクへ進みます。

```
Claude 実装 ──▶ Codex レビュー
  ├ 指摘あり      → Claude が修正（従来どおり）
  └「完了です」    → Claude へ「合格。tasks/todo.md の次の1件へ」
                      └ 未チェック項目なし → Claude「次のタスクをください」
                           └─▶ Codex へ「リストから次を1件指示して」
                                 ├ 次タスク提示 → Claude 実装へ戻る
                                 └「全タスク完了」→ ■ ループ終了（exit 0）
```

- **終端は Codex の「全タスク完了」宣言だけ**です。`--max-rounds` は暴走防止のキャップとして残るので、
  連続モードでは大きめ（例 `AIPAIR_MAX_ROUNDS=100`）にしてください。
- **次タスクの根拠は `tasks/todo.md` の未チェック項目に限定**され、リスト外の新規提案を禁じる文面を
  Codex に送ります（放っておくと「改善案」が無限に湧いてスコープが膨らむため）。パスは `AIPAIR_TASK_LIST` で変更可。
- 合図の判定は既定モードと同じ **最終メッセージの冒頭 100 字**です。3 つの合図
  （完了です／次のタスクをください／全タスク完了）はいずれもこの窓に入った時だけ効きます。
  窓内に偶発的に書かれると誤検知しますが、いずれも**早く止まる/次に進む方向**に倒れます。

### relay の再点火（`aipair-relay-here`）

relay が終了して往復が止まった後、新しい仕事があれば **`aipair-relay-here`**（ペア内の claude / codex / bridge
どのペインからでも）で relay を 1 本だけ再点火できる（`--adopt` で既存ペアに乗る。bridge が busy なら二重起動を避けて exit 2）。
Claude からは `aipair-relay` スキルでも同じことができる。**オンデマンド専用＝自動再起動はしない**（「完了です」の直後に
無条件で再点火すると、Codex がまた即「完了です」→ 延々ループになるため）。

```bash
aipair-relay-here --print [rounds N] [stop "フレーズ"] [stop-side codex|claude|both]   # ドライラン（組み立てたコマンドを表示）
aipair-relay-here [rounds N] [stop "フレーズ"] [stop-side codex|claude|both]           # 本番
aipair-relay-here -- --endless --max-rounds 100                                        # relay 本体のフラグを素通し
```

### プラン承認ダイアログの自動処理（プランレビュー）

Claude がプランモードで **「Would you like to proceed?」の承認待ち**になると、
ターンが完了しないため従来はループが止まっていた。relay はこのダイアログを検知して自動処理する:

1. ダイアログからプランファイルのパス（`~/.claude/plans/*.md`）を読み取り、**Codex にレビュー依頼**
2. Codex の返答で分岐:
   - **修正要求** → 「Tell Claude what to change」を選択し、レビュー本文をペーストして **Enter**（Claude がプラン修正 → 再度ダイアログ → 繰り返し）
   - **承認**（冒頭に「プラン承認」）→ 「Yes, and bypass permissions」を選択して実装開始
   - **承認＋付帯コメント** → feedback をペーストして **shift+tab**（feedback 付き承認）
3. プランレビューは 1 プランにつき最大 5 回（`--plan-rounds`）。超過時はベルを鳴らして人間に委ねる

### 質問ダイアログの自動処理（質問リレー）

Claude が **AskUserQuestion（選択式の質問ダイアログ）** で停止すると、従来はターン完了を
検知できずループが停止し、poke もセレクタ UI に食われて届かなかった。relay はこれを検知して自動処理する:

1. **検知**: 番号付き選択肢＋「Chat about this」＋画面最下行のフッター（`Enter to select …`）
2. **全質問のスクレイプ**: →キーでタブを走査し 1 問ずつ画面から収集
   （未回答の tool_use はセッション jsonl に書かれないため、画面が唯一の完全ソース。→ は選択を確定しない非破壊キー）
3. **Codex へ 1 往復で依頼**: 全質問を poke に畳んで送信（「N 問目: 選択肢 M（ラベル）」形式で回答指示）
4. **「Chat about this」経由で配達**: chat 押下でダイアログは「User declined to answer questions」として解決しコンポーザへ戻る →
   Codex の回答本文を後追いメッセージとしてペースト送信。Claude は回答を読んで続行する
5. 連続上限 `--question-rounds` 回（Claude のターン完了でリセット）。超過・poke 失敗はベルを鳴らして停止し人間に委ねる

Codex のレビュー配達時（通常ループ）も、Claude が質問ダイアログ表示中なら poke ではなく
「Chat about this」経由で配達する（poke の nonce 数字が選択として誤解釈されるリスクの根本遮断）。

| relay オプション | 既定 | 意味 |
|---|---|---|
| `--plan-rounds N` | `5` | プランレビューの上限回数 |
| `--plan-ok WORD` | `プラン承認` | Codex の承認ワード |
| `--no-plan-review` | — | プランダイアログ処理を無効化（従来動作） |
| `--question-rounds N` | `5` | 質問リレーの連続上限（ターン完了でリセット） |
| `--no-question-relay` | — | 質問ダイアログ処理を無効化（従来動作） |
| `--claude-log PATH` | 自動 | 既存の Claude セッション jsonl を明示指定（relay を途中再起動する時に） |
| `--codex-log PATH` | 自動 | 既存の Codex rollout jsonl を明示指定（同上） |
| `--busy-wait SEC` | `90` | poke 前に相手ペインのアイドルを待つ上限秒 |

> relay は起動時点より**後に生まれた**セッションログに自動ロックする設計のため、
> ループ稼働中に relay だけ再起動する場合は `--claude-log` / `--codex-log` で既存ログを指定すること。

- **ポーク方式**: 本文は各自 `peer` で読むので長文・多行でも壊れない。
- 注入は `send-keys -l` → 配達確認 → 画面静止待ち → `Enter` → busy 確認（「text+Enter」を一発で送ると TUI が改行として解釈するため）。
- **停止**: relay ペイン（下段）で `Ctrl-C`、または「🛑 停止」タスク。

`aipair-relay` を直接呼べば `--poke-claude` / `--poke-codex`（ポーク文面）・`--start-side`（先攻）も変更可（`aipair-relay --help`）。

> ⚠️ **2 エージェントが自走するためトークン消費が大きい**。最大往復上限と `Ctrl-C` 即停止を常に意識してください。
> `aipair loop` はループが許可プロンプトで止まらないよう **`--unsafe`（または `AIPAIR_UNSAFE=1`）が必須**で、危険フラグ（`--dangerously-…`）付きで起動します。付けずに `aipair loop` すると起動を拒否します。

### relay の exit code（orchestrator 向け）

| code | 意味 |
|---|---|
| 0 | 停止ワード検知（正常完了）。連続モードでは Codex の「全タスク完了」宣言 |
| 2 | 起動エラー（session/pane 不明等）・env の不正値 |
| 3 | 最大往復キャップ到達 |
| 4 | poke 配達失敗 |
| 5 | プランレビュー/質問リレーの上限到達・選択肢欠落 |
| 6 | 停止ゲート（`--gate`）が `--gate-rounds` 回失敗 |
| 130 | Ctrl-C 中断 |

---

### 版ゲート（自動・claude/codex のバージョン）

プラン承認ダイアログや選択式質問の**自動操作は、CLI の画面（TUI）を文字列で読んで数字キーを送る**方式なので、
Claude Code / Codex CLI の版が変わると壊れうる。そこで relay は起動時に `claude --version` / `codex --version` を取得し、
**検証済み版（上の「必要環境」表）と一致しない、または取得できない**場合は、

- **プラン承認・質問リレーの自動操作だけを OFF**（起動ログに理由を表示）
- **poke による往復・transcript の読み取りは通常どおり継続**（＝ペアの相互レビューは動く）

とする。ダイアログで止まった時は人間が対応する。判断が変わったら:

```bash
aipair-relay --allow-untested-dialogs      # 版が違っても自動操作を続ける（AIPAIR_ALLOW_UNTESTED_DIALOGS=1）
aipair-relay --no-version-gate             # 版チェック自体をしない（AIPAIR_NO_VERSION_GATE=1）
```

検証済み版を上げたら `bin/aipair-relay` の `TESTED_VERSIONS` と README の表を**両方**更新すること（テスト `VersionGate` が両者の一致を前提にしている）。

### 停止ゲート（任意・`--gate`）

既定の停止条件は「Codex が本文冒頭に停止ワードを書く」= **エージェントの自己申告**で、品質を機械的に保証するものではない。
`--gate` を指定すると、停止ワードを検知した時点で **指定コマンドを作業ディレクトリで実行し、exit 0 の時だけ**停止（連続モードでは次タスクへ）する。

```bash
AIPAIR_GATE='npm test && npx tsc --noEmit' aipair loop      # env（tmux が引き継ぐので relay が読む）
aipair-relay --gate 'pytest -q' --gate-rounds 2              # フラグ
```

- 失敗時: 出力の末尾（40 行・1500 字まで、1 行に畳む）を添えて **Claude に差し戻し**、Codex には送らない。Claude が直して再びレビュー → 合格 → ゲート、の順で回る
- `--gate-rounds`（既定 3）回失敗したら人間の判断が必要として relay は **exit 6** で停止する
- `--gate-timeout`（既定 600 秒）超過は失敗扱い
- ゲートは `--stop-side` が claude / codex / both のどれでも、停止ワードを検知した側で走る。未指定なら従来どおり（挙動変更なし）

## 仕組み

- **ログの所在**
  - Claude: `~/.claude/projects/<cwd の非英数字を '-' に置換>/<sessionId>.jsonl`
  - Codex : `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`（先頭行 `session_meta.payload.cwd` で cwd 判定）
- **peer-log** … 指定 cwd の最新セッション JSONL を見つけ、user/assistant の本文だけを抽出整形。
  Codex がセッション冒頭に注入する AGENTS.md 本文などのメタは除外。
- **bridge（`--watch`）** … 両ログを追記監視し、新着メッセージをタイムスタンプ順にマージして流す。
- **相互参照の周知** … `aipair` が各ペインに `AI_SELF` / `AI_PEER` を設定。さらに
  `~/.claude/CLAUDE.md` と `~/.codex/AGENTS.md` の周知ブロックで「`peer` で相手ログを読める」と教えているので、
  両エージェントは必要時に自発的に相手を参照できる（読み取り専用。相手への送信は relay か人間が行う）。
- **ターン完了の検知**（relay）… Claude: 最新の `assistant` エントリの `stop_reason != "tool_use"`。Codex: 最新の `task_*` イベントが `task_complete`。

---

## tmux チートシート

| 操作 | キー |
|---|---|
| ペイン移動 | `Ctrl-b` → 矢印 / `Ctrl-b o` |
| ペインをズーム（全画面トグル） | `Ctrl-b z` |
| デタッチ（裏に残す） | `Ctrl-b d` |
| マウス | 有効（クリックでペイン選択・スクロール可） |

---

## トラブルシュート

- **`aipair: command not found`** → `~/.local/bin` が PATH にあるか確認（インストーラが `[warn]` で追記行を表示する）。新しいシェルを開く。
  VS Code タスクは `bash -ic`（インタラクティブ）なので `.bashrc` が読まれて解決する。
- **タスクが一覧に出ない** → `Ctrl+Shift+P` →「Reload Window」。
- **`(no codex session found for …)`** → そのディレクトリで Codex をまだ起動していない／cwd 不一致。
  `aipair` で両方を同じ cwd から起動すれば一致する。
- **色が出ない／文字化け** → パイプ経由では色を自動オフ。端末では自動オン。`--no-color` で常時オフ。文字化けはロケールを UTF-8 に。
- **停止できない** → `aipair name` で実際のセッション名を確認し、`tmux kill-session -t <name>`。
- **`tmux: split-window: size … invalid`（3.0 系）** → tmux が古い。≥ 3.1 に更新する（インストーラは版を見て `[fail]` にする）。
- **インストーラが `[fail] … damaged aipair block`** → `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md` の `<!-- aipair:start -->` / `<!-- aipair:end -->` が 1 組になるよう手で直してから再実行（壊れた状態には上書きしない設計）。
- **インストーラの smoke が失敗** → `tmux ls` で別ユーザー／別ソケット（`TMUX_TMPDIR`）を見ていないか確認。`aipair-aipair-smoke-*` という名前のセッションが残っていれば `tmux kill-session` で消してよい。
- **codex / claude が「ログインしていない」** → 各 CLI を一度単体で起動してログインしてから `aipair`。

---

## カスタマイズ

- **コマンド名を変える**: `~/.local/bin/aipair` をリネーム（PATH 上にあれば何でも可。他の 5 本はリネームしない）。
- **bridge の高さ / 左右比**: `aipair` 内の `split-window -l 30%`（下段の高さ）と `-l 50%`（codex の幅）を編集。
- **bridge の初期表示件数**: `aipair` 内の `peer-log both --watch --last 15` の数値。
- **起動フラグ / 安全モード**: 既定（安全）は**フラグ無し＝通常の許可プロンプト**。`--unsafe` か `AIPAIR_UNSAFE=1` を付けると
  `claude --dangerously-skip-permissions` / `codex --dangerously-bypass-approvals-and-sandbox` で起動する（`aipair loop` は必須）。
  env で完全上書き: `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS`（明示指定は安全/危険モードに関わらず優先。空文字でフラグ無し）。
  例: `AIPAIR_CLAUDE_FLAGS="--model opus" aipair` ／ 危険フラグ付きの対話起動は `aipair --unsafe`。
  （VS Code の「🤖 単体起動」タスクは `tasks.json` に直書きしているのでそちらを編集。）
- **停止ワード・合図**: 上の「日本語の既定値と変更方法」。

---

## テスト・CI

```bash
bash tests/run-all.sh        # shebang で判別した全 bash/python3 スクリプトの bash -n / compile / shellcheck（入っていれば）+ tests/ 以下すべて
```

| テスト | 対象 | 方式 |
|---|---|---|
| `tests/session-name.sh` | `aipair name` / `stop` / 実起動のセッション名解決（衝突・旧名引き継ぎ・大小文字・collision） | 専用ソケット `tmux -L` の隔離サーバー。本番ペアには触れない |
| `tests/launch-cmds.sh` | 各ペインに打ち込む起動行（クォート・`AIPAIR_*` の真偽値・シェル断片のフラグ） | `AIPAIR_DRY_RUN=1` の出力を実際にシェルで評価し、シムが受け取った argv を比較 |
| `tests/codex-follow.py` | Codex rollout の探索・追従・増分インデックス | 一時ディレクトリの fixture。`~/.codex` は読まない |
| `tests/relay-parsers.py` | 停止ワード判定・env 解析・ペイン特定・プラン/質問ダイアログ検出・ターン完了検出・transcript パーサ | `tmux` / 画面キャプチャをモック |

GitHub Actions（`.github/workflows/ci.yml`）が push / PR ごとに ubuntu-latest で同じ `tests/run-all.sh` を回す（tmux と shellcheck を apt で導入）。
TUI 本体（Claude Code / Codex CLI の実画面）は CI では動かせないため、ダイアログ検出などは**画面キャプチャの fixture** で固定している。実 UI が変わった時は fixture ごと更新すること。

## 制約・既知の限界

- 配置先 `~/.local/bin` 固定（上記）。`--prefix` のような指定はない。
- 停止ワード・連続モードの合図・relay ペインのタイトルは日本語が既定（env で変更可。タイトルは固定）。
- `aipair` は起動後に必ず attach する（`--no-attach` は無い）。非 TTY から呼ぶと attach だけ失敗するがセッションは作られる（インストーラの疎通確認はこれを利用）。
- Claude Code / Codex CLI の画面文字列（「Would you like to proceed?」「Chat about this」等）に依存する機能（プランレビュー・質問リレー）は、両 CLI の UI 変更で動かなくなりうる。検証済み版は「必要環境」の表を参照。
- macOS は未検証。Windows ネイティブは対象外。

---

## ライセンス

MIT License — Copyright (c) 2026 InOutVillage. 詳細は [`LICENSE`](LICENSE)。
