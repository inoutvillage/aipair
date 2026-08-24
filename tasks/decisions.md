# aipair — 社長判断待ち（2026-08-21 外部レビュー起点）

> **2026-08-22 決定済み**: D1=案A / D2=**完全削除**（本リポジトリと用途が違うため queue を撤去）/ D3=案B→A / D4=案A（現状維持）。
> 実装状況: **D1〜D4 すべて実装完了**。D3 案A は corelib/loglib/tmuxlib/deliverylib/dialoglib の 5 sibling へ分割し relay を薄いランチャ化（当初 111KB→81KB）。

# aipair — 社長判断待ち（2026-08-21 外部レビュー起点）

`[?]` = 方針が決まるまで**着手しない**。endless relay が読む `tasks/todo.md` からは意図的に外してある
（`- [ ]` は自動着手対象になるため）。決まったら具体タスクに落として `todo.md` へ移す。

## [x] D1. 起動フラグの既定（レビュー P0）— **案A で実装済み（2026-08-22）**
> 素の `aipair` = 通常の許可プロンプト。`--unsafe` / `AIPAIR_UNSAFE=1` で権限バイパス、`aipair loop` は必須（無ければ exit 2）。明示 `AIPAIR_*_FLAGS` は最優先。README/周知ブロック/installer/VS Code タスクも更新。

- 案A（開発部推奨）: `aipair` = 権限確認あり／`aipair loop` = `--unsafe`（env `AIPAIR_UNSAFE=1`）必須、無ければ起動拒否 + 理由表示。
  relay は許可プロンプトで止まる（README:351）ので loop は bypass 前提 → 「既定 OFF」ではなく「明示 opt-in」が整合する。
- 案B: 既定は現状維持、README の警告を Quick Start 直下へ格上げのみ。
- 社長の日常運用への影響: 案A でも `AIPAIR_UNSAFE=1` を環境既定にすれば変わらない。
- **変更対象は起動経路すべて**（README だけでは不足。Codex 指摘 2026-08-21）:
  - `bin/aipair:91-96` 既定値
  - `templates/vscode-tasks.json:27` loop タスク（`aipair loop` → `--unsafe` を付ける）
  - `templates/vscode-tasks.json:71,86` 単体起動タスク（bypass 直書き → `aipair` 経由 or env 経由に統一）
  - `templates/claude-md-block.md:20-21`／`.claude/skills/aipair-setup/SKILL.md:159`／`aipair-install.sh:532`
  - README:19-20, 176, 236, 351, 435
  - 全経路を F2 の `AIPAIR_DRY_RUN` テスト対象に含める

## [x] D2. `aipair-queue` — **完全削除で決定・実施（2026-08-22）**
> 本リポジトリ（Claude×Codex 連携コア）と用途が違うため queue を撤去。`bin/aipair-queue`・`tests/migration-screen.py`・`tests/queue-state.py` 削除、installer/relay/templates/skill/README の全参照をクリーン。F8 の allowlist ゲートも queue と共に撤去（queue 専用機能だったため）。

- 案A（開発部推奨）: `experimental/aipair-queue` へ移動、installer は既定で入れず `--with-experimental` で opt-in。
- 案B: `bin/` に残す。
- **両案共通の推奨（Codex 指摘で強化）**: migration を含む PR は**既定で「本番適用なし・自動マージなし」→ `[!] 要人間` 保留**。
  `--auto-migrate` を明示した時だけ適用し、その時も **allowlist**（F8）で未知の SQL を全拒否する。
  `DROP`/`RENAME` の denylist では `SET NOT NULL`・型変更・`TRUNCATE`・DEFAULT 無し必須列追加などを通してしまうため不可。
- 現行コードの「Maintainer decision: マージは migration 含め全自動」（`bin/aipair-queue:19`）を覆す判断になるため社長確認が必要。
- 波及: queue は relay を `SourceFileLoader` で読み込む（`bin/aipair-queue:44-50`）→ 移動時は import パスを実ファイル位置から解決させる。

## [x] D3. relay 分割（案B→A）— **完了（2026-08-22）: B（relay-parsers 89 テスト）+ A2〜A6 全増分**
> B（純粋関数の被覆）: `tests/relay-parsers.py` 52 ケースで完了（F6）。
> A 増分1: tmux/ログ非依存の自己完結ヘルパ（停止ワード判定 `hit_stop` / 版ゲート `parse_version`・`detect_version`・`version_gate` / 停止ゲートの出力整形 `scrub_output`・`gate_tail`・`gate_message`・`_oneline_cap` + 定数）を **`bin/aipair-corelib`** へ抽出（peer-log と同じ `SourceFileLoader` パターン、installer が同ディレクトリに配置）。relay は名前を束ねて従来どおり呼ぶ。corelib 単体ロード可（relay 非依存）を回帰テストで担保。end-to-end（temp HOME で installer→relay ロード→smoke）確認済み。
> A 残り（tmux 結合部: ダイアログ検出 / poke 配達 / LogWatch / メインループ）は**触れた時に段階的に**移設する方針。実戦投入済みの統合コードを統合テスト無しで一括移動する退行リスクを避け、52 テストの安全網の下で少しずつ割る（開発部推奨・社長承認 案B→A の趣旨）。

- 案A: `bin/aipair-relay` を薄いランチャにし `lib/aipair/{tmux,claude_log,codex_log,delivery,dialogs,state}.py` へ分割。installer を lib ごと配置に変更、`aipair-relay-here:26` のハードコードパスと queue の import も追従。
- 案B: 分割せず、純粋関数だけ fixture テストで固める（F6）。
- 開発部の意見: **B → A の順**。テストが無い状態で 100KB を割ると退行を検知できない。F6 が緑になってから A を段階的に。

## [x] D4. グローバル注入の縮小（F7）— **案A（現状維持）で決定（2026-08-22）**
> グローバル注入のまま。ブロックは条件付き本文なので非 aipair セッションでも読み飛ばせる。実装変更なし。

**問題（レビュー元指摘）**: installer は `~/.claude/CLAUDE.md` と `~/.codex/AGENTS.md` に aipair 周知ブロックを注入する。これは**全 Claude/Codex セッション**（aipair を使わない通常セッションを含む）に毎回コンテキストとして載る＝トークン浪費。ブロックを「aipair セッションだけ」にスコープしたい。

**調査結果（2026-08-22、実 `--help` / 実起動で確認）**:
- **Claude**: `claude --append-system-prompt-file <path>` が**実在・動作確認済み**（`-p` で実行して受理）。`aipair` 起動時にブロックファイルを渡せば aipair セッションだけにスコープ可能（`bin/aipair:210` の `claude $CLAUDE_FLAGS` にフラグ追加）。
- **Codex**: per-session に instructions を追記する綺麗な仕組みが**無い**（`-c key=value` / `--profile <name>` のみ。指示は `~/.codex/AGENTS.md`（グローバル）か project `AGENTS.md` から読む）。`--profile` で `$CODEX_HOME/<name>.config.toml` を重ねられるが instructions 追記用途は非対応。→ **Codex 側はグローバル or project AGENTS.md 依存が避けられない**（非対称）。

**なぜ社長判断が要るか**:
1. グローバル注入を削る＝**既存ユーザーの `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md` の挙動変更**（installer の marker ブロックを撤去/縮小する破壊的変更）。
2. Claude はスコープ化できるが **Codex はできない**非対称。両者を揃えるには project `AGENTS.md`（＝各プロジェクトに書き込む別の侵襲）か、グローバル維持のどちらか。
3. 「毎セッションのトークン節約」と「aipair セッションでの in-context 可用性」のトレードオフ判断。

**案**:
- 案A（現状維持 + 明記）: グローバル注入のまま。ブロックは既に「When launched by aipair…」と条件付き本文なので、非 aipair セッションでも読み飛ばせる。コスト実測して小さければ据え置き。
- 案B（Claude のみスコープ）: `aipair` が Claude に `--append-system-prompt-file` でブロック投入 + installer のグローバル Claude 注入を **opt-out 可能**に（既定は後方互換で維持）。Codex はグローバル維持（非対称を許容）。
- 案C（両者スコープ・侵襲大）: Claude は append、Codex は project `AGENTS.md` へ marker 注入（各プロジェクトを汚す）。installer のグローバル注入は既定 off。
- 開発部の推奨: **案B**（Claude 側だけ低リスクにスコープ、Codex はグローバル維持）。ただし「グローバルを削るか維持か」は既存ユーザー影響があるため社長判断。

→ 方針が決まれば具体タスク化して `todo.md` へ。実装自体は Claude 側 append が中心で低リスク。

## [?] D5. dev version の形式（P2-2）— SemVer `0.2.0-dev.0` vs PEP 440 `0.2.0.dev0`

> **経緯（2026-08-24）**: 改修その2 P2-2「release 直後の main を dev version に」で、CEO 指定文字列は
> `0.2.0.dev0`（PEP 440 形式）。しかし本プロジェクトは**厳密 SemVer** を全経路で強制している
> （`tests/doc-sync.py` の `SEMVER` 正規表現・git タグ `vX.Y.Z`・`release.yml` の `tag==__version__`
> 検査・`RELEASING.md`）。`0.2.0.dev0` は SemVer 非妥当（pre-release はハイフン `-` を使う）。
> 開発部が独断で `0.2.0-dev.0` へ置換して実装・マージしたが、**受入条件（version 形式）の変更は
> 方針判断**であり、今回の基本方針「AI が判断できない・入力と契約が衝突する場合は推測して進めず
> HUMAN_REQUIRED へ倒す」に反するため差し戻し、CEO 判断待ちとする（PR #142 の version 変更は revert 済み）。

**衝突の本質**: `0.2.0.dev0`（PEP 440・Python 慣習）と SemVer（本プロジェクトの既存契約）は
`__version__` 検証で非互換。ただし dev サフィックスは **prepared 状態でしか使わず**、release 時に
最終 `0.2.0` へ落とす（`RELEASING.md`）。したがって **git タグ `vX.Y.Z`・`release.yml` の
`tag==__version__` 契約は不変**（Codex レビュー relay-id:95c69f9d の是正）。影響は
**prepared 版の validator（doc-sync `SEMVER`）・CHANGELOG lifecycle 判定・文書**に限定。

**案A（CEO 指定どおり `0.2.0.dev0`）**: PEP 440 を採用。
- 影響: prepared 版 validator（`SEMVER`）と CHANGELOG lifecycle（`_top_version`/prepared shape）を
  PEP 440 `.devN` 許容へ拡張＋文書（RELEASING/README/CHANGELOG）対応。タグ・release は不変。
- Python パッケージ的には自然。ただし本プロジェクトは pip 配布ではなく git/tag 配布で、
  `__version__` の SemVer 前提と表記が混在する（released=SemVer / prepared=PEP440）。

**案B（SemVer 等価 `0.2.0-dev.0`・開発部推奨）**: ハイフン pre-release。
- validator は既に許容（契約変更ゼロ）・文書対応のみ。`aipair --version` は `0.2.0-dev.0` となり
  出荷版 `0.2.0` と明確に区別。CEO 指定文字列とは `.`↔`-` のみ相違。表記が一貫（全て SemVer）。

**決定後の残作業（どちらでも）**: `__init__.py` の `__version__`・CHANGELOG を prepared 状態へ・
`RELEASING.md` に dev-suffix 運用を明記・**回帰テスト**（prepared=dev版／released=安定版を doc-sync で固定）・
README/CHANGELOG 冒頭の prepared 表記を同期（Codex レビュー relay-id:d2a80854 の指摘2）。
