# endless モード BLOCKED / HUMAN_REQUIRED 対応（社長指示 2026-08-24）— `_reference/new-task.md`

> **問題**: endless モードのタスク状態が実質「未完了/完了」の2値しかなく、人間対応・外部依存で
> AI が完了できないタスクが残ると、未チェック項目ありのまま `全タスク完了` にできず max-rounds まで
> 無駄に往復する（P1-5/#8-9 の secrets 待ちで**実際に発生**）。
> **方針**: 「未完了=続行」をやめ、**READY / DONE / BLOCKED を state machine で区別**する。
> 「全タスクは未完了だが AI が今できるタスクは0件」を正式な状態にし、進行不能を検出した時点で
> 即座に人間待ちとして停止する（max-rounds は安全網であって正常終了制御ではない）。
> **現状の実態**: relay は task-list を一切読まず、ALL_DONE は Codex の `[AIPAIR_ALL_DONE]` sentinel のみで決まる
> （`state_machine.py:621`）。exit は 0/3/4/5/6/7/130 のみで **exit 8 未定義**。→ 本改修で relay 自身が task-list を分類する。

## チェックボックスの意味（拡張・endless relay が厳密に読む / Phase 1 で既存「チェックボックスの意味」節へ統合）
- `- [ ]` = 実行可能な未完了（**READY** 対象）
- `- [x]` = 完了
- `- [!]` = 保留（**BLOCKED/HUMAN_REQUIRED**）: 未完了だが AI pair だけでは進行不能、人間/外部条件の解消が必要。直下に `blocker:` を併記。
  （記法 `[!]` は実装上の既定。**GFM では `[!]` はチェックボックスとして描画されず、`[!]` を含むただのリスト文字列として表示される**〈未チェック `[ ]` 扱いではない〉。endless relay は `[!]` を BLOCKED として厳密に区別する）

### Phase 1 — 基盤: `[!]` 状態 + task-list 分類器 + exit 8 + sentinel
- [x] **task-list 分類器（純関数・単体テスト）**〔`bin/aipairlib/tasklist.py`＋`tests/tasklist.py` 17件・run-all 緑〕: **本文テキストを受け取り**（ファイル I/O は分離）**構造体**を返す: `{state, ready:[行テキスト...], blocked:[{item:行テキスト, blocker}...], hash}`。`state`=`READY`(≥1 `[ ]`) / `BLOCKED`(0 `[ ]`・≥1 `[!]`) / `ALL_DONE`(0 `[ ]`・0 `[!]`)。ネストした `  - [ ]/[!]` も対象。**認識する記法は厳密に `[ ]`・`[x]`/`[X]`・`[!]` のみ**。**未知の checkbox 風記法（`[?]`・`[-]` 等）は無視して `ALL_DONE` にせず、解析エラー exit 2**（fail-closed）。**`[!]` に直下の `blocker:` 行が無ければ解析エラー exit 2**（全 blocked は理由必須）。**任意長のバッククォート／チルダ・コードフェンス（```/~~~）内の疑似 checkbox は無視**。
- [ ] **task-list ローダ（I/O・fail-closed）**: 相対パスは **`--dir` 基準**で解決。欠損・読取不能・解析不能は **`ALL_DONE` にせず起動エラー exit 2**（fail-closed。「読めない＝完了」で誤停止させない）。
- [ ] **sentinel 追加（cli.py）**: `--human-required`（既定 `[AIPAIR_HUMAN_REQUIRED]`）＋ env のみ。既存 `hit_stop`（先頭行完全一致）準拠。**`[AIPAIR_BLOCKED]` は agent sentinel にしない**（no-progress は relay 内部検出＝下記 Phase 4）。
- [ ] **新 env の配線を完全化**: `AIPAIR_HUMAN_REQUIRED` を `cli.py` だけでなく `bin/aipair`・`bin/aipair-relay-here`（stale-env 打ち消し＋argv 転送）に追加し、`tests/env-forward.sh`・`tests/launch-cmds.sh` の被覆に含める（既存 sentinel/env と同じ経路）。
- [ ] **exit code 8** を reason map（`state_machine.py:696`）と全終了経路に追加。2つの内部理由を持つ: (a) `HUMAN_REQUIRED`（分類==`BLOCKED`）(b) `BLOCKED (no-progress)`（relay 内部検出）。どちらも exit 8・reason 文字列で区別。max-rounds(3) と明確に区別。
- [ ] `[!]` の意味を既存「チェックボックスの意味」節（endless relay が読む厳密定義）・README・SECURITY に記載。

### Phase 2 — endless 終端の3状態化（§2/§10）
- [ ] **起動直後・各 terminal sentinel 受信時に relay が task-list を再分類**し、分類結果を**唯一の権威**とする。`READY`=継続 / `BLOCKED`=HUMAN_REQUIRED で exit 8 停止 / `ALL_DONE`=exit 0。
- [ ] **sentinel は分類が一致する時のみ honor**: `[AIPAIR_ALL_DONE]` は分類==`ALL_DONE` の時だけ終了、`[AIPAIR_HUMAN_REQUIRED]` は分類==`BLOCKED` の時だけ終了。**分類に `READY` が残る場合は sentinel を拒否して継続**（Case 6 に必須。誤 sentinel で未処理の `[ ]` を残して止めない）。no-progress による停止（exit 8）は分類とは独立の relay 内部経路であり error/max-rounds と同一扱いにしない。

### Phase 3 — Codex/Claude の選択ロジック・プロンプト（§4/§5/§9）
- [ ] **Codex 次タスク選択プロンプト**: `[ ]`優先→1件指示 / `[ ]`無→`[!]`確認→`[!]`あれば `[AIPAIR_HUMAN_REQUIRED]` / どちらも無→`[AIPAIR_ALL_DONE]`。**`[!]` を再指示しない**。
- [ ] **Claude プロンプト**: 実行不能（承認/認証/管理者/外部反映/実機/環境）を検出したら `[ ]`→`[!]`＋`blocker:` 理由に更新してから、他の `[ ]` を続行 or 手詰まり宣言。
- [ ] **§9 修正**: 「人間に伝言を頼むな」は通常レビュー往復のみ維持。**HUMAN_REQUIRED まで人間へのエスカレーションを禁止しない**（「安易に人間へ投げるな」と「人間しか解けない時も止まるな」を混同しない）。
- [ ] **配布済みドキュメントの旧契約を更新**（新契約と矛盾するため必須）: `bin/aipairlib/relay.py` の help（`:58-73`「終端は Codex の --all-done 宣言のみ」）、`bin/aipair` のコメント・初期 title（`:278-286`）、`templates/claude-md-block.md`、`.claude/skills/aipair-relay/SKILL.md`、`.claude/skills/aipair-setup/SKILL.md` から**「終端は ALL_DONE のみ」を削除**し、**task-list 分類（READY/BLOCKED/ALL_DONE）＋ HUMAN_REQUIRED 終端＋ exit 8** を記載。

### Phase 4 — no-progress guard（§8）
- [ ] **タスク同一性の契約を確定**: 識別子は **task-list 上の完全一致行テキスト（verbatim `- [ ]` 行）** に固定（安定 ID 方式は採らない）。Codex は次タスク指示時にその行を逐語エコー（プロンプトで指定）。relay は現 task-list 内で**厳密一致が丁度1件**である事を検証。**fail-closed**: 抽出失敗 or 一致 0/≥2 件は識別子=`UNRESOLVED` とし、警告ログ＋ no-progress ストリークを進める（同一性を判定できないまま無限往復させない）。
- [ ] no-progress 判定は**簡易版に固定**（`git diff` 条件は採らない — 無関係な dirty/untracked で常にリセットされ判定不能になるため）。**意味的 task-list snapshot hash**（生バイトでなく、パースした checkbox 項目＋状態の正規化スナップショット。装飾的編集ではリセットしない）を用い、**(同一識別子の再選択 OR `UNRESOLVED`) AND snapshot hash 不変**が **3 回連続（初版は定数で固定。env 調整は導入しない — 全配線が未計画のため）** で **relay 内部 reason `BLOCKED (no-progress)` として直接 exit 8**（agent sentinel は介さない）。ストリークは「新しい識別子の解決」または「snapshot hash 変化」でリセット。snapshot hash は**順序付きの正規化タプル列 `(indent, state, item, blocker)` から生成**（行順を保持・状態と本文とインデントのみを織り込む）。

### Phase 5 — 人間待ち終了 UX（§6/§7）
- [ ] 2種類の exit 8 を**別々の banner** で即終了（無駄往復なし）。pane title 反映。
  - **HUMAN_REQUIRED（分類==BLOCKED）**: 「■ 自動処理を停止しました／理由: 人間対応が必要なタスクのみ残っています／残: `[!]` 項目名＋blocker 理由」を一覧表示。
  - **BLOCKED (no-progress)**: 「■ 自動処理を停止しました／理由: 進捗がないまま同じタスクが再選択／繰り返された READY 項目（or `UNRESOLVED`）／ストリーク数／task-list snapshot hash」を表示。**no-progress 時は `[!]` が存在しない場合があり**、`[!]` 一覧に依存しない。

### Phase 6 — 回帰テスト（§11）
- [ ] Case1（`[ ]`+`[x]`→A選択・継続）/ Case2（全`[x]`→ALL_DONE・exit0）/ Case3（`[!]`+`[x]`→HUMAN_REQUIRED・exit8・再指示なし）/ Case4（`[ ]`+`[!]`→A先行→後にHUMAN_REQUIRED）/ Case5（同一`[ ]`を **2回目までは継続・3回目で exit 8**／snapshot hash 不変→no-progress）/ Case6（`[!]`あるが別`[ ]`あり→継続・まだHUMAN_REQUIREDにしない）を自動テスト化。
- [ ] **負テスト**: (a) `[!]` に `blocker:` 無し→exit 2 / (b) **任意長のバッククォート／チルダ・コードフェンス**内の疑似 checkbox を分類が無視 / (c) task-list 欠損・解析不能→exit 2 / (d) 識別子 0/≥2 一致→`UNRESOLVED` 経路 / (e) **未知 checkbox 風記法（`[?]` 等）→exit 2**（`ALL_DONE` にしない）。**正**テスト: `[X]`（大文字）を完了として扱う。
- [ ] **旧説明の再発防止**: `tests/broadcast-blocks.sh` と doc-sync で、上記配布ドキュメントに「終端は ALL_DONE のみ」等の旧契約が**再発しないこと**＋新契約（HUMAN_REQUIRED / task-list 分類 / exit 8）が**存在すること**を assert。
- [ ] doc-sync に新 sentinel（`[AIPAIR_HUMAN_REQUIRED]`）・exit 8・`[!]` 意味を pin（README と実装の同期）。

### 依存順
Phase 1（基盤）→ Phase 2（終端判定）→ Phase 3（プロンプト）は 2 と並行可 → Phase 4（guard）→ Phase 5（UX）→ Phase 6（テストは各 Phase と並行、最後に統合）。

---

# aipair 改修（社長指示 2026-08-23）— 外部レビュー指摘の優先対応

> 方針: **「推測して動き続ける」より「判定できない場合は止まる（fail-closed）」を優先**。
> 権限バイパス下で Claude Code / Codex を自律操作するため、**誤停止しない・誤承認しない・
> 別ターンを誤帰属しない・未知 schema で勝手に継続しない**を利便性より優先する。
> 優先順位: 1〜7（安全性・正しさ）を新機能より先行。

- [x] **P0-1 停止ワード判定を substring→専用 sentinel の先頭行完全一致へ**（`corelib.hit_stop`）— 否定文・引用・文中言及で誤停止しない。sentinel: `[AIPAIR_REVIEW_OK]` 等。poke も sentinel-at-head 指示へ。
- [x] **P0-2 プラン承認判定も専用 sentinel＋先頭行完全一致へ**（`relay.py` の `a.plan_ok in final[:80]`）— sentinel: `[AIPAIR_PLAN_APPROVED]`。否定文中の承認を絶対に成立させない。
- [x] **（1〜4）sentinel protocol 導入＋否定文/引用/文中一致テスト**（8 ケース: 単独=真/後続説明=真/2行目=偽/「まだ〜ではない」=偽/「〜と回答して」=偽/文中=偽/100字以降=偽/複数候補は先頭のみ）
- [x] **P1-1 JSONL schema mismatch 時は既定 fail-closed（relay 停止・exit 7）**、`--allow-untested-schema` 明示時のみ継続。README/SECURITY 同期。
- [x] **P1-2 schema probe を「ターン完了」だけでなく「応答帰属」まで拡張**（Codex: response_item/turn_id/task_started.turn_id/task_complete.turn_id）。turn completion / response attribution / delivery confirmation / dialog resolution の単位に分割。全て compatible で初めて compatible。
- [x] **P1-3 Codex の turn_id 欠落時 fallback 見直し**（`codex_response_complete`）— 通常モードは turn_id 無し→帰属不能→fail-closed。残す場合は compatibility mode 明示＋警告＋自律判定には使わない。
- [x] **P1-4 schema latch を agent 単位→agent+tracked log identity（path）単位へ**。log 切替（/resume・/clear・再起動・compaction・rotation）で未確認へ戻し再 probe。
- [x] **P1-5 認証付き round-trip E2E**（方針決定で解決・2026-08-23 社長判断）。**API キー方式の CI `authenticated-e2e` を必須ゲートにしない**。根拠: (1) aipair は Claude Code / Codex CLI を**サブスク（対話ログイン）認証**で駆動するもので、CI の API キー方式は**実利用と別の認証経路**を検証するに過ぎない。(2) この `aipair loop` セッション自体が、**実サブスク認証の Claude Code × Codex CLI が実機で poke→返信→relay 返しの round-trip を 60+ 往復で成立させている直接の実機証跡**（＝実利用経路での検証）。`authenticated-e2e` job は **opt-in**（secrets があれば走る回帰用）として残置。※「API キー E2E を実走し 3シグナル PASS を観測した」という主張ではなく、実機サブスク運用＋非ゲート化の決定で解決（README も同旨）。
  - 実走試行の観測（2026-08-23・Codex 指示 relay-id:fe7387b3 で着手）: `nightly.yml` を `workflow_dispatch`（HTTP 204 受理）→ run [32642537864](https://github.com/inoutvillage/aipair/actions/runs/32642537864)。**`authenticated-e2e` は self-skip**（実出力に `skip: the round-trip E2E needs BOTH ANTHROPIC_API_KEY and OPENAI_API_KEY` が存在し、`E2E PASS` は不在＝実走せず）。GitHub API でも repo secrets は**「なし」**（`ANTHROPIC_API_KEY`/`OPENAI_API_KEY` 未設定）を確認。**job の conclusion=success は self-skip 成功であって 3シグナル検証ではない** → 当時は要件未達で未チェック維持だった。**その後、非ゲート方針の社長判断（2026-08-23）で P1-5 を解決**（round-trip は実機サブスク運用で実証／API キー E2E は opt-in・未実走のまま）。この self-skip 観測は当時の事実として残す。
- [x] **P1-6 nightly を latest smoke（secrets 無）と authenticated-e2e（version pin + secrets 有）に分離**。上流 latest 破壊と aipair 既存版 E2E 破壊を切り分け。
- [x] **P2-1 relay state machine を state 単位で分割**（state_machine/review_protocol/schema_guard/plan_flow/question_flow）。relay.py は arg parse/依存構築/起動/exit に寄せる。
  - 増分1（着手済）: **schema_guard** を `bin/aipairlib/schema_guard.py` の `SchemaGuard` クラスへ抽出（latch/identity を所有・純関数と probe を注入）。従来 nested closure でテスト不能だった schema_watch/guard を**単体テスト可能**に（5 ケース追加）。installer FILES/import-verify にモジュール追加。残: review_protocol / plan_flow / question_flow / state_machine。
  - 増分2（着手済）: **review_protocol** を `bin/aipairlib/review_protocol.py` へ抽出（poke 文面 7 本＋`plan_extra_comment`＝副作用無しの純関数群）。relay は `from .review_protocol import ...` で再エクスポート。standalone import＋sentinel-at-head 指示のテスト追加。残: plan_flow / question_flow / state_machine。
  - 増分3（着手済）: **gate**（停止ゲート実行 `run_gate`/`_kill_group`）を `bin/aipairlib/gate.py` へ抽出（subprocess・プロセスグループ kill の純ロジック・tmux 非依存）。relay は `run_gate` を再エクスポート、UI 依存の `gate_or_message` は relay 残置。standalone import テスト追加。残: plan_flow / question_flow / state_machine。
  - 増分4（着手済）: **log_lock**（各ペインが所有するログの特定・lock/refresh の 12 関数＋`_CODEX_SINCE_*` 状態）を `bin/aipairlib/log_lock.py` へ抽出。relay は lock/refresh を再エクスポート（codex-follow テストは patch 先を `relay.log_lock.*` に更新）。relay.py -227 行（1077 行）。残: plan_flow / question_flow / state_machine（main ループ内で結合度が高い）。
  - 増分5（着手済）: **gate モジュール完成** — `gate_or_message` を gate.py へ集約（`run_gate`/`_kill_group` と同居）、汎用 `oneline` を corelib へ移し relay は再エクスポート。relay.py 1054 行。dialog scraping（detect_plan_dialog/send_plan_feedback/detect_question_dialog/send_question_answer）は既に dialoglib に在り。残: state_machine（main ループ本体）＝大きな慎重リファクタ。
  - 増分6（着手済）: **cli モジュール完成** — argparse（`build_parser`）と `AIPAIR_*` env 既定（`_env_str/_env_int/_env_bool`＋`ENV_USED`）を cli.py へ切り出し、relay は再エクスポート（`relay._env_* is cli._env_*`／`relay.ENV_USED is cli.ENV_USED`）。main() は `ap = build_parser(__doc__)` に縮約。relay.py 957 行（1054→）。standalone import テスト追加。残: state_machine（main ループ本体）のみ。
  - 増分7（着手済）: **state_machine モジュール開始** — main() の最重要かつテスト不能だった nested closure `response_done`/`poke_noshow`（＝poke nonce のライフサイクルと P1-2/P1-3 の応答帰属ゲート）を `bin/aipairlib/state_machine.py` の `ResponseGate` クラスへ抽出（SchemaGuard と同型・probe 状態を所有・純関数 dep と fake clock を注入）。relay は `rg.arm/clear/response_done/noshow` と `rg.probe*` で呼ぶだけに。ロジックは完全保存（挙動不変）、fail-closed 不変条件（turn_id 欠落→reject・位置フォールバック不使用・nonce 未着→no-show 停止）を **13 の単体テストで直接被覆**（従来 0）。relay.py 957→894 行。installer/ガードテスト参照先も更新。残: ループ骨格（state 遷移本体）は A6 の判断通り relay の orchestration 核に残置（丸ごと移設は本番ペア駆動経路でリスク最大・単体テスト不可のため見送り、テスト可能な核から段階抽出）。
  - 増分8（着手済）: **plan_flow の判定核を抽出** — プラン自動承認（権限バイパス下で最も高リスクな不可逆自律アクション）の判定カスケードを `state_machine.decide_plan_action(texts, plan_ok, dialog)` の純粋関数へ切り出し（副作用＝press/send/log/code はループ残置）。sentinel 先頭行完全一致・feedback 閾値・修正 vs 承認・no_tell_option を、否定文中 sentinel を絶対に承認しない安全性込みで **単体テスト化**（PlanApproval を source-grep から挙動テストへ格上げ＋全分岐 165 tests）。relay.py 893→891 行。残: question_flow（判定核が薄いため優先度低）／ループ骨格は前述の通り残置。
  - 増分9（完了）: **question_flow の判定核を抽出** — Codex 質問回答の判定（no_text / no_dialog / deliver）を `state_machine.decide_question_action(texts, qdlg)` の純粋関数へ切り出し（plan と対称・副作用はループ残置）。`QuestionRelayDecision` テスト追加（166 tests）。relay.py 894 行、state_machine.py 173 行。
  - 増分10（完了）: **ループ骨格を StateMachine へ移設し relay.py をランチャ化** — `while True` の状態機械本体＋`LogWatch`／`approval_took_effect`／`done_banner`／4 closure を `state_machine.py` の `StateMachine`（loop 状態を属性化・`run()→exit code`）へ移設。`relay.main()` は「parse → 依存構築 → 起動バナー/gate → `StateMachine(...).run()` → exit」の**ランチャのみ**に縮約（**relay.py 894→392 行**、分割開始時 ~1470 から通算 **−1078**）。移設は逐語コピー（依存は run() 冒頭で self→ローカル展開）で、バックアップとの**字下げ正規化 diff により loop 364 行・LogWatch・approval・done_banner・closures・loop-init すべて逐語一致を確認**＝挙動保存。判定核 `decide_plan_action`／`decide_question_action` は named module `plan_flow.py`／`question_flow.py` へ分離（state_machine が re-export）。`StateMachineWiring` 構築テスト＋introspection ガード群を新配置へ更新（168 tests）。installer に state_machine/plan_flow/question_flow を配布登録。
  - **P2-1 完了（`- [x]`・受入条件充足）**: named module（**state_machine / review_protocol / schema_guard / plan_flow / question_flow**）が全て実ファイルで出揃い、`relay.py` は **arg parse／依存構築／起動／exit のみ**のランチャ（392 行）。状態機械の判定核（ResponseGate＝応答帰属・decide_plan_action＝プラン承認・decide_question_action＝質問中継・SchemaGuard＝schema fail-closed）は単体テスト化済み。ループ本体は tmux/画面と密結合な統合レベルのため単体テストは効かないが、移設が逐語一致であることを diff で機械確認して挙動保存を担保（self-host は次回インストールで反映）。fail-closed 不変条件は全増分で不変更。Codex レビュー relay-id:3298e7a2 の指摘（ループ残置・plan_flow/question_flow 不在）を解消。
- [x] **P2-2 installer の transaction 性強化**（package + entrypoints + templates を staging→検証→一括切替、Phase2 途中失敗は entrypoint も自動 rollback）。
  - 完了（**統一トランザクション**・Codex レビュー relay-id:00863ce7 反映）: installer 全体（aipairlib package・thin entrypoints・退役 flat libs・skills・GLOBAL 通知ブロック CLAUDE.md/AGENTS.md・任意 VS Code tasks）を **1 つの journal を持つ単一トランザクション**に統合。**全 destination を commit 前に staging・検証**（binaries=`.aipair-stage-$TS` に置いて import＋`bash -n`＋co-located `--help`／通知=`stage_block` が両テンプレのマーカー検証＋新内容を `*.aipair-new-$TS` に生成）。検証を全て通してから **commit フェーズ**で journal（`_apply target backup`）に記録しつつ適用し、**どの段階の失敗でも `_txn_rollback` が全適用先を逆順に復元**（`_die`）。旧実装の弱点だった「package+entrypoints は原子的だが通知は per-file `os.replace`」（2 つ目の通知テンプレ不正で binaries＋1 つ目の通知が中途半端に残る）を解消。テスト `install-upgrade.sh`（51 checks）: (A) 2 つ目の通知テンプレ不正→**STAGE で捕捉し何も commit されない**（binaries 未導入・1 つ目通知 unchanged・temp 残らない）、(B) commit 段階失敗（VS Code の `.vscode` をファイル化）→ **binaries＋両通知ファイルが pre-upgrade 内容へ journal ロールバック**、(既存) Phase2 途中失敗の entrypoint ロールバック。run-all 10 系統緑。
  - 追加修正（Codex レビュー relay-id:56486c49 反映）: (1) **最終 `smoke_test` 失敗も `_die`＝全 journal rollback** に（構文は通るが起動不能な `aipair` を残さない）。回帰テスト (C): `bin/aipair` を「`bash -n` は通るが実行時 `exit 1`」に差し替え → smoke 失敗 → binaries＋通知ブロックが pre-upgrade へ復元・staging 掃除を確認。(2) **skills / VS Code も stage→（backup→）atomic move→journal** に（旧: live へ直接 `cp` し journal は成功後で、途中書込み失敗で破損/新規ファイルが journal 外に残る）。temp は同一 FS 上で `mv` が atomic ゆえ live が中途半端にならない。install-upgrade.sh 56 checks・run-all 10 系統緑。
- [x] **P2-3 README/SECURITY/todo/TESTED_VERSIONS/CI 説明/schema・stop・plan protocol の同期をテスト化**（README の version==TESTED_VERSIONS、README の sentinel==コード定数）。
  - 完了: 新規 `tests/doc-sync.py`（run-all が自動発見・7 tests）で README/SECURITY をコードの source of truth に pin。**Versions**: README「必要環境」表の claude/codex 版 == `corelib.TESTED_VERSIONS`（bump 忘れ・stale 版の両方を検出。行内の全 `\d+.\d+.\d+` が tested 版のみであることを要求）。**Sentinels**: `build_parser` の argparse 既定（stop/next_ask/all_done/plan_ok）が `[AIPAIR_*]` 形式かつ、README の該当 env 変数と同一行に《その既定値》が記載されている（既定を改名したら落ちる）。**Exit codes**: README『relay の exit code』表（installer 用の別表と見出しで区別）== 正準集合 {0,2,3,4,5,6,7,130}、かつ `state_machine` の reason dict のコードが全て記載済み。**Schema protocol**: relay が schema 不一致で `return 7`／SECURITY が「exit 7」「fail-closed」を記述／`--allow-untested-schema` がコード（build_parser）・README・SECURITY で一致。ネガティブ検証で drift を確実に捕捉することを確認（vacuous でない）。nightly の pin 版は `corelib.TESTED_VERSIONS` を実行時に読む設計ゆえハードコードドリフト無し。run-all 11 系統緑。
  - 追加修正（Codex レビュー relay-id:72a11e14 反映）: (1) Sentinels テストが `AIPAIR_*` env 上書きを既定と誤認していた（`AIPAIR_STOP=CUSTOM` で失敗）→ setUp で AIPAIR_* を除去して build_parser し**組み込み既定のみ**を検査。(2) P2-3 対象の **todo/CI 説明も検査**する `TodoAndWorkflows`（3 tests）を追加: todo.md に TESTED_VERSIONS↔README の古い手動運用注記が残っていないこと／`nightly.yml` の job（upstream-latest-smoke・authenticated-e2e）が定義され README に記載／`ci.yml` の matrix（py3.8/3.13・tmux3.1）が README と一致。(3) 既存ドリフトを是正: todo.md の旧・手動運用注記2箇所→doc-sync 化、README「検証済み版を上げたら…」の旧 `bin/aipair-relay`/`VersionGate`→`bin/aipairlib/corelib.py`/`tests/doc-sync.py`、README の旧「5 lib/6 sibling module」→`aipairlib パッケージの各モジュール`。doc-sync 10 tests（env 上書き下でも緑）・run-all 11 系統緑。
  - 追加修正2（Codex レビュー relay-id:f7a73103 反映）: (1) `SchemaProtocol` も生の `build_parser()` を呼び `AIPAIR_MAX_ROUNDS=bad` で `SystemExit(2)` していた → parser 構築を共通 helper `_builtin_defaults()`（全 `AIPAIR_*` を除去）に集約し全テストで使用。`AIPAIR_MAX_ROUNDS=bad`／`AIPAIR_STOP=X` でも緑。(2) CI matrix テストを「版番号がどこかに在るだけ」から **matrix の3行（(3.8,distro)/(3.13,distro)/(3.13,3.1)）を集合一致で検証**へ強化＋現行 doc（ci.yml コメント・README）が旧 module count を使わないことを検査。ci.yml:18-19・todo.md:288 の旧「relay + 5 libs / 6 sibling module」を count-free 記述へ更新（todo の履歴 PR 注記は当時の事実として残置）。doc-sync 11 tests・run-all 11 系統緑。
  - 追加修正3（Codex レビュー relay-id:f91224a4 反映・**意味的契約まで固定**）: (1) nightly の `authenticated-e2e` が job 名検査だけでは版ハードコード化を見逃す → `corelib.TESTED_VERSIONS` の実行時読取（`T["claude"]`/`T["codex"]`）と `@$CV`/`@$XV` での npm 導入を検査。(2) stop/plan protocol が sentinel 値の存在しか見ず契約文削除を見逃す → `Protocol` クラスで「最終回答の先頭行に単独／否定文・引用・文中言及では不成立／`[AIPAIR_PLAN_APPROVED]` を先頭行に単独で」を README、「先頭行に単独／誤停止・誤承認しない」を SECURITY に固定し、**`corelib.hit_stop` の実挙動（lone head=真・inline=偽・2行目=偽）が doc 契約と一致**することを検証。(3) matrix テストが README 側は版存在しか見ない → README 3-lane 表を行単位でパースし ci.yml matrix と**集合一致**で相関。doc-sync 15 tests・run-all 11 系統緑。
- [x] **P2-4 versioned release 運用開始**（`aipair --version` / git tag / CHANGELOG.md / GitHub Releases）。
  - リリース基盤の整備（`__version__`/`aipair --version`/CHANGELOG/RELEASING/`release.yml`（tag==`__version__`・dated 見出し検証）/doc-sync）を先に完了。その後、社長の明示承認を受けて実発行した（下記）。
  - **v0.1.0 発行完了（`- [x]`・社長の明示承認「v0.1.0 リリースしていいよ」2026-08-23 受領）**: `RELEASING.md` 手順どおり実施。release commit（`CHANGELOG.md` を `## [0.1.0] - 2026-08-23` に確定＋新 `## [Unreleased]`＋`[Unreleased]` compare link／doc-sync の undated 断定 assertion を除去）を **PR #92（`0334681`）**で CI 3レーン緑マージ → merge commit に annotated tag **`v0.1.0`** を push → **`.github/workflows/release.yml` run [32646290187](https://github.com/inoutvillage/aipair/actions/runs/32646290187) が success**（全 step 緑: tag==`__version__`／**dated 見出しガード通過**／CHANGELOG 節抽出／`gh release create`）→ **GitHub Release「aipair v0.1.0」を公開（draft:False・notes 2096 文字を CHANGELOG から生成）** [releases/tag/v0.1.0](https://github.com/inoutvillage/aipair/releases/tag/v0.1.0)。実発行を API で観測して確定。

---

# aipair — 外部コードレビュー（2026-08-21 受領）対応

## チェックボックスの意味（endless relay が読むファイルなので厳密に）
- `- [ ]` = **着手可**（承認済み・依存解消済み）。endless モードの Codex はここからだけ次を選ぶ
- `- [x]` = 完了（検証済み）
- **判断待ちはこのファイルに置かない** → `tasks/decisions.md`（`[?]` D1〜D3）。決まったら具体タスクに落としてここへ移す

## 事実確認（確定）— レビュー 10 件、事実誤認ゼロ

| # | 指摘 | 結果 | 根拠 |
|---|---|---|---|
| 1 | 既定で permission bypass 起動 | 事実 | `bin/aipair:91-92`（README:19-20 で告知あり）→ D1 |
| 2 | queue が merge 前に本番 DB へ migrate deploy、expand-only 検証なし | 事実 | `bin/aipair-queue:204-217`, `:222-275` → D2 / F8 |
| 3 | tmux セッション名が basename のみで衝突 | バグ | `bin/aipair:21-24`（導出は 1 箇所、relay 側は `#{session_name}` を読むだけ）→ F1 ✅ |
| 4 | relay 102KB 単一ファイル | 事実 | 101,898 bytes / トップレベル def・class 55 → D3 |
| 5 | tests / CI なし | 事実 | `tests/` `.github/` 不在 → F6 |
| 6 | TUI 文字列依存・版ゲートなし | 事実 | `capture-pane` 14 箇所。README:61 の検証済み版表に実行時ゲートなし → F5 |
| 7 | `BRIDGE_CMD` のクォート崩れ | 事実 | `bin/aipair:70,76-78` → F2 |
| 8 | peer-log の毎周期 glob | 事実（指摘より悪い） | glob 後に全ファイル `getmtime` → 500 件 open、1.5 秒ごと（`bin/peer-log:66-68,269`）→ F3 |
| 9 | グローバル CLAUDE.md / AGENTS.md 注入 | 事実 | `aipair-install.sh:39-40`（書き込み自体は marker/backup/検証付き）→ F7 |
| 10 | 停止条件が Codex 自己申告（最終メッセージ冒頭 100 字） | 事実 | `bin/aipair-relay:885-895` → F4 |

分割時に効く事実: queue が relay を `SourceFileLoader` でモジュール読み込み（`bin/aipair-queue:44-50`）／`aipair-relay-here:26` が `~/.local/bin/aipair-relay` をハードコード／installer は `bin/*` をフラット配置／`peer-log` と relay のログ探索は手動同期の重複コード（`bin/peer-log:50`）。

## 着手可タスク（依存順）

- [x] **F1. セッション名衝突の修正**（P0 バグ）— `bin/aipair`、回帰テスト `tests/session-name.sh`
  - `aipair-<basename>-<正規化パスの sha1 先頭 12 桁>`（6 桁は約 5k ディレクトリで衝突が見つかったため 48 bit に）。正規化 = realpath + 大小文字を区別しない FS ではディスク上の綴りに統一（`canon_dir`: 各階層を `listdir` → NFC 正規化 + casefold 一致かつ `samefile` のエントリに置換。case-sensitive FS では完全一致が必ず存在するので `Case`/`case` は別のまま）。
  - **既存セッションの所有者判定は、作成時にセッションへ stamp する user option `@aipair-dir`（正規化 DIR）を優先**し、それが無い旧形式セッションだけ `#{session_path}`（作成時 `new-session -c` の値。`attach-session -c` で書き換わるので新形式では使わない）に fallback。比較は `-ef`（dev+inode、文字列一致を fallback）。新形式セッションが同名でも別ディレクトリのものなら「hash collision」として `name`/`attach`/`stop` とも exit 1（触らない）。新形式が無い時だけ旧形式 `aipair-<basename>` を同じ照合で引き継ぐ。`pane_current_path` は可変なので不使用。
  - tmux 3.2a の実測: `-t NAME` は前方一致 → has-session / list-panes / kill-session / attach は `=NAME` で完全一致。`set-option` / `display-message` / `send-keys` は `=` を受け付けないので、ペイン ID 指定か `list-sessions -F` の文字列完全一致で代替。
  - Codex レビュー（2026-08-21〜22）で追加した回帰ケース: (a) 別 dir の旧セッションの pane が対象 dir に `cd` していても採用しない／`stop` で巻き込まない、(b) DrvFs の大小文字違いが同一名・ext4 の `Case`/`case` は別名、(c) 別綴り `-c` の旧セッションを `-ef` で引き継ぐ、(d) 新旧共存時は新形式優先、(e) 同名だが別 `session_path` の新形式セッション → `name`/`stop`/`attach` が「hash collision」で exit 1 し対象を殺さない、(f) 6 桁では衝突するパスの組（テスト内で birthday 探索で生成）が 12 桁では別名、(g) 新形式セッションに `attach-session -c 別dir` で `session_path` を書き換えても `@aipair-dir` により元 dir のセッションと識別され、別 dir 側も誤採用しない。
  - テストの隔離: `set -euo pipefail`、一意 socket（`aipair-test-$$-$RANDOM`）、shim 経由の `#{socket_path}` を実 tmux `-L` の値と照合してから開始、cleanup は常に `"$REAL_TMUX" -L "$SOCKET" kill-server`（bare `tmux` を一切使わない）。
  - 検証: `bash tests/session-name.sh` → 38 ケース全通過。並列 2 本同時実行も両方全通過（互いのサーバーに干渉しない）。本番ペア `aipair-aipair` / `aipair-iovillage-cms` は無傷。`script` が無い環境では実起動ケースを skip。
- [x] **F2. シェル文字列組み立ての是正** — `bin/aipair` 起動セクション、回帰テスト `tests/launch-cmds.sh`
  - 仕様（互換維持）: `AIPAIR_CLAUDE_FLAGS` / `AIPAIR_CODEX_FLAGS` は**ペインのシェルが解釈するシェル断片**（`"--model opus"` → 2 引数、`'--append-system-prompt "a b"'` の引用符も有効、空文字 = フラグ無し）。README に明記。
  - それ以外の `AIPAIR_*`（`STOP` / `STOP_SIDE` / `MAX_ROUNDS` / `TASK_LIST` / `NEXT_ASK` / `ALL_DONE`）は bash 配列で組み立て、`q()` が必要な語だけ**シングルクォート包み**（`'it'\''s'`）にして 1 本の行にする。`printf %q` は不採用: C ロケールで `完了です` が `$'\345…'` になり画面で読めない。
  - 起動行は `clear; env AI_SELF=… AI_PEER=… <cmd> …` 形式（Codex 指摘: fish に `export` は無い。`env` 接頭は sh/bash/zsh/fish 共通）。副作用: ペインのシェル自体には `AI_*` が残らない（エージェントとその子プロセスには渡る。人間がエージェント終了後に同じペインで `peer` を打つと両ログ表示の fallback）。
  - 真偽値 env（`AIPAIR_ENDLESS` / `AIPAIR_DRY_RUN`）は `env_on()` で relay の `_env_bool` と同じ解釈（`strip().lower()` 後に `0/false/no/off` 以外が on）。`${v,,}` は macOS bash 3.2 で構文エラーなので `tr` で実装。以前は launcher が `AIPAIR_ENDLESS=0` でも `--endless` を付け relay の解釈を上書きしていたバグも同時に解消。
  - `AIPAIR_DRY_RUN=1`: 3 ペインへ打ち込む行・セッション名・bridge タイトルを表示して終了（何も起動しない）。README の env 表に追加。
  - 検証: `bash tests/launch-cmds.sh` → 34 ケース。表示行を**実際にシェルで評価**し、claude/codex/aipair-relay/peer-log/clear のシムが受け取った argv を比較。親環境の `AIPAIR_*`・`AI_*`・`TMUX`・`BASH_ENV` を全 unset（Codex の汚染環境再現 `AIPAIR_STOP=from-parent AIPAIR_ENDLESS=1 AIPAIR_MAX_ROUNDS=77` でも全通過）。インストール済みの各シェル（sh/dash/bash/zsh/fish）で同じ行を評価して argv 一致を確認する条件付きケース付き。`DRY_RUN` の on 5 値は dry run、off 4 値は私設 socket で実起動されることを確認。`tests/session-name.sh` 38 ケースも再実行して全通過。
  - 未検証（仮説）: zsh / fish はこのマシンに無く skip（`sh` は bash への symlink）。fish 互換は公式ドキュメント（`export` 非対応、`env VAR=x cmd` 可）に基づく。入っている環境で `tests/launch-cmds.sh` を回せば自動で検証される。
  - 範囲外メモ: `templates/vscode-tasks.json:71,86` の単体起動タスクは `bash -ic "export AI_SELF=… && claude --dangerously-skip-permissions"` のまま（bash 明示なので `export` は可。bypass 直書きの是正は D1 の対象）。
- [x] **F3. Codex rollout 追従の修正 + 探索キャッシュ** — `bin/peer-log`（共有ロジック `CodexIndex`）、`bin/aipair-relay`（利用側）、テスト `tests/codex-follow.py`
  - 欠陥 1（追従）: relay の `refresh_codex_lock` が `max(files)`（全体で最新 1 件）しか cwd 照合せず、別 cwd の Codex が動いている間は自分の cwd の新 rollout を永遠に見逃した（2026-08-21 に aipair / iovillage-cms の 2 ペア同時稼働で実際に踏める状態だった）。
  - 欠陥 2（探索コスト）: `--watch`（1.5 秒周期）と relay（約 1 秒周期）が毎回 `glob` + 全 rollout の `getmtime` + ソートを繰り返していた（Codex 指摘で 1 回目の修正では 1 行目の `open` しかキャッシュしておらず未解消 → 2 回目で解消）。
  - 修正（peer-log に集約し relay は `peerlog.` 経由で共用 → 手動同期の重複コードを解消）:
    - `codex_cwd(path)`: rollout 1 行目（session_meta.cwd、書かれたら不変）を path → 正規化 cwd でキャッシュ。書きかけ（改行なし）はキャッシュせず再読、壊れた 1 行目は `""` で確定。
    - `CodexIndex`（増分インベントリ）: 初回だけ全走査（glob + 全ファイル stat）、以後は **新しい rollout が現れうるディレクトリだけ**（sessions ルート／最新の年・月ディレクトリ／最新 2 日分の日ディレクトリ）を stat し、mtime が動いた時だけ list（ファイル作成は親ディレクトリの mtime を動かす。追記は動かさないが cwd は不変なので不要）。cwd ごとの新旧判定は**その cwd の直近 20 本だけ** stat（それより古いものは直近の全走査時の mtime）。安全網として 60 秒ごとに全再走査（想定外の場所に現れた rollout・`codex resume` で古い rollout に追記されたケース・消えたファイルの忘却はここで拾う）。mtime 粒度が粗い FS 向けに「2 秒以内に更新されたディレクトリは再 list」。監視ディレクトリは**存在しなくても `None` として監視し続ける**（sessions ルート未作成の新規マシン・空ルート・削除→再作成でも、出現を通常 poll で検知。Codex 指摘 3 回目で追加）。**消えたものを返さない 3 層**（Codex 指摘 4 回目）: 監視ディレクトリの消失は即 `full_scan()` で index を作り直す／日ディレクトリ再 list 時に消えたファイルを index から除去／監視外（キャッシュ mtime）の候補を返す前に必ず stat で存在確認し無ければ除去して再選択。`load()` は**探索（glob / stat / exists）を含む全体**を `OSError` で保護し、消えた場合 `(None, [])`（Claude 側 `claude_file` の glob→stat 競合も包含）。`codex_follow` は current の mtime 取得を `try` にし、消えていれば `codex_newest` で最初からやり直す（Codex 指摘 5 回目: `exists()` 直後の削除 TOCTOU）。
    - `codex_newest(cwd, newer_than, limit, exclude)` / `codex_follow(cwd, current)`（`current` より新しい cwd 一致が現れた時だけ乗り換え。旧ファイルは消えないので消失検知は使わない）。`--watch` は agent ごとに pin を保持。relay の `refresh_codex_lock` → `codex_follow`、`lock_codex`（新セッション待ちの間 毎秒呼ばれる）→ `codex_newest(exclude=seen)`、adopt 走査 → `codex_newest`。
  - 検証: `python3 tests/codex-follow.py` → 33 ケース全通過。追従（A 旧 / B 最新 / A 中間で A 中間へ、B に釣られない／A の新 rollout で乗り換え／書きかけ・壊れた 1 行目／`open` が増えない／`limit`／relay 3 関数）に加え、走査コスト: 300 本の他 cwd 履歴がある状態で**静かな 2 回目の poll は glob 0・listdir 0・stat ≤ 監視ディレクトリ数 + 自 cwd 2 本 + current**、履歴 301 本の cwd でも stat ≤ 監視ディレクトリ数 + 20、最新日ディレクトリの新ファイル／翌日ディレクトリの新ファイルは全走査なしで検知、古い日ディレクトリの新ファイルと resume 追記は全再走査で検知、消えたファイルは即スキップ→全再走査で忘却、ルート未作成／空／削除→再作成の 3 ケースは全再走査なしで検知、25 本（監視 20 本超）作成後のルート削除で `None` かつ `load()` 無例外→再作成を検知、監視外候補の消失で次候補へ、日ディレクトリ再 list での除去、読込直前の消失、`exists()` 直後の current 削除で次候補へ、`load()` が探索中の消失（codex / claude）で無例外。実アーカイブ（142 rollout）: cold 804 ms → **warm poll 1.14 ms / stat 7 / glob 0 / listdir 0**。実環境スモーク: `peer-log codex`・`peer-log both --watch`・`aipair-relay --help` OK。
  - 注意: 稼働中の relay（`~/.local/bin`）は旧コード。次回のインストール（`aipair-install.sh`）で反映。
- [x] **F6. テスト基盤** — `tests/run-all.sh`（一括ランナー）、`.github/workflows/ci.yml`、新規 `tests/relay-parsers.py`・`tests/queue-state.py`（先行の `session-name.sh` / `launch-cmds.sh` / `codex-follow.py` と合わせ 5 系統）
  - `tests/run-all.sh`: 検査対象は **shebang で自動判別**（`aipair-install.sh` / `bin/*` / `tests/*` の bash → `bash -n` + `shellcheck -S warning`、python3 → compile。判別不能なスクリプトは FAIL、`bin/peer` や runner 自身を含む必須ファイルが集合に無ければ FAIL — Codex 指摘で `bin/peer` と runner 自身の漏れを修正）→ 全テスト（実行ループだけ runner 自身を除外）。shellcheck 未導入なら **skip を明示**、CI では必ず実行。1 つでも失敗で exit 1、各結果を summary に列挙。
  - `.github/workflows/ci.yml`: push / PR で ubuntu-latest、`apt-get install tmux shellcheck` → `bash tests/run-all.sh`。
  - `tests/relay-parsers.py`（23 ケース）: `hit_stop`（最終メッセージ冒頭 100 字のみ・ナレーション複数・文中言及で止まらない）／`_env_str|int|bool`（不正値は exit 2、真偽値は launcher と同じ）／`find_panes`（タイトル・コマンド・レイアウト順の fallback、自ペイン除外。`tmux()` をモック）／`detect_plan_dialog`（画面から番号を読む・bypass 優先・プランパス抽出・非表示時 None）／`detect_question_dialog` + `_question_block`（フッターが最終行の時だけ・Chat about this 必須・プラン優先・タブバー有無）／`claude_done_ts` / `codex_done_ts`（stop_reason / task_started→complete・`since`）／`parse_claude` / `parse_codex`（meta 除外・tool 表示・壊れ行）。
  - `tests/queue-state.py`（6 ケース）: `next_task` は最初の top-level `- [ ]`（ネスト行は無視）／`[ ]`→`[>]`→`[x]` と `[!] 要人間` → 人間が `[ ]` に戻すと再投入／ユーザーの同時編集で行番号がずれても行内容一致で書換、未知行は False、末尾改行維持／欠損・空ファイル／`task_prompt` が行をそのまま含む。
  - 実行: `bash tests/run-all.sh` → 5 系統すべて緑（34 + 38 checks、33 + 6 + 23 tests）。shellcheck はこのマシンに無い → Codex が 0.10.0 を一時展開して CI と同じ `-S warning` を実行し 5 件検出（SC2088 ×2 installer の表示用 `~`／SC2033 `attach()` 関数名が `tmux attach` と衝突／SC2046 `unset $(compgen …)`／SC2164 runner の `cd`）→ 全件修正（局所 disable は installer の表示文字列 1 箇所のみ、他は改名・ループ化・`|| exit 1`）。**修正後の shellcheck 再実行は Codex 側で確認**（私の環境では未実行）。
  - README に「テスト・CI」節（各テストの対象と方式、TUI は fixture 固定で実 UI 変更時は fixture 更新）を追加。
- [x] **F4. 停止条件に機械ゲート（opt-in）** — `bin/aipair-relay`（`--gate` / `--gate-timeout` / `--gate-rounds`、env `AIPAIR_GATE*`）、テスト `tests/relay-parsers.py` `StopGate`、README「停止ゲート」節 + env 表
  - 停止ワード検知時（通常モードの claude/codex 側停止、endless のレビュー合格）に `--gate` のシェルコマンドを `--dir` で実行。成功なら従来どおり停止／次タスクへ（`gate_state.fails` を 0 に戻す＝endless ではタスクごとに上限）。失敗なら **Codex ではなく Claude に差し戻し**（出力末尾 40 行・1500 字を 1 行に畳んで添付。コンポーザへの入力は改行で送信されるため）。`--gate-rounds`（既定 3）回で **exit 6**（`aipair-queue` は非 0 を `[!] 要人間` にするので整合）。タイムアウト（既定 600 秒）は失敗扱い。
  - 差し戻しは既存の配達経路を再利用: Codex 側停止では `msg_claude`/`back_text` を差し替えてダイアログ経由（plan / question）も含む通常の配達へ、Claude 側停止では Claude へ直接 poke して state を `claude` のまま維持。未指定（既定）は分岐に入らず挙動変更なし。
  - 検証: `StopGate` 4 ケース（`run_gate` の成功/失敗/stdout+stderr/timeout/`--dir` で実行、`gate_tail` の 1 行化と上限、`gate_message` の内容、`gate_or_message` の状態機械: ゲート無し→通過／成功→通過＆カウンタ 0／失敗→差し戻し文＆カウント／上限で `(False, None)`／成功でリセット）。`bash tests/run-all.sh` 5 系統すべて緑。`aipair-relay --help` に表示。
  - Codex レビュー（2026-08-22）で 4 点修正: (P0) タイムアウト時にゲートを **新プロセスグループ（`start_new_session=True`）で起動→`os.killpg` TERM→KILL**、`... & wait` の孫プロセスも道連れにする（`subprocess.run(timeout=)` はシェルしか殺さず孤児が残っていた）。(P1) ゲートは **正規化済み cwd** で実行（`gate_or_message(a, gate_state, cwd)`。生の `a.dir` だと引用符付き `~` で FileNotFoundError）。(P1) 差し戻し文中のコマンド表示も **1 行化 + 200 字上限**（複数行 `AIPAIR_GATE` が poke に改行を入れて途中送信されるのを防止）。(境界) `--gate-timeout` / `--gate-rounds` / `--max-rounds` / `--plan-rounds` / `--question-rounds` の **CLI 値も 1 以上を起動時検証**（argparse `type=int` は 0・負数を通す。env は `_env_int` が既に検証）。
  - 追加テスト: プロセスグループ kill（marker が後から作られない）／正規化 cwd での実行／複数行コマンドが poke に改行を入れない／`CliBoundaries`（実スクリプトを subprocess 起動し、CLI・env の非正整数と不正 stop-side が exit 2）。`tests/relay-parsers.py` 33 ケース、`bash tests/run-all.sh` 5 系統すべて緑。
  - Codex レビュー 2 巡目（2026-08-22）で 4 点修正: (P0) `_kill_group` は **TERM → 0.5s 猶予 → 無条件 SIGKILL → `proc.wait` で刈り取り**。`(trap '' TERM; sleep; touch marker) & wait` で親シェルだけ TERM 死→子が TERM 無視でも 0.25s で SIGKILL され marker が作られない（旧実装は killpg(pgid,0) がゾンビ親を「生存」と誤検知して 2s 空回り＋子が仕事を終える窓が残った）。(P1) ゲート出力は **別スレッドで drain しリングバッファで末尾 256KB のみ保持**（`yes` 系の無限出力で OOM しない・パイプ満杯で待ちが詰まらない。切り詰め時は先頭に明示）。(P1) 出力は **`errors="replace"` でデコード**（`printf '\377'` の非 UTF-8 でクラッシュしない）。(P2) **exit 6 を一元化**: relay の終了タイトル `reason[6]="停止ゲート失敗"`／`aipair-queue` の `RELAY_EXIT[6]`／README の終了コード表・タイトル例。
  - Codex レビュー 3 巡目（2026-08-22）で 3 点修正: (P0) `run_gate` を **`try/finally` で timeout・KeyboardInterrupt・正常終了のいずれでも必ず `_kill_group`＋reader.join** するように。Ctrl-C は cleanup 後に **再送出**（別セッションのゲートを孤児化しない）。PGID は **Popen 直後に捕捉**して kill に使う（reap 後の pid 再利用グループへの誤送信を防止）。(P1) **バックグラウンドジョブを残してシェルが正常終了**（`(sleep 1; touch x) & true`）してもグループごと後始末。バッファは reader.join 後にのみ結合（同時変更を回避）。(P1) 出力を **`scrub_output` で無害化**: ANSI CSI/OSC/裸 ESC を除去、改行・タブ以外の制御文字を空白化 → `tmux send-keys` の `embedded null byte` と ESC のキー誤解釈を防ぐ。範囲は `unicodedata.category=="Cc"` 全体（C0＋**DEL 0x7f**＋**C1 0x80-0x9f**。`ord<32` だけでは DEL/C1 が漏れる — Codex 4 巡目指摘）。
  - 未検証（仮説）: 実 TUI 上での差し戻し配達（poke 経路自体は既存のものを流用）。実機では `AIPAIR_GATE=false aipair loop` で「差し戻し → 3 回で exit 6」を確認するのが良い。
- [x] **F5. 版ゲート** — `bin/aipair-relay`（`TESTED_VERSIONS` / `parse_version` / `detect_version` / `version_gate`、`--allow-untested-dialogs` / `--no-version-gate`、env `AIPAIR_ALLOW_UNTESTED_DIALOGS` / `AIPAIR_NO_VERSION_GATE`）、テスト `tests/relay-parsers.py` `VersionGate`、README「版ゲート」節 + env 表
  - 起動時に `claude --version` / `codex --version` を取得（`--version` の出力から最初のドット区切り数値を抽出）。検証済み `TESTED_VERSIONS`（claude 2.1.238 / codex 0.149.0、README 表と一致）と**不一致または取得不可**なら、**プラン承認ダイアログと質問リレーの自動操作だけを OFF**（`a.no_plan_review` / `a.no_question_relay` を立てる）。**poke 往復・transcript 読取は継続**。起動バナーに各 CLI の版と OFF 理由を表示。
  - オプトアウト: `--allow-untested-dialogs`（不一致でも自動操作を続行）、`--no-version-gate`（版チェック自体をしない）。既定は「不一致なら安全側に倒す」。gate は自動操作を OFF にするだけで、明示指定の `--no-plan-review` 等を ON に戻すことはない。
  - 検証: `VersionGate` 6 ケース（`parse_version` の各形式・None／`detect_version` が binary 実行・FileNotFound・timeout で None／一致→ダイアログ ON のまま／不一致→両方 OFF・rows の status／取得不可→未検証扱い／`--allow-untested-dialogs` で ON 維持しつつ bad は報告）。実機の `claude`/`codex` で `detect_version` が実際に版を返すことも確認。`bash tests/run-all.sh` 5 系統すべて緑。
  - Codex レビュー 2 巡目（2026-08-22）で 3 点修正: (P1) `parse_version` は **prerelease/build suffix を保持**（`2.1.238-beta.1` を検証済み `2.1.238` と別物として mismatch 扱い）。(P1) `detect_version` は **`returncode != 0` を取得不可（None）**に（`--version` が非 0 終了で stdout に数字があっても検証済み扱いしない）。(P1) 新 env（`AIPAIR_GATE*` / `AIPAIR_ALLOW_UNTESTED_DIALOGS` / `AIPAIR_NO_VERSION_GATE`）を **`bin/aipair`（loop 分岐）と `bin/aipair-relay-here`（ENV_ARGS）の両起動経路でフラグへ展開**（既存 tmux server は新セッションに env を継がせないため無言で無効になっていた。`AIPAIR_GATE*` も同じ穴だった）。`aipair-relay-here` に `AIPAIR_RELAY_BIN` 上書き（テスト用）も追加。
  - 追加テスト: `tests/env-forward.sh`（**既存の private tmux server**に対し `aipair loop` と `aipair-relay-here --print` を実行し、relay が実際にフラグを受け取ることを確認 / 7 checks）、`VersionGate` に prerelease→mismatch・非 0 終了→None。`bash tests/run-all.sh` 6 系統すべて緑。
  - Codex レビュー 3 巡目（2026-08-22）で 3 点修正: (P1) **stale env の打ち消し**: 既存 tmux server が古い `AIPAIR_*` を保持していると、現在値が空/0 でもペインに残って relay に読まれる（意図しないゲート実行・版ゲート無効化、stale な不正整数で `_env_int` が起動前に exit 2）。→ `bin/aipair`・`bin/aipair-relay-here` の両方で、relay が読む**全 `AIPAIR_*` を現在プロセスの値（未設定は空）で `env VAR=… relay …` として明示上書き**（フラグ変換に加えて）。(P2) `aipair-relay-here` の `truthy()` を **前後 trim のみ**に（`tr -d [:space:]` で全空白除去だと `"f alse"` を false 化して relay の `_env_bool` と食い違う）。(SC) `tests/env-forward.sh` の `AIPAIR_CLAUDE_FLAGS=` を `=''` に（ShellCheck SC1007）。テスト用に `aipair-relay-here` へ `AIPAIR_RELAY_BIN` 上書きを追加。
  - 追加テスト（`tests/env-forward.sh` 14 checks）: relay シムが **argv と受領 env の両方をダンプ**し、(1) 現在値がフラグ＆env に伝わる、(1b) **既存 server の stale `AIPAIR_GATE='stale gate'`/`NVG=1`/`AUD=1` が現在の空値で打ち消され**、argv に `--gate` が出ず relay env も空、(2) `relay-here --print` の launch 行が env pin とフラグを含む、を確認。`bash tests/run-all.sh` 6 系統すべて緑。
  - Codex レビュー 4 巡目（2026-08-22）で 3 点修正: (P1) parse_version を版トークン全体を捕捉する正規表現に（先頭は数字に接続しない d.d、末尾は必ず英数字＝区切りで終わらない）。2.1.238.1 / 2.1.238rc1 / 0.149.0.1 が検証済み 3 要素版へ切り詰められず mismatch になる。(P1) detect_version に errors="replace" を追加（非 UTF-8 の --version 出力で UnicodeDecodeError を出さず安全側＝取得不可へ）。(SC2034) tests/env-forward.sh の未使用変数 out を削除。
  - 追加テスト: parse_version の 4 要素版・glued suffix（TESTED_VERSIONS.values() に含まれない）・末尾区切り、detect_version の非 UTF-8 出力。bash tests/run-all.sh 6 系統すべて緑（relay パーサ 49）。
  - 注意: TESTED_VERSIONS と README「必要環境」表の一致は `tests/doc-sync.py` が強制（P2-3・2026-08-23 追加で自動検証化）。稼働中 relay は旧コード → 次回インストールで反映。

  - Codex レビュー 5 巡目（2026-08-22）で 1 点修正: (P1) 非 UTF-8 の --version 出力を「取得不可」に。errors="replace" はデコード例外を防ぐだけで、置換後の文字列を解析すると検証済み数字が拾えてしまい docstring/契約（安全側＝取得不可）と矛盾していた。→ 出力に置換文字 U+FFFD が含まれたら parse せず None を返す。version_gate はこれを未検証扱いにしダイアログ自動操作を OFF。
  - 追加テスト: 非 UTF-8 出力（U+FFFD 混じり）で detect_version が None、version_gate が両ダイアログを OFF。bash tests/run-all.sh 6 系統すべて緑（relay パーサ 50）。

- [x] **F8. migration の allowlist ゲート** — `bin/aipair-queue`（`screen_migration_sql` / `screen_migration_files`、merge_phase フック、`--allow-unsafe-migrations`）、テスト `tests/migration-screen.py`、README + docstring
  - D2 の結論に依らず、本番適用経路の防御として追加（`prisma migrate deploy` は破壊的で、コードが本番に出る前に本番 DB へ当たるため）。merge_phase が PR の migration `.sql`（`gh pr diff --name-only` の `prisma/migrations/*.sql`）を **適用前に検査**。違反なら `deploy_migrations` を呼ばず（本番 DB 未変更のまま）`[!] 要人間` 保留。
  - 許可（追加系のみ）: `CREATE TABLE` / `ALTER TABLE … ADD COLUMN`（NOT NULL は DEFAULT 付きのみ・複数 ADD 可）/ `CREATE [UNIQUE] INDEX [CONCURRENTLY]` / `CREATE TYPE` / `ALTER TYPE … ADD VALUE` / `CREATE EXTENSION` / `COMMENT ON`。**それ以外は全拒否** — DROP・DROP COLUMN・`SET NOT NULL`・型変更・RENAME・TRUNCATE・DELETE/UPDATE・DEFAULT 無し必須列・`$$…$$` の**コード本体**（DO/CREATE FUNCTION 等、先頭キーワードが非許可なので拒否。`$$…$$` は文字列リテラルとして lex し、`COMMENT ON … IS $$…$$` のようなデータ用途は許可）・パースできない文字列・括弧不整合・構文不完全。SQL コメント（`--` / `/* */`）除去・`;` 分割は文字列リテラル内を除外・大小文字非依存。
  - `--allow-unsafe-migrations` で検査スキップ（手動レビュー済みの時のみ）。
  - 検証: `tests/migration-screen.py`（8 tests）— 許可 13・拒否 12 の SQL fixture、複文で 1 つでも違反ならファイル拒否・該当文を報告、空/コメントのみは vacuously OK、文字列内 `;` で分割しない、`screen_migration_files` の複数ファイル・違反ファイル特定・読めないファイルは拒否。`bash tests/run-all.sh` 8 系統すべて緑。
  - Codex レビュー 2 巡目（2026-08-22, P0×2 + P1）で全面強化: (P0) 正規表現でなく **ステートフル lexer** に。文字列 `'…'`/識別子 `"…"`/コメント/`;`/`$$…$$` を状態遷移で処理し、リテラル内の `--`・`/* */`・`;`・キーワードに騙されない。トークン列で照合するので `ADD COLUMN "DEFAULT" … NOT NULL`（識別子 DEFAULT を DEFAULT 句と誤認）・`ADD CONSTRAINT …`（COLUMN でない）・`RENAME VALUE 'ADD VALUE'`（文字列内 ADD VALUE）を正しく拒否。閉じない引用符/コメントも拒否。(P1) **検査対象を PR head SHA に固定**: 内容をローカル作業ツリーではなく `git show <headRefOid>:<path>` で読む（`read_blob_at`）→ 検査。適用直前に `checkout <sha>`（`checkout_detached`）で作業ツリーを固定し、検査後・マージ直前の 2 箇所で head 未更新を再確認（動いていたら保留）。→ 検査＝適用＝マージが同一コミット。
  - 追加テスト（`tests/migration-screen.py` 9 tests）: 敵対 5 種（コメント/引用符偽装・DEFAULT 識別子・ADD CONSTRAINT・RENAME VALUE）＋閉じない引用符/コメント、`read_blob_at` が **dirty worktree を無視して commit 内容を読む**（worktree を DROP に書き換えても検査は safe な commit を見る）。`bash tests/run-all.sh` 8 系統すべて緑。
  - Codex レビュー 3 巡目（2026-08-22, P0 + P1×2）で更に強化: (P0) SHA 固定の **fail-open / TOCTOU** 解消 — head 取得失敗（`pr_head_sha=""`）も必ず保留、`gh pr merge` 全試行に **`--match-head-commit <head>`**（サーバ側で head 不一致なら拒否）、検査後・マージ直前の再確認も取得失敗を保留扱いに。(P1) `ADD COLUMN` が**非引用の bare identifier / schema-qualified table 名**も受理（`ALTER TABLE users ADD COLUMN age INTEGER` を通す。CONSTRAINT 等は文脈で拒否）。(P1) lexer/照合を**構文妥当性まで検証**: ブロックコメントの**ネスト**追従、各許可形式を**終端まで照合**（`CREATE TABLE DROP TABLE …`・`ADD VALUE`（値無し）・括弧不整合・余剰トークンを拒否）。dollar-quote は文字列リテラル扱い（DO/FUNCTION 本体は先頭キーワードで拒否）。
  - 追加テスト: 許可 15・拒否 13 の構文 fixture、`MergePhaseHeadPinning`（gh/git をモックし、head 取得失敗・マージ直前の head 変更・再確認失敗はすべて保留かつ非マージ、安定 head は `--match-head-commit` 付きでマージ）。`tests/migration-screen.py` 13 tests、`bash tests/run-all.sh` 8 系統すべて緑。
  - Codex レビュー 4 巡目（2026-08-22, P0 + P1）で更に強化: (P0) merge 後の成功判定を state だけでなく **MERGED かつ headRefOid == 固定 head** に。別主体が別コミットを外部マージして MERGED になっても成功扱いせず保留（`--merge` なので PR の headRefOid はマージされたコミットのまま＝判別可能）。(P1) 各許可形式を **canonical subset として終端まで消費**し、残余トークンを拒否（`_match_group` で対応する閉じ括弧位置まで消費 → 以降にトークンがあれば拒否）。`CREATE TABLE/INDEX/TYPE/EXTENSION/COMMENT ON … <balanced> DROP TABLE …`、`ADD COLUMN`（型必須・action verb 混入拒否）を全て拒否。
  - 追加 fixture 7（残余トークン 5 + 型無し + DROP 混入）+ 5（composite/shell type・extension schema・COMMENT NULL・index USING）、`test_foreign_merge_of_a_different_head_is_not_success`。tests/migration-screen.py 14 tests、bash tests/run-all.sh 8 系統すべて緑。

  - Codex レビュー 5 巡目（2026-08-22, P0 + P1×2 + テスト欠落）で更に強化: (P0) 検査対象を **deploy が実際に当てる pending 全件**に。head を先に checkout してから `prisma migrate status` の pending 集合（PR 分＋既存未適用）を parse（`parse_pending_migrations`）して全件検査（`screen_pending_migrations`）。PR 差分だけの検査だと main に残った破壊的 migration を見逃す。解釈不能な status は保留。read_blob_at は不要になり削除（checkout 後の作業ツリー＝deploy 対象を読む）。(P1) `_split_actions` が `[]` 深さも追跡し配列型/配列 DEFAULT（`TEXT[] DEFAULT ARRAY['a','b']`）を許可、末尾空 action を保持して `…INTEGER,` を拒否。`_parens_balanced` も `()`/`[]` 両方に。(P1) **危険動詞のグローバル拒否**（DROP/TRUNCATE/INSERT/GRANT/REVOKE/MERGE/DO/… がどこに出ても拒否）で `CREATE TABLE (DROP TABLE …)`・`CREATE INDEX(…DROP…)`・`COMMENT ON DROP TABLE …` を封鎖、COMMENT ON は対象種別も検証。
  - テスト欠落を修正: 前回「追加した」と述べた余剰トークン 7 件が実際には fixture に入っていなかった（Codex 指摘）→ ALLOWED 24 / REJECTED 30 を全面書き直しし grep 確認。PendingSet（parse・up-to-date・解釈不能→保留・pending 全件検査で pre-existing DROP 捕捉）を追加。tests/migration-screen.py 17 tests、bash tests/run-all.sh 8 系統すべて緑。

  - Codex レビュー 6 巡目（2026-08-22, P0 + P1×2 + P2）で修正: (P0) 直前の編集で **`prod_env` 定義が消えており migration PR は必ずクラッシュ**していた → 復元。さらに `deploy_migrations(cwd, env)` に変更し、merge_phase で `prod_env` を**一度だけ**取得して screen_pending_migrations と deploy_migrations に**同じ env** を渡す（別 DB を検査・適用しない）。(P1) pending parser を **fail-closed** に: 名前取得開始後の非空行が有効な ASCII migration 名でなければ（未知形式・非 ASCII 含む）`None` で保留、blank でリスト終端。(P1) **DELETE/UPDATE は `ON DELETE`/`ON UPDATE` の文脈のみ許可**（直前トークンが `ON` でない DELETE/UPDATE はどこでも拒否）→ `CREATE TABLE (DELETE FROM …)`・`CREATE INDEX(UPDATE …)` を封鎖。(P2) pending SQL 読み込みを `with open` + **strict UTF-8**（不正 UTF-8 は解析不能として保留、ResourceWarning も解消）。
  - 追加テスト: FK の ON DELETE/UPDATE 許可、group 内 bare DELETE/UPDATE 拒否、parser の部分解釈/非 ASCII→保留、非 UTF-8 migration→保留、migration PR が merge_phase を通り screen と deploy に同一 env オブジェクトが渡ることを検証。tests/migration-screen.py 21 tests、bash tests/run-all.sh 8 系統すべて緑（`-W error::ResourceWarning` でも警告なし）。

  - Codex レビュー 7 巡目（2026-08-22, P0 + P1×2 + P2）で修正: (P0) `migrate status` 判定を **fail-closed** に。`parse_pending_migrations(rc, text)` に rc を渡し、clean は **rc==0 かつ既知肯定文への行単位完全一致**（`_UP_TO_DATE`）のみ（`"…is not up to date!"` の部分一致を排除）。pending 一覧の後は footer allowlist と異常語（error/panic/truncat）を最後まで検証し、未知/異常行は保留。`deploy_migrations` の事後確認も共通 `migrate_status_clean` に統一。(P1) **`SET NOT NULL` を明示拒否**（`_ACTION_VERBS` に `SET` 追加 + `SET NOT NULL` 隣接のグローバル拒否）。(P1) localhost ガードを **URL パース + `ipaddress`** に（`urlsplit().hostname` を正規化し `localhost`/`127.0.0.0/8`/`::1` 等の loopback 全体を拒否、host 判定不能も安全側で拒否）。(P2) `prod_env` が `.env.production` の **不正 UTF-8 で `UnicodeDecodeError` を捕捉**して保留（キュー全体の例外終了を防止）。
  - 追加テスト: SET NOT NULL 拒否、rc/行完全一致/footer 異常/truncation の status 判定、`migrate_status_clean` の rc 要件、loopback ガード（localhost/LOCALHOST/127.0.0.2/[::1] vs 本番ホスト）、prod_env の非 UTF-8 と loopback 拒否。tests/migration-screen.py 26 tests、bash tests/run-all.sh 8 系統すべて緑（ResourceWarning なし）。

  - Codex レビュー 8 巡目（2026-08-22, P0 + P1）で更に強化: (P0) status 判定を全面 fail-closed に。異常語 `_STATUS_ANOMALY`（error/panic/truncat/fail/warning/could not/not up to date）を**出力全体**へ適用、pending header 時は**想定終了コード（rc==1）以外を拒否**（rc=-1/0/2/137 は保留）、footer は substring でなく **`_FOOTER_RE.fullmatch`**（`Warning: migrate deploy could not …` を通さない）。`migrate_status_clean` も rc==0＋既知肯定文の行完全一致＋異常語なし。(P1) loopback ガードを **PostgreSQL-URI aware** に: netloc から userinfo/port を除き **複数 host（`host1,host2`）を個別判定**、各 host は **percent-decode（`local%68ost`）・末尾ドット・`[::1]`・`*.localhost`・`socket.inet_aton` による IPv4 短縮/整数表記（`127.1`/`2130706433`）** を解決して 127.0.0.0/8・::1 を拒否。
  - 追加テスト: rc 別 pending 保留、clean 行＋末尾 ERROR は非 clean、footer Warning 保留、loopback バイパス 11 種（localhost./foo.localhost/127.1/2130706433/local%68ost/多 host）と本番ホスト許可。tests/migration-screen.py 29 tests、bash tests/run-all.sh 8 系統すべて緑。

  - Codex レビュー 9 巡目（2026-08-22, P0 + P1 + P2）で更に強化: (P0) footer を `to apply\b.*` の任意後続許可から **既知の文型のみ fullmatch**（`to apply (this|these|the)? (pending)? migrations,? run (the following command)?:?` 等）に。`To apply only the listed migrations; others were omitted` を保留。(P1) loopback ガードに **Unix-domain socket（decode 後の絶対パス host `%2Fvar%2Frun%2Fpostgresql`・`%2Ftmp`）** と **unspecified アドレス（`0.0.0.0`・`::`＝ローカル listener 到達）** を追加拒否（`ip.is_unspecified` + `inet_aton` 先頭 0）。(P2) 異常語検索の false-positive を解消: `_STATUS_ANOMALY` を**行頭アンカー/単語境界**（`^\s*(error|warning|panic|fatal)\b` + `\bcould not\b`/`\btruncat`/`\bnot up to date\b`）にし、裸の `fail` を排除。さらに **migration 名の区間を異常語検索から除外**（ヘッダ前と footer のみ検査）→ `20260822000000_add_failed_login_count` や host `db-failover.example.com` を誤拒否しない。
  - 追加テスト: footer の任意後続文保留・正規 footer 許可、名前/host の `fail` 誤検知なし、loopback バイパス Unix socket 2 種 + `0.0.0.0`/`::`。tests/migration-screen.py 31 tests、bash tests/run-all.sh 8 系統すべて緑。

  - Codex レビュー 10 巡目（2026-08-22, P0 + P1）で更に強化: (P0) footer 判定を `_is_footer_line`（2 つの exact 正規表現）に。実 Prisma の `To apply migrations in production run prisma migrate deploy.` 等の案内文を許可しつつ、コマンド行は **`[$ ] [npx|yarn|pnpm] prisma migrate deploy [.]` のみ**に限定 → `$ prisma migrate reset --force`・`$ rm -rf /`・`$ prisma migrate deploy && curl evil` を保留（旧 `\$ .*` の任意コマンド許可を排除）。(P1) ヘッダ前診断の異常語に **DB 状態の既知フレーズ**を追加（`failed migration`/`found failed`/`diverged`/`drift detected`/`migration history`/`not found locally`/`not in a valid state`）。Prisma は接続エラー・履歴 divergence・failed migration・通常 pending がいずれも rc=1 で終了コードでは区別できないため、フレーズで判定。空白入りフレーズなので名前 `…_add_failed_login_count` や host `db-failover…` は誤検知しない。
  - 追加テスト: production footer 許可 + shell コマンド 3 種保留、DB 状態診断 6 種の保留。tests/migration-screen.py 33 tests、bash tests/run-all.sh 8 系統すべて緑。

  - Codex レビュー 11 巡目（2026-08-22, P0×2）で更に強化: (P0) footer instruction が `prisma migrate (deploy|dev)` の両方を許可（Prisma の実出力は development 行『…run prisma migrate dev.』と production 行『…run prisma migrate deploy.』の 2 行併記）。(P0) loopback ガードが **`?host=` / `?hostaddr=` query パラメータ**も解析（`parse_qs`）: libpq/Prisma は `host` を Unix-domain socket ディレクトリとして使い netloc host を無視するため、`?host=/tmp`・`?host=%2Fvar%2Frun%2Fpostgresql`・`?hostaddr=127.0.0.1`/`::1` を拒否（`_host_is_loopback` を再利用）。通常の `?sslmode=require` や実ホスト指定は許可。
  - 追加テスト: dev+prod 2 行 footer の許可、query host/hostaddr の loopback バイパス 4 種 + 通常 query 許可。tests/migration-screen.py 34 tests、bash tests/run-all.sh 8 系統すべて緑。

  - Codex レビュー 12 巡目（2026-08-22, P1）で修正: loopback ガードの `?host=`/`?hostaddr=` 解析で **複数 host・空 host** を処理。`parse_qs(..., keep_blank_values=True)`（空 host= は libpq のデフォルト Unix socket＝local）＋各値をカンマ分割して全要素を `_host_is_loopback` に通す。`?host=db.prod,localhost`・`?host=db.prod,%2Ftmp`・`?hostaddr=10.0.0.5,127.0.0.1`・`?host=` を拒否、`?host=db.prod,other.prod` は許可。tests/migration-screen.py 34 tests、bash tests/run-all.sh 8 系統すべて緑。

- [x] **F9. ペア内 tmux 操作のガードレール周知**（2026-08-21 の障害起点、`tasks/lessons.md` 参照）— `templates/claude-md-block.md` / `templates/codex-agents-block.md` / `.claude/skills/aipair-setup/SKILL.md`、回帰テスト `tests/broadcast-blocks.sh`
  - 両周知ブロックの Notes に「ペア内で tmux を使うテストは専用サーバー（`tmux -L <名前>` / `-S`）必須。`$TMUX` が `TMUX_TMPDIR` より優先されるので `TMUX_TMPDIR` 隔離は効かない。引数無しの `tmux kill-server` は本番ペアを巻き込むので禁止、破壊前に `#{socket_path}` で確認」を追記。`aipair-setup` スキルに同趣旨の「安全メモ」節（`tasks/lessons.md` へのポインタ付き）。
  - installer の marker ブロック更新で既存環境にも次回インストールで反映される。**temp HOME で `aipair-install.sh` を実走**し、guardrail が `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md` の両方に入り、marker が各 1 対・「残りはバイト一致」検証を通ることを確認（smoke も自セッションのみ生成・撤収でペア無傷）。
  - 回帰テスト `tests/broadcast-blocks.sh`（10 checks）: 両テンプレ＋スキルが `kill-server` / `tmux -L` / `tasks/lessons.md` を含み、marker が各 1 対であること。`bash tests/run-all.sh` 7 系統すべて緑。
> **F7. グローバル CLAUDE.md / AGENTS.md 注入の縮小**（P2）は調査の結果、**社長判断が要る設計項目**と判明し `tasks/decisions.md` の **D4** へ移動（着手承認済みの `- [ ]` だけをこのリストに残すルールに従う）。
> 調査結論: Claude は `--append-system-prompt-file` で aipair セッションにスコープ可能（動作確認済み）、Codex は per-session の綺麗な追記手段が無くグローバル/`AGENTS.md` 依存（非対称）。グローバル注入を削るのは既存ユーザーの挙動変更なので方針決定待ち。

## D3 relay 分割（案A）— 残り増分（社長承認済み・順次実装）

> 案B（純粋関数テスト網 = `tests/relay-parsers.py` 52 ケース）完了。案A は `bin/aipair-corelib` への
> 抽出（増分1）を実施済み。残りを increment 単位の `- [ ]` にする。各 increment は「移設 → relay は
> `SourceFileLoader` + 名前束ねで従来どおり呼ぶ → tests 緑 → installer に同梱 → smoke」を満たすこと。
> 実戦投入済みの統合コードなので、tmux 結合が薄い順に、テストが緑を保つ範囲で少しずつ割る。

- [x] **A2. ログ/ターン検出ヘルパの抽出** — `claude_done_ts` / `codex_done_ts` / `turn_texts` /
  `find_poke_ts` / `codex_response_complete` / `claude_response_attributed` / `make_fragment` を
  **`bin/aipair-loglib`**（peer-log を SourceFileLoader で読む・tmux 非依存）へ抽出。relay は名前束ねで従来どおり呼ぶ。
  installer に配布/preflight/ロード確認を追加。`LoglibStandalone`（単体ロード + relay 再公開の同一性）と既存 `DoneTimestamps`/transcript テストで担保。end-to-end（temp HOME で installer→relay ロード→smoke）確認済み。relay-parsers 54 tests・9 系統緑。
- [x] **A3. tmux ランナー/ペイン操作の抽出** — `tmux()` / `find_panes` / `own_pane` /
  `current_session` / `capture_pane` / `pane_busy` / `cancel_copy_mode` / `set_pane_title` を
  **`bin/aipair-tmuxlib`**（stdlib のみ・色定数不使用）へ抽出。relay は名前束ねで従来どおり呼ぶ。installer 配布/preflight/ロード確認 + README 追記。テスト: `TmuxHelpers`（`current_session` 正常/例外・`own_pane` の TMUX_PANE 有無/別セッション/例外・`cancel_copy_mode` の in-mode 分岐・`pane_busy` の fast path と差分 3 行境界・`capture_pane`/`set_pane_title`）、`TmuxlibStandalone`（単体ロード + 再公開同一性）。既存 `FindPanes` は tmuxlib.tmux へパッチ先修正。end-to-end（installer→relay ロード→smoke）確認済み。relay-parsers 71・9 系統緑。
- [x] **A4. 配達（poke）の抽出** — `poke` / `submit_enter` / `paste_text` / `press` を **`bin/aipair-deliverylib`** へ。relay への循環 import なし。tmux 系（`tmux`/`cancel_copy_mode`/`pane_busy`）・`dim`・`dialog_on_screen`（`_dialog_on_screen`）・`BUSY_WAIT` を relay が**明示注入**（`deliverylib.X = …`、main で `deliverylib.BUSY_WAIT = max(60, a.busy_wait)`）。未注入 `tmux` は RuntimeError で明示。テスト `Delivery`（press/paste の copy-mode 解除、submit_enter の confirm/badge 成功・ダイアログ検知で Enter 中止・3 回再試行失敗、poke の nonce 確認で probe 返却・配達失敗で falsy かつ Enter 撃たない・busy 待機後続行／fake clock で高速化）、`DeliverylibStandalone`（単体ロード・安全既定・未注入 tmux 例外・再公開同一性）。installer/README 同梱。end-to-end 確認済み。relay-parsers 80・9 系統緑。
- [x] **A5. ダイアログ検出/応答の抽出** — `detect_plan_dialog` / `detect_question_dialog` / `_question_block` / `scrape_questions` / `send_plan_feedback` / `send_question_answer`（+ `_dialog_on_screen`→`dialog_on_screen`・`newest_plan`）とダイアログ定数/正規表現（`PLAN_QUESTION` / `QUESTION_FOOTER` / `QUESTION_CHAT_LABEL` / `_OPT_RE` / `_SEP_RE`）を **`bin/aipair-dialoglib`** へ。relay 循環 import 無し。host が tmux/capture・配達（press/paste_text/submit_enter）・make_fragment・dim を注入。**`deliverylib.dialog_on_screen` を dialoglib の検出関数へ再ポイント**（A4 で relay._dialog_on_screen を注入していた箇所）。`PLAN_QUESTION` は relay の `claude_matches_pane` が使うため再公開。既存 Plan/Question fixture のパッチ先を `relay.dialoglib.*` に修正。テスト `DialogSendScrape`（2 タブ scrape・capture 失敗・plan revise（submit_enter+watch confirm）・plan approve（BTab+watch/badge/失敗）・question answer（watch 有無）／fake clock）、`DialoglibStandalone`（単体ロード・未注入例外・再公開同一性・deliverylib 再ポイント確認）。installer/README 同梱。end-to-end 確認。relay-parsers 88・9 系統緑。
- [x] **A6. relay を薄いランチャ化（最終化）** — `main()` の状態機械 + オーケストレーション核（env 解析・色/ログ・log-locking + `LogWatch`・停止ゲート runner・poke 文面）は relay に残し、6 sibling モジュール（peer-log / corelib / loglib / tmuxlib / deliverylib / dialoglib）を先頭でロード・束ねる形に整理。relay docstring に **module layout** を明記。`aipair-relay-here` に **ライブラリ・ロード検証**（`"$RELAY" --help` で全 sibling の import を確認 → 不完全インストールは bridge ペインで crash させず relay-here 時点で loud に die）を追加。installer は 6 lib の配布/preflight/ロード確認済み。テスト: `tests/relay-here-libcheck.sh`（完全一式は load gate 通過・1 lib 欠落で非 0 + 「ロードできない」）、`ModuleLayout`（6 lib ロード・各代表 binding が lib 実装を指す・deliverylib→dialoglib 注入・核は relay に残存）。relay 当初 111KB→81KB / 30 defs。10 テスト系統緑。
依存: A2 → A3 →（A4, A5 は A3 後・並行可）→ A6。**A2〜A6 すべて完了（D3 案A 完了）**。

## 依存関係
F1 ✅ → F2 ✅ → F3 ✅ → F6 ✅ → F4 ✅ → F5 ✅ → F9 ✅ → F8 ✅。**F7 は D4（社長判断待ち）へ移動**。D1〜D3（`decisions.md`）は方針確定後に具体タスク化してここへ追加。

## レビュー
- F1（2026-08-22、Codex 指摘 計 5 件 + 補足 1 件反映後）: `bin/aipair`・README 1 段落・`tests/session-name.sh` 新規。38 ケース全通過（逐次 + 並列 2 本）。
  未検証（仮説）: tmux 内からの `switch-client -t =NAME`（ヘッドレスで実行不能。attach と同じ target-session 解決なので同挙動の見込み）／macOS APFS の NFC/NFD（コードは `unicodedata.normalize("NFC")` で対応、README では「未検証」と明記）。

## 追記: compaction 帰属バグ + dogfood 自己ホスト（2026-08-23）— main マージ済み・CI 緑

> レビューループ中に relay の診断（「応答チェーン不一致で棄却」）から実バグを発見・根治し、
> 新コードを稼働ペアへ自己ホスト（dogfood）した記録。todo の新規タスクではなくループ派生。

- [x] **compaction が応答帰属の parentUuid 鎖を切断（実機ループ停止バグ）**（PR #19 `5b003fa` → 精緻化 PR #20 `8d74933`）—
  Claude Code は文脈圧縮時に `{type:"system", subtype:"compact_boundary", parentUuid:None}` の新ルートを書き
  parentUuid 鎖を切断。「poke→圧縮→応答」で `claude_response_attributed` が pre-compaction の nonce に届かず
  正当な応答を永久に棄却→ループ停止。境界の `logicalParentUuid`（正確な圧縮前祖先）を親として祖先探索を
  続ける方式で根治（PR #19 は行位置ベース→ Codex 指摘で PR #20 が logicalParentUuid に一本化・欠落は
  fail-closed）。schema probe にも parentUuid 検査を追加。実停止トランスクリプトで False→True を確認。
- [x] **dogfood: 新コードを稼働ペアへ自己ホスト（relay だけホットスワップ）**（社長判断・案1）—
  private `-L` socket に tmux を shim して `aipair-install.sh` を実行（既定 server の smoke 汚染を回避、
  4 セッション不変）→ 旧 relay を Ctrl-C → `aipair-relay-here --adopt` で新 relay 点火。起動バナーで
  版ゲート（claude 2.1.240≠2.1.238→ダイアログ自動操作 OFF）・schema probe（実ログ OK）・adopt
  （claude=%9/codex=%11）がライブ検証された。launcher 側（transactional/`@aipair-*` スタンプ）は次の
  フレッシュ起動で検証（このセッションを殺さないため持ち越し）。
- 申し送り: 稼働 relay は PR #19 の位置ベース版のまま（線形ループでは実害なし）。次回インストールで
  logicalParentUuid 版へ同期。live claude が 2.1.240（TESTED=2.1.238）→ 版ゲートがダイアログ自動操作を
  OFF 中（安全側）。2.1.240 の実 TUI 確認後に `TESTED_VERSIONS` 更新を検討（盲目的 bump はしない）。

## 追記: マージ後のレビュー対応（CI 互換・診断）— すべて main マージ済み・CI 緑

> F1〜F9 / D1〜D4 完了後、PR #1 を main へ入れた後の Codex レビューで判明した互換・診断の追対応。
> いずれも todo の新規タスクではなくレビュー派生。記録のため完了ログとして残す。

- [x] **tmux 3.4 互換**（PR #1 内で対応）— `list-sessions -F` が制御バイト（`\x1f`）を `\037` に
  エスケープする挙動で `session_dir_of` のパースが破綻 → CI（ubuntu-24.04 / tmux 3.4）が赤。
  区切り依存を排し、テスト用サーバの空落ち（exit-empty）と `kill-server`→`new-session` 競合にも対処。
- [x] **tmux 3.1 の所有者/衝突ガード復活**（PR #2, main `5febec1`）— `list-sessions -f`・`#{session_path}`
  は共に 3.2+（対応下限は 3.1）。3.1 で `-f` がエラー→握り潰しで `session_dir_of` が空を返し、legacy
  採用だけでなく所有者・ハッシュ衝突チェックまで無効化されていた。区切りも `-f` も使わない単一経路
  （`list-sessions -F '#{session_name}'` で厳密存在確認 → `display-message -p '#{@aipair-dir}'`/
  `'#{session_path}'` の単値取得）に統一し 3.1〜3.4 全対応。回帰テスト [10]（tmux 3.1 をシムで再現）追加。
- [x] **衝突診断に `@aipair-dir` 表示 + コメント/テスト修正**（PR #3, main `e21ac78`）— hash-collision の
  inspect 行が `#{session_path}` のみで tmux 3.1 では所有者が見えなかった → `#{@aipair-dir}` を追加。
  採番コメントを「@aipair-dir 優先」へ更新。`tests/session-name.sh` の未エスケープ `` `script` ``
  （コマンド置換で `script` 起動しうる）を修正。

## 外部レビュー #2（2026-08-22 受領）— トリアージ

> 現行 main に対する2度目の外部レビュー（総合 7.0/10）。最優先の P0 のみ即対応・main マージ済み。
> 残りは着手前に方針判断が要るもの（`[?]`→decisions 相当）と、承認済みで着手可のものを分ける。

- [x] **P0. `peer` を起動ペアのセッションへ pin**（PR #4, main `aca462c`）— bin/aipair（Claude=`--session-id`、
  Codex=`AIPAIR_CODEX_SINCE`）／bin/peer-log（uuid glob 解決・codex_since 最古 pin）／tests/peer-pin.py 13件。
  README も更新（peer 説明・pin env・「6本」ドリフト修正）。

### 残（未承認＝着手前に社長/ユーザー判断。ここでは記録のみ、自動着手対象にしない）
- P1: `aipair loop` を既存セッションに対して実行すると relay を起動せず attach で終わる（しかも既存
  Claude/Codex は unsafe で起動し直されない）。案: 既存ペアなら bridge で relay を開始 / または「既存
  session には適用不可」と明示エラー。→ 挙動の選択が要る。
- P1: 自走モードがフル権限バイパス必須。readonly / workspace-write のみ / push 禁止 等の中間モードが無い。
  → Codex の `--sandbox`/approval 連携など設計判断が要る。
- P1: 版ゲートが TUI 文字列にしか掛からず、JSONL schema（stop_reason/parentUuid/turn 検出など）依存の
  コア relay は版不一致でも動き続ける。→ ゲート概念をコアへ拡張する設計。
- P1: relay 分割の続き（本体まだ ~82KB。helper 5個を外した段階）。→ D3 案A 残り（メインループ移設）を
  「触れた時に段階的に」。
- P2: CI が ubuntu 単一＋実 CLI 無し。→ tmux/OS マトリクス、境界の E2E（実 claude/codex は困難）。
- P2: tmux セッション作成の途中失敗に rollback（trap で partial session を kill）が無い。
- P2: README と実装のドキュメントドリフト（今回「6本」は修正。他も棚卸し）。
- P3: グローバル `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md` 注入（D4 で現状維持決定済。レビューは再指摘）。

## 外部レビュー #3（2026-08-22, 総合 7.5/10）— 優先4項目 + README 対応

> `peer` の identity 修正後の再レビュー。最優先4項目を PR #8（main `156c66c`, CI 緑）で対応。

- [x] **#1 relay の Codex 同定を peer-log と一本化**（最優先）— relay の adopt/lock/refresh を
  `codex_via_pane(cwd) or <従来>` にし、identity を単一 source of truth 化（`peer` と `relay-here` が
  別セッションを追う矛盾を解消）。非対応/未起動時のみ従来 mtime 方式へフォールバック。
- [x] **#2 aipair-relay-here の cwd を `@aipair-dir` 基準に** — `--dir "$PWD"` を撤去、対象セッションの
  `@aipair-dir`（無い旧セッションのみ $PWD）を正とし、明示 `--dir` で上書き可。
- [x] **#3 既存ペアへの `aipair loop` を無言 attach → exit 3 でエラー誘導**（`aipair-relay-here` を案内）。
- [x] **#4 起動を transactional 化** — `new-session` 直後に EXIT trap で kill、完成後に解除（build 途中
  失敗で half-built セッションを残さない）。
- [x] **#10 README の安全既定の矛盾修正** — 「既定で許可確認なし」を `--unsafe` 使用時の注意へ。

### #1〜#4 の Codex レビュー派生の精緻化（マージ済み）
- [x] PR #9 `167d879`: relay identity を解決済み pane に固定（TMUX_PANE 非依存）／一時失敗で drift しない／
  session 完全一致（=name）／`--dir` を canonical 化。
- [x] PR #10 `aaac803`: 初回 lock の mtime fail-open 停止／capability を明示 pane で安定化／非-/proc fallback を
  `codex_since` で peer と共有／relay-here に canon_dir。
- [x] PR #11 `cf03290`: `@aipair-codex-since` を peer-log 同等に検証（fail-closed）／必須メタデータ保存失敗を fatal 化。

### 残（外部レビュー #3 の #5〜#12・着手可）
- [x] **#5 `aipair status`＋ pane ID 保存**（main 反映予定）— 起動時に `@aipair-claude-pane` /
  `@aipair-codex-pane` / `@aipair-bridge-pane` を必須メタデータとして保存（保存失敗は transactional trap で
  ロールバック）。`find_panes` はこの記録を最優先（無ければ従来ヒューリスティック）＝Codex=node・タイトル
  上書きへの依存を排除。`aipair status` サブコマンドで claude/codex/bridge の pane と running/idle を表示。
- [x] **#6 版ゲートを JSONL schema の feature probe まで拡張** — `bin/aipair-corelib`（`schema_probe`/`schema_gate`）、`bin/aipair-loglib`（`read_records`）、`bin/aipair-relay`（起動バナー＋実行時 latch `schema_watch`、`--allow-untested-schema`/`--no-schema-probe`、env `AIPAIR_ALLOW_UNTESTED_SCHEMA`/`AIPAIR_NO_SCHEMA_PROBE`）、`bin/aipair`・`bin/aipair-relay-here`（env 転送＋stale 打ち消し）、テスト `tests/relay-parsers.py` `SchemaProbe`、README「版ゲート／schema ゲート」。
  - 動機（レビュー #3 P1）: 版ゲートは `--version` 文字列しか見ず、**コア relay がログを「キー」で読む**（claude: `type=="assistant"`＋`message.stop_reason`＋`timestamp`＋`uuid`/`parentUuid`／codex: `type=="event_msg"`＋`payload.type` task_started/complete＋`timestamp`）依存はノーチェックだった。CLI 更新で版文字列が「検証済み」のままキーが移動するとターン検出が黙って壊れる。
  - `schema_probe(agent, records)` は純関数（corelib）で 3 値 `ok`/`unverified`/`mismatch`。**積極的ドリフトのみ mismatch**（その種のレコードは在るのに relay が読むサブフィールドが欠落）＝空/生まれたてログは `unverified` で**ブロックしない**（起動直後の誤検知ゼロ）。claude は「型名ドリフト（inner role=assistant だが type≠assistant）／stop_reason 欠落／timestamp 欠落／uuid 欠落」、codex は「task 事件が event_msg 外／timestamp 欠落」を検出。1 本でも整形式なら即 ok。
  - 起動時は明示 pin（`--claude-log`/`--codex-log`）だけ probe できる（`aipair loop` はログ未生成→`unverified`）。実運用のドリフトは**実行時 latch**が拾う: メインループ先頭で `schema_watch()` が tracked ログを bounded tail 読み（`read_records`＝deque で末尾800行・メモリ有界）→ probe、エージェントごとに一度だけ latch。`mismatch` で大きく警告＋ベル、`--allow-untested-schema` 未指定なら版ゲートと同じ安全姿勢（ダイアログ自動操作 OFF・`a.schema_mismatch`）。
  - 検証: `SchemaProbe` 15 ケース（claude/codex の ok・空/nascent=unverified・各ドリフト=mismatch・1本先勝ち・schema_gate の劣化/unverified 不発/allow・`probe_log_schema`/`read_records` の tail・junk スキップ・欠損=unverified）。**実データ**（本セッションの claude jsonl・codex rollout）で ok＝誤検知ゼロを確認。`tests/env-forward.sh`（14→**28 checks**）で新 env 2 本の転送＋stale 打ち消しを確認。`bash tests/run-all.sh` 10 系統すべて緑（relay パーサ 108）。
  - 派生（同 PR・private-socket 衛生の完遂）: `tests/env-forward.sh`・`tests/session-name.sh` の cleanup が private `-L` **socket ファイルも削除**するように（従来は `kill-server` のみで死んだ socket inode が /tmp に蓄積＝`tasks/lessons.md` ガードレール）。PR #17 が install テストで行った隔離を残り 2 テストへ展開し、`run-all` 全体が private socket をゼロリーク。既定 server は前後 4 セッションで不変。
  - 注意: `TESTED_VERSIONS` と README「必要環境」表の一致は `tests/doc-sync.py` が強制（P2-3・2026-08-23 追加で自動検証化）。稼働中 relay は旧コード→次回インストールで反映（installer は corelib/loglib を配布済み）。
- [x] **#7 `aipair-relay` + 5 lib + peer-log を通常の Python package `aipairlib` 化**（PR #34 `merged`、3 レーン緑）— `bin/aipairlib/`（relay/peerlog/corelib/loglib/tmuxlib/deliverylib/dialoglib＋共有 logs）へ再編し、`SourceFileLoader`＋実行時属性注入を通常 import／明示依存へ置換（delivery↔dialog 循環は module import＋call-time、`dim` は共有 logs＋`configure()`、`BUSY_WAIT` は `poke(busy_wait=)` 引数）。`bin/aipair-relay`・`bin/peer-log` は薄い entrypoint、旧フラット lib 削除＋RETIRED、installer は package 配布、tests は `import aipairlib.*`＋subprocess standalone、run-all は package .py を compile。Python 3.8 互換維持（実 3.8 CI 緑）。稼働 relay は次回インストールで反映。
  - Codex レビュー派生（PR #36 `ead6edd` / #37 `4d91330` / #38 `de87064`・マージ済み）: installer の upgrade 安全性を段階的に強化 — (a) 旧フラット lib の退役を package 導入＋import 確認の後へ（chmod も明示 fail）、(b) 二相化で旧 entrypoint も温存、(c) **package を temp dir に stage→import 検証→同一 FS で atomic swap**（in-place 上書きを廃止し、壊れた upgrade でも既存 #7 install が無傷）。install-upgrade を claude/codex shim で CI 実走化し、旧 flat/legacy entrypoint 生存・live package 保持（sha256）まで検証（30→37 checks）。
- [x] #8/#9 CI を実 CLI の nightly smoke/E2E ＋ Python 3.8/tmux 3.1 の matrix に。実装完了（`upstream-latest-smoke`＝auth 不要の起動回帰・`authenticated-e2e` harness・3-lane matrix）。残っていた「認証付き E2E の実走検証」は **P1-5 と同一で、上記の方針決定（実機サブスク運用を根拠に非ゲート化）で解決**。
  - [x] **Python バージョン matrix**（PR 進行中）— `.github/workflows/ci.yml` を対応下限 3.8（installer 要件）と現行 3.13 の matrix（`fail-fast: false`・`actions/setup-python`）に。run-all が `python3` として起動する全経路（aipairlib パッケージの各モジュール・thin entrypoint・.py テスト）をその版で検証。3.8 互換は事前スキャン（3.9+ 機能不使用）＋ python3.9 実走で確認。README 反映。
  - [x] **tmux 3.1 lane**（PR 進行中）— `ci.yml` に matrix include `{py3.13, tmux 3.1}` を追加（distro は 3.4 なので **ソースビルド**）。`session-name.sh` は実 tmux 版を検出し **tmux<3.2 で `#{session_path}` 依存の採用/衝突ケース（[3]/[4]の一部/[6]/[9]の precondition）を skip**（3.1 は safe-miss＝[10] の模擬で被覆）。他テストは session_path/-f 不使用で 3.1 安全。実 3.2a では SP=1 で 55 checks 不変、SP=0 強制で skip 分岐が正常。
  - Codex レビュー派生（PR #29 マージ済み）: tmux 3.1 tarball を SHA-256 pin（実DLで一致確認・展開前に `sha256sum -c`・hash 未登録版は fail-closed）で検証、ソースビルド lane で `tmux -V`==`tmux <version>`（`grep -qx`）を assertion、README を 3-lane 表に更新。
  - [x] **実 CLI の nightly smoke**（PR #30 `0635f71`・手動 dispatch で検証済み）— `.github/workflows/nightly.yml`（schedule daily + workflow_dispatch）が npm で実 claude+codex を導入し installer smoke を実 CLI に対して実行。dispatch 実行で実 claude `2.1.197`/codex `0.149.0` 導入→`aipair` が実ペア起動→**3 pane 確認→撤去**を確認。ci.yml の mock では捕まえられない実 CLI 起動/版出力の回帰を検知する。
    - Codex レビュー派生（PR #32 `3852614`・再 dispatch で検証）: node を `lts/*`（現行 claude 2.1.240 は node>=22。旧は 2.1.197/node20 だった）に、aipair の実起動形 `claude --session-id <uuid> --version`＋`codex --version` の終了ステータス＋版出力を検証する step 追加（3 pane 存在だけでは pane 内 CLI のエラー終了を見逃す）、E2E は両 secret 揃わなければ skip・両方あって未実装なら fail。
  - [x] **認証付き round-trip E2E — harness 実装＋非ゲート opt-in 方針決定で解決**（社長判断 2026-08-23: API キー方式 CI E2E を必須ゲートにしない。**API キー認証パスは未実走のまま**／round-trip の検証は実機サブスク運用＝この `aipair loop` で成立、P1-5 参照）— `nightly.yml` の E2E step を、両 secret がある時に一時 dir で実ペアを起動→固有 nonce プロンプトを claude に注入→**full round-trip（下記3シグナル）を ~240s** タイムアウトで確認し、成否を問わず session を撤去（trap）する検証へ置換。fail-closed（1つでも欠ければ FAIL＋bridge/転写ダンプ）。tmux サーバは起動プロセスの env を継承するため smoke が残したサーバを `kill-server`→鍵入りで fresh 起動、`aipair loop` の detached セッションを `script`+`timeout` で起こして pane へ注入。`AIPAIR_STOP_SIDE=claude` を明示（codex の「完了です」で return hop が省略される偽失敗を防ぐ）。
    - 成功に必要な3シグナル（各々が実認証を要求）: (1) **claude が assistant ターンで nonce を完全一致で返した**（`<SID>.jsonl` 直読・`type==assistant` 本文結合の `strip()==nonce`／注入 user プロンプトや「token は X です」では不成立）、(2) **codex が実応答した**（codex rollout で最初の `relay-id` poke の `turn_id`＝`internal_chat_message_metadata_passthrough.turn_id` を特定し、同 turn の `task_complete.last_agent_message` が**非空**／空 task_complete での return poke による偽陽性を排除＝Codex e869c48f/8c192c1e/d16f34ca 対応）、(3) **relay が claude へ poke を返した**（claude rollout の `relay-id`／claude 初回は注入プロンプトで relay-id 無しのため一意）。
    - 検証（secret 無しで可能な範囲）: YAML パース・`bash -n`・skip/one-secret パス（exit 0）。**claude 完全一致 python を合成 JSONL 7 ケース**（完全一致/言及/接頭辞/空白/文字列/user のみ）＋**codex_replied python を合成 rollout 4 ケース**（last_agent_message 非空=真/空=偽/未完了=偽/cwd 不一致=偽）で単体テスト。実データ確証: return hop の `relay-id` を実 claude rollout（382 件）、codex_replied を稼働ペアの実 codex rollout（`last_agent_message` 非空を検出）で。`AIPAIR_STOP_SIDE=claude` の伝播は DRY_RUN で relay argv=`--stop-side claude` を実証（`relay.py:1317` の停止分岐を確認）。
    - **解決（非ゲート方針・社長判断 2026-08-23）**: API キー認証パスは opt-in の回帰用として残置し**未実走のまま**（クリーン runner 専用・API 予算＝管理者権限）。secrets を後で足せば `authenticated-e2e` が走るが必須ではない。round-trip の検証は実機サブスク運用（P1-5）で成立。
    - 実走試行（Codex 指示 relay-id:91631a4b）: `workflow_dispatch` を実行（https://github.com/inoutvillage/aipair/actions/runs/32602627712・head SHA は当時の main）→ nightly は success。**確定事実（run ログで誰でも検証可）**: 当該 run で `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` が**空**のため **E2E step が self-skip**（`skip: ... needs BOTH ... — not run.` を出力し exit 0）。
      - secret 列挙について（Codex P2 relay-id:7188eb38 訂正）: owner PAT での Secrets API は HTTP 200 / `total_count:0`（＝未設定）だが、**これは admin 権限トークン限定**で、非 admin/別アカウントのトークンでは 403 で列挙不可（Codex 環境で 403 だったのはこのため）。よって普遍的に検証可能なのは上記 self-skip のみで、「両 secret が空だった／一覧は権限依存」と理解するのが正確。
      - 3シグナルの認証パスは未実行のため**チェックせず**（Codex 条件「secret 不足時はチェックしない」に従う）。
    - **（旧「管理者アクション待ち」→非ゲート方針で解決済み）**: 本項目と親 #8/#9 は方針決定で `[x]`。将来 secrets＋API 予算を用意すれば opt-in の `authenticated-e2e` が走り 3シグナルを観測できるが、リリースの必須条件ではない。
  - Codex レビュー派生（PR #27 マージ済み）: sibling module 数え間違い是正（`aipair-*lib` は 5 本＋peer-log＝6 sibling modules。「relay + 6 lib + peer-log」の二重計上を統一）。
  - 運用: この間に稼働 relay が poke-to-me 配達失敗（claude 2.1.240 自己更新＋大量出力中の pane 状態）で停止 → 最新 main を再インストール（logicalParentUuid compaction 修正・parentUuid schema 検査を反映）して `aipair-relay-here` で再点火・復旧。
- [x] **#11 installer の global 注入 opt-out**（main 反映予定）— `--no-global-instructions`（＋env
  `AIPAIR_NO_GLOBAL_INSTRUCTIONS=1`）で `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md` への注入をスキップ
  （既存ブロックは非破壊）。usage/README/install-upgrade テスト追加。

  - Codex レビュー派生（マージ済み）: PR #16 `ab03cd3` opt-out を CI で検証する `tests/install-global-optout.sh`（claude/codex を `--version` 応答する shim にして global-instructions 分岐を実走）＋ installer の env bool を `bin/aipair` の `env_on` と同じ正規化（trim + lowercase）に統一。PR #17 `4069ff8` 両 install テスト（`install-global-optout.sh` / `install-upgrade.sh`）の installer smoke を **専用 private tmux socket（`-L`）** に隔離（実 tmux を一意 socket へ転送する wrapper を shim ディレクトリに追加し `#{socket_path}` を検証してから実行・後始末で kill-server + socket 削除）→ 既定/本番 server を一切触らない（`tasks/lessons.md` ガードレール）。両 CI 緑。
- [x] **#12 `SECURITY.md` / README に threat model** — 新規 `SECURITY.md`（信頼モデル・攻撃対象面6項目・スコープ外・安全な使い方・脆弱性報告＝GitHub private advisory・英語サマリ）＋ README「セキュリティ」節（目次込み・SECURITY.md へのポインタ＋主要警告）。
  - 攻撃対象面（実コードと照合済み・盛らない）: ①権限バイパス実行（`aipair loop` は `--unsafe` 必須・未指定は exit 2）②トランスクリプト読取（peer/relay が全履歴を読む・pin あり）③tmux キー注入（stop=最終メッセージ冒頭100字・版/schema/停止ゲート）④自律 git push ⑤グローバル指示注入（マーカー境界・`--no-global-instructions`）⑥テストの private `-L` socket ガードレール。
  - **`aipair-queue`（本番 migration deploy）は D2 で撤去済み**（shipped FILES/README に無し）のため threat model から除外（存在しない機能を書かない）。
  - Codex レビュー派生（PR #22-#25・マージ済み）: クロスプロバイダー境界の明記（peer/relay 出力が相手クラウドへ＝Claude→OpenAI/Codex→Anthropic）＋private vulnerability reporting を API 有効化＋テンプレの push/PII 誤帰属修正（#22）／§6 のテスト隔離記述を正確化＋非公開 `tasks/lessons.md` 参照を一掃（#23）／SKILL の権限モードを安全既定に是正＋`launch-cmds`・`env-forward` に socket_path preflight（#24）／`AIPAIR_*_FLAGS` の『空でフラグ無し』を対話起動限定に是正（loop は危険フラグ除去不能・追記）（#25）。
