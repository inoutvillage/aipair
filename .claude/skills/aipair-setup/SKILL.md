---
name: aipair-setup
description: aipair（Claude Code × Codex CLI を tmux で並走させるツール一式）を新しい PC に対話形式で導入するセットアップウィザード。環境を診断（OS / パッケージマネージャ / tmux と版 / python3 と版 / claude / codex / PATH / 周知ブロック）し、不足分だけを 1 件ずつ承認を取りながら `aipair-install.sh` で導入し、最後に疎通確認の結果を報告する。sudo が要る操作（tmux 導入）は実行コマンドを原文で提示して承認を得てからしか行わない。起動例「aipair をセットアップして」「aipair を入れたい」「/aipair-setup」。前提: aipair リポジトリの checkout 直下（`aipair-install.sh` がある場所）で起動すること。
---

# aipair-setup スキル

## このスキルの目的

aipair リポジトリを clone した直後のユーザーが、**1 セッション内の質問応答だけで** aipair 一式（`aipair` / `aipair-relay` / `aipair-relay-here` / `peer` / `peer-log`、Claude 用スキル、`~/.claude/CLAUDE.md` と `~/.codex/AGENTS.md` への周知ブロック）を導入し、**tmux セッションが実際に立つところまで**確認できるようにする。

実作業はすべて **`aipair-install.sh`（冪等・非対話）に任せる**。このスキルは「診断を読んで説明する」「承認を取る」「オプションを決めて呼ぶ」「結果をそのまま報告する」だけを行う。

---

## 起動方法

- スラッシュ: `/aipair-setup`
- 自然言語: 「aipair をセットアップして」「aipair を入れたい」「この PC で aipair を使えるようにして」

---

## 🚫 禁止事項（厳守）

1. **`~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md` / シェルの rc ファイル（`~/.bashrc` 等）を直接編集しない。** 周知ブロックの追記・置換はインストーラに一元化されている（マーカー置換・バックアップ・検証つき）。PATH の追記行もユーザーに提示するだけで、書き込まない。
2. **sudo を要するコマンドを承認なしに実行しない。** `--install-tmux` を付けるのは、実行されるコマンドを原文で見せて明示的に承認を得た後だけ。
   - 🚫 **`sudo -S`・パスワードの標準入力への流し込み・パスワードの代理入力をしない。** `/etc/sudoers` への `NOPASSWD` 追加もしない。sudo のパスワードは**利用者が自分の端末で入力する**もの。Bash ツールから `--install-tmux` を実行して sudo がパスワードを要求し失敗したら、そのコマンドを利用者自身の端末で実行してもらう（下の Step 4 参照）。
3. **失敗を言い換えない。** インストーラの `[fail]` 行・非 0 終了は失敗としてそのまま報告する（「ほぼ成功」「軽微なエラー」などに丸めない）。
4. **claude / codex / python3 をこのスキルが代わりに導入しない。** 導入手順を案内して終了する（インストーラも同じ方針で exit 3 を返す）。
5. **検出ロジックを自前で再実装しない。** OS・版・PATH・ブロックの有無は `aipair-install.sh --check` の出力だけを情報源にする。

---

## 処理フロー（厳守）

### Step 0. 前提確認（リポジトリ直下か）

```bash
pwd
test -f ./aipair-install.sh && test -f ./bin/aipair && test -f ./templates/claude-md-block.md && echo OK
```

`OK` が出ない場合は次を案内して **停止**:

> aipair リポジトリの直下で実行してください。未取得なら
> `git clone https://github.com/inoutvillage/aipair && cd aipair` してから `claude` を起動し直し、`/aipair-setup` を呼んでください。

### Step 1. 診断（無変更）

```bash
bash ./aipair-install.sh --check
echo "exit=$?"
```

- exit **0**（依存すべて OK）または **3**（依存に不足あり）は正常な診断結果。それ以外（1 / 2）は診断そのものの失敗 → 出力をそのまま見せて停止。
- `KEY=VALUE` を読んで **表で報告**する（値は出力からそのまま転記。推測で埋めない）:

| 項目 | 読むキー | 表示 |
|---|---|---|
| OS | `os` / `os_detail` | `wsl2` / `linux` / `macos`（macOS は **未検証環境** と明記） |
| パッケージマネージャ | `pkg_manager` | `dnf` / `apt` / `pacman` / `brew` / `none` |
| 権限 | `is_root` / `sudo` | root か、sudo が使えるか |
| tmux | `tmux` / `tmux_version` / `tmux_ok` / `tmux_min` | 有る（版）／無い／**古い**（`tmux=1` かつ `tmux_ok=0`） |
| python3 | `python3` / `python3_version` / `python3_ok` | 同上 |
| claude / codex | `claude` `claude_version` / `codex` `codex_version` | 有る（版）／無い |
| ロケール | `locale_utf8` / `lang` | UTF-8 か |
| PATH | `path_ok` / `install_dir` | `~/.local/bin` が PATH にあるか |
| 既存インストール | `aipair_installed` / `aipair_current` | `none` / `partial` / `all`、最新と一致か |
| スキル | `skill_aipair_setup` / `skill_aipair_relay` | 配置済みか |
| 周知ブロック | `claude_md_block` / `codex_agents_block`（＋ `_current`） | `present` / `absent` / `broken` / `missing-file`、最新と一致か |

`claude_md_block=broken` または `codex_agents_block=broken` の場合: インストーラはそのファイルに触らず `[fail]` で止まる設計。該当ファイルのマーカー（`<!-- aipair:start -->` / `<!-- aipair:end -->`）が 1 組になるよう**ユーザー自身に直してもらう**案内を出し、直ったら Step 1 からやり直す（このスキルは直接編集しない）。

### Step 2. 不足分の確認と承認（不足しているものだけ）

診断で不足していた項目 **だけ** を列挙し、1 件ずつ AskUserQuestion で承認を取る。すべて揃っていればこの Step は「不足なし」と一言で済ませて Step 4 へ。

#### 2-1. tmux が無い（`tmux=0`）

実行されるコマンドを **原文で** 提示してから承認を取る。`pkg_manager` と `is_root` から組み立てる（インストーラの `--install-tmux` が実行するものと同一）:

| pkg_manager | root | 非 root |
|---|---|---|
| `dnf` | `dnf install -y tmux` | `sudo dnf install -y tmux` |
| `apt` | `apt-get update` → `apt-get install -y tmux` | `sudo apt-get update` → `sudo apt-get install -y tmux` |
| `pacman` | `pacman -Syu --needed --noconfirm tmux` | `sudo pacman -Syu --needed --noconfirm tmux` |
| `brew` | （brew は root で使わない） | `brew install tmux`（sudo 不要） |
| `none` | — | 自動導入不可。tmux ≥ 3.1 を手動で入れてもらい、Step 1 からやり直す |

質問例: 「tmux が見つかりません。次のコマンドで導入してよいですか？（sudo のパスワードは端末で聞かれます）`sudo dnf install -y tmux`」
- **承認** → Step 4 で `--install-tmux` を付ける
- **拒否** → コマンドを示して「手動で導入後に `/aipair-setup` を再実行してください」と案内し **停止**（tmux 無しではインストーラが exit 3 で止まるため）
- 非 root で `sudo=0` → 自動導入はできない。root で実行すべきコマンドを示して停止

#### 2-2. tmux が古い（`tmux=1` かつ `tmux_ok=0`）

`--install-tmux` は既存 tmux を更新しない。「`tmux_version` は `tmux_min` 未満。`split-window -l 30%` が使えないため、パッケージマネージャで更新するかソースからビルドしてください」と案内して **停止**。

#### 2-3. claude / codex / python3 が無い、または python3 が古い → Step 3

#### 2-4. VS Code タスク（任意）

`os=wsl2` のとき（または VS Code を使うと言われたとき）だけ聞く: 「VS Code の Tasks から起動できる `tasks.json` をプロジェクトに置きますか？（置く場合はプロジェクトのディレクトリを教えてください。既存の tasks.json は上書きしません）」
- 置く → Step 4 で `--vscode-tasks <dir>` を付ける
- 置かない／後で → 付けない（README の「VS Code から」節を案内）

### Step 3. claude / codex / python3 が無ければ案内して終了

このスキルは代わりに導入しない。該当分だけ案内して **停止**（導入後に `/aipair-setup` を再実行してもらう）:

- **python3 無し／古い**: `dnf install python3` / `apt-get install python3` / `pacman -S python` / `brew install python`（3.8 以上、標準ライブラリのみ使用）
- **claude 無し**: `npm install -g @anthropic-ai/claude-code` → 一度 `claude` を起動してログイン（このスキルが動いているのに `claude=0` なら、PATH が `--check` 実行時のシェルと違う可能性。`command -v claude` の結果を添えて報告）
- **codex 無し**: `npm install -g @openai/codex` → 一度 `codex` を起動してログイン

### Step 4. インストーラ実行

承認内容に応じてオプションを決め、**原文をユーザーに見せてから**実行する:

```bash
bash ./aipair-install.sh [--install-tmux] [--vscode-tasks <dir>]
echo "exit=$?"
```

結果の読み方（**終了コードとステップ行の両方を見る**）:

| exit | 意味 | 報告 |
|---|---|---|
| 0 | 成功（`[warn]` を含みうる） | `[ok]` / `[skip]` / `[warn]` を分類して報告。`[warn]` は対応が要るものとして明示 |
| 3 | 依存不足で中断（案内済み） | `[fail]` 行をそのまま見せる。Step 2/3 の案内へ戻る |
| 1 | 失敗（ファイル配置・ブロック書き込み・疎通確認のいずれか） | `[fail]` 行を **原文のまま** 報告。成功とは言わない。周知ブロックの失敗はバックアップから復元済み（インストーラが自動で戻す）であることを添える |
| 2 | 引数エラー | 組み立てたコマンドを見直す |

`--install-tmux` を付けた場合、sudo のパスワード入力は端末（ユーザー）側で行われる。Claude Code の Bash ツールから実行して sudo がパスワードを要求し失敗した場合は、**そのコマンドをユーザー自身の端末で実行してもらい**、終わったら `--install-tmux` 無しでインストーラを再実行する。

### Step 5. 動作確認

1. インストーラ出力の **smoke 行**（`[ok]   smoke: 'aipair <tmpdir>' created tmux session ... with 3 panes ...`）を提示する。これが「tmux セッションが実際に立った」証拠。`[fail] smoke:` なら失敗として報告（Step 4 の表）。
2. 最後に AskUserQuestion で聞く: 「実際に `aipair` を起動してアタッチしてみますか？」
   - **はい** → 起動は **tmux の外の通常のシェル**（VS Code のターミナル等）で行ってもらう。Claude Code のツールからは画面をアタッチできないため、手順だけ案内する:
     ```bash
     cd <作業したいプロジェクト>
     aipair            # 3 ペイン（claude ┃ codex ／ 下段 bridge）が立ち上がりアタッチされる
     # 終わったら: aipair stop   （デタッチだけなら Ctrl-b d）
     ```
     PATH に `~/.local/bin` が無かった（`[warn]`）場合は、先に新しいシェルを開く／提示した export 行を反映してもらう。
   - **いいえ** → 同じ手順を「後で」として案内

### Step 6. 最終報告（必ず以下を含める）

```
✅ aipair セットアップ結果

【入れたもの】     [ok] 行から（ファイル / スキル / 周知ブロック / tmux 導入 など）
【スキップ】       [skip] 行から（既に最新だったもの）
【要対応 (warn)】  [warn] 行から（例: ~/.local/bin を PATH に追加: export PATH="$HOME/.local/bin:$PATH"）
【失敗】           あれば [fail] 行を原文で（無ければ「なし」）

⚠ 既定の起動フラグ:
  claude --dangerously-skip-permissions / codex --dangerously-bypass-approvals-and-sandbox
  （確認プロンプト無しで動くモード。信頼できる作業ディレクトリでだけ使うこと）
  変更・無効化: AIPAIR_CLAUDE_FLAGS / AIPAIR_CODEX_FLAGS（例: AIPAIR_CODEX_FLAGS= aipair でフラグ無し）

▼ 自走ループの既定値（日本語）
  停止ワード「完了です」（AIPAIR_STOP）／ 判定側 codex（AIPAIR_STOP_SIDE）／ 最大 20 往復（AIPAIR_MAX_ROUNDS）
  連続モードの合図「次のタスクをください」「全タスク完了」（AIPAIR_NEXT_ASK / AIPAIR_ALL_DONE）

▼ 次にやること
  1. 新しいシェルを開く（PATH 反映）→ cd <project> → aipair
  2. 詳しい使い方: README（自走ループ / 連続モード / VS Code から / トラブルシュート）
```

---

## 安全メモ: ペア内で tmux を叩くとき

- aipair の動作確認やテストで tmux を使うときは、**必ず専用サーバー**（`tmux -L <名前>` か `-S <ソケット>`）を指定する。
- tmux ペインの中では環境変数 `$TMUX` が `TMUX_TMPDIR` より優先されるため、`TMUX_TMPDIR` で隔離したつもりでも本番サーバーに刺さる。
- **引数無しの `tmux kill-server` は禁止**（動いているペアを巻き込む）。後始末は `tmux -L <名前> kill-server` のように対象を明示し、破壊的コマンドの前に `#{socket_path}` で隔離を確認する。
- このガードレールの根拠は公開版 `SECURITY.md` の「テストハーネスの tmux」節にまとめてある（2026-08-21 の障害が発端）。

---

## OS 差異の扱い

- `os=wsl2`: VS Code の Tasks は `wsl.exe --cd … bash -ic …` 形式（`templates/vscode-tasks.json`）。`--vscode-tasks` を提案する
- `os=linux`: そのまま。VS Code を使うなら tasks.json の `wsl.exe` 部分をユーザー側で書き換える必要がある旨を添える
- `os=macos`: **未検証**（実機での検証なし）と必ず明記する。`brew install tmux` は sudo 不要。GNU 固有のコマンドは使っていないが、問題が出たら README のトラブルシュートへ

---

## エラーケース

| 状況 | 対応 |
|---|---|
| リポジトリ直下でない | clone と `cd` を案内して停止 |
| `--check` が exit 1/2 | 出力を原文で提示して停止（診断できない状態では進めない） |
| `*_block=broken` | ユーザーにマーカー修復を依頼 → Step 1 から |
| sudo 承認が得られない | 手動コマンドを案内して停止 |
| インストーラ exit 1 | `[fail]` 行を原文で報告。成功と言わない。再実行の前に原因（権限・ディスク・壊れたブロック等）を一緒に確認 |
| smoke が `[fail]` | tmux が別ユーザーのソケットを見ていないか（`TMUX_TMPDIR`）、`tmux ls` の結果と併せて報告 |

---

## 補足: AskUserQuestion の使い方

- 1 回の呼び出しは最大 4 問。Step ごとに分けて呼ぶ
- 「はい／いいえ」はラジオボタン、ディレクトリ名などの短い自由記述は "Other" で受ける
- 承認を求める質問には **実行するコマンドの原文** を必ず含める
