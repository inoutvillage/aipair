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
> cd /path/to/your/project && aipair   # claude ┃ codex / bridge
> ```
> Requirements: tmux ≥ 3.1, python3 ≥ 3.8 (stdlib only), `claude`, `codex`, a UTF-8 locale.
> ⚠ By default both agents start with **permission-bypass flags**
> (`claude --dangerously-skip-permissions`, `codex --dangerously-bypass-approvals-and-sandbox`).
> Override with `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS` (see *Customization* below). Tested on Linux
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
6. [キュー自動処理（aipair-queue・実験的）](#キュー自動処理aipair-queue実験的)
7. [仕組み](#仕組み)
8. [トラブルシュート](#トラブルシュート)
9. [カスタマイズ](#カスタマイズ)
10. [制約・既知の限界](#制約既知の限界)
11. [ライセンス](#ライセンス)

---

## 必要環境

| 必要 | 最小 | 検証済みバージョン（2026-08-21 実測） | 備考 |
|---|---|---|---|
| `tmux` | **≥ 3.1** | 3.2a | `split-window -l 30%`（割合指定）が 3.1 から。Ubuntu 20.04 の 3.0a は不可 |
| `python3` | **≥ 3.8** | 3.9.25 | `peer-log` / `aipair-relay` / `aipair-queue` の本体。**標準ライブラリのみ**（pip 不要） |
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
| `~/.local/bin/aipair-queue` | キューのタスクを「relay → PR → CI → マージ」まで流す orchestrator（**実験的・プロジェクト固有**） |
| `~/.claude/skills/aipair-setup/` | 対話型セットアップ（Claude Code スキル） |
| `~/.claude/skills/aipair-relay/` | Claude から relay をオンデマンド点火するスキル |
| `~/.claude/CLAUDE.md` | 末尾に周知ブロック。Claude に `peer` の使い方を周知（全セッションで読まれる） |
| `~/.codex/AGENTS.md` | Codex に同様に周知（グローバル読込） |
| `<project>/.vscode/tasks.json` | VS Code「Tasks: Run Task」から起動（WSL2 向けテンプレ）。**無修正で全プロジェクト共通** |

🔒 **配置先は `~/.local/bin` 固定**: `aipair-relay-here` が `$HOME/.local/bin/aipair-relay` を参照し、
`aipair-relay` / `peer-log` / `aipair-queue` は**同じディレクトリ**の隣のファイルを読み込む（`SourceFileLoader`）。
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

セッション名は作業ディレクトリ名から自動算出（例: `…/my-project` → `aipair-my-project`）。

既定の起動フラグは `claude --dangerously-skip-permissions` ／ `codex --dangerously-bypass-approvals-and-sandbox`
（どちらも確認プロンプト無しの YOLO 起動）。env で上書き可 → 下の「カスタマイズ」参照。

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
| `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS` | 上記の `--dangerously-…` | 起動フラグ（空文字でフラグ無し） |

不正値（`AIPAIR_MAX_ROUNDS=abc` / `0` / 負数、`AIPAIR_STOP_SIDE=typo`）は**既定へ落とさず exit 2** で即エラー。
`AIPAIR_ENDLESS` は `0` / `false` / `no` / `off` で明示的に off。環境に残っている時、その 1 本だけ通常モードに戻すには `--no-endless`。

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
  | 終了後 | `relay ■ 終了(全タスク完了) / 3往復` ／ `■ 終了(キャップ到達)` ／ `■ 終了(配達失敗)` ／ `■ 中断` |

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
- 🔴 **`aipair-queue` とは併用しないでください。** queue は「relay の exit 0＝1 タスク完了」を合図に
  PR 作成・マージへ進む設計なので、止まらない relay を渡すと全タスクで `--task-timeout` まで待ちます。

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
> ループが許可プロンプトで止まらないよう、既定の危険フラグ（`--dangerously-…`）付きで起動します。

### relay の exit code（orchestrator 向け）

| code | 意味 |
|---|---|
| 0 | 停止ワード検知（正常完了）。連続モードでは Codex の「全タスク完了」宣言 |
| 2 | 起動エラー（session/pane 不明等）・env の不正値 |
| 3 | 最大往復キャップ到達 |
| 4 | poke 配達失敗 |
| 5 | プランレビュー/質問リレーの上限到達・選択肢欠落 |
| 130 | Ctrl-C 中断 |

---

## キュー自動処理（aipair-queue・実験的）

> 🧪 **実験的・プロジェクト固有のツール**です。`main` ブランチ・GitHub（`gh` CLI・required checks）・Prisma（`prisma migrate deploy`）・
> Vercel 前提のワークフロー向けに書かれており、他の構成では**そのままでは動きません**。同梱はしますが、読んで自分の構成に合わせる前提で使ってください。

`tasks/queue.md` に書き溜めたタスクを **1 件ずつ「relay → PR → CI green → 自動マージ」まで全自動**で流す orchestrator。

```
bridge ペインで:
  aipair-queue              # <cwd>/tasks/queue.md を上から処理
  aipair-queue --dry-run    # 構成確認のみ
  aipair-queue --max-tasks 1 --no-merge   # お試し（1 件・PR 作成まで）
```

- 1 タスク = 1 relay = 1 PR = 1 マージ。`- [ ] タスク文` がそのまま Claude への指示になる
- `prisma/migrations/` を含む PR は**マージ前に本番 DB へ `prisma migrate deploy`**（`.env.production` の URL、localhost ガード付き）
- 異常（relay キャップ/poke 失敗・PR 未作成・CI red・migrate 失敗・タイムアウト）は `- [!] 要人間:` で保留して次へ。
  **連続 3 回異常でキュー全体を停止**（同一原因の空回り防止）
- 停止はキューを実行しているペインで `Ctrl-C`（処理中の relay も連鎖停止）

---

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
- **起動フラグ**: 既定は `claude --dangerously-skip-permissions` / `codex --dangerously-bypass-approvals-and-sandbox`。
  env で上書き: `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS`（空文字にするとフラグ無しで起動）。
  例: `AIPAIR_CLAUDE_FLAGS="--model opus" aipair` ／ フラグを切るなら `AIPAIR_CODEX_FLAGS= aipair`。
  （VS Code の「🤖 単体起動」タスクは `tasks.json` に直書きしているのでそちらを編集。）
- **停止ワード・合図**: 上の「日本語の既定値と変更方法」。

---

## 制約・既知の限界

- 配置先 `~/.local/bin` 固定（上記）。`--prefix` のような指定はない。
- 停止ワード・連続モードの合図・relay ペインのタイトルは日本語が既定（env で変更可。タイトルは固定）。
- `aipair` は起動後に必ず attach する（`--no-attach` は無い）。非 TTY から呼ぶと attach だけ失敗するがセッションは作られる（インストーラの疎通確認はこれを利用）。
- Claude Code / Codex CLI の画面文字列（「Would you like to proceed?」「Chat about this」等）に依存する機能（プランレビュー・質問リレー）は、両 CLI の UI 変更で動かなくなりうる。検証済み版は「必要環境」の表を参照。
- `aipair-queue` は実験的・プロジェクト固有（上記）。
- macOS は未検証。Windows ネイティブは対象外。

---

## ライセンス

MIT License — Copyright (c) 2026 InOutVillage. 詳細は [`LICENSE`](LICENSE)。
