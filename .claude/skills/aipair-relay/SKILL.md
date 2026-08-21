---
name: aipair-relay
description: "aipair セッション内で、Codex との自律レビュー往復（relay）をオンデマンドで1本起こす。relay は「完了です」等で終了して往復が止まるので、新しい仕事ができた時に再点火する。起動トリガー例『リレーで処理して』『relay で回して』『Codex にレビューさせて』『レビュー往復を起こして』『もう一周レビュー回して』。前提: 実行中の aipair ペア（tmux）の Claude ペイン内であること。ペア外の通常セッションでは使えない。"
---

# aipair-relay（レビュー往復のオンデマンド点火）

`aipair` は Claude + Codex を tmux で並走させ、`aipair-relay` が両者のペインを交互に poke して
自律的にレビューし合わせる。relay は停止フレーズ（既定「完了です」）や上限ラウンドで**終了する**＝
そこで往復が止まる。このスキルは、**新しい仕事ができた時に relay を1本だけ起こし直す**ためのもの。

## いつ使うか（オンデマンド。自動再起動ではない）

- **ユーザーが「リレーで処理して」「relay で回して」等**と言った（＝③のトリガー）。
- **自分（Claude）がレビュー往復を回す価値があると判断した**（＝②のトリガー。例: 大きめの実装を終え、Codex の批判的レビューを一周かけたい）。
- **前の relay が終了した後**、次の仕事について改めて往復したい（＝①。前 relay が終わっているのが前提）。

> 🚫 これは「終わったら勝手に次を回す」常駐ではない。**1回叩けば relay 1本**。無限ループにしないための設計（maintainer decision）。「完了です」で終わった直後に無条件で再点火すると、Codex がまた即「完了です」→ 延々ループになるため、**新しい入力がある時だけ**起こす。

## 前提チェック（最初に必ず）

- `echo "${TMUX:-なし} / self=${AI_SELF:-なし} / peer=${AI_PEER:-なし}"` を見る。
- `TMUX` が空、または aipair ペアの外 → **このスキルは使えない**。ユーザーに「aipair ペア内の Claude ペインでのみ有効です」と伝えて終了する。ペアの起動自体は人間が `aipair loop <dir>` で行う（このスキルは起動しない）。

## 実行

共通コマンド **`aipair-relay-here`** を呼ぶ（Codex も同じコマンドを自分のペインから直接使える）。

```bash
# まずドライランで、検出した bridge ペインと組み立てたコマンドを確認する（推奨）
aipair-relay-here --print [rounds N] [stop "フレーズ"] [stop-side codex|claude|both]

# 問題なければ本番（--print を外す）
aipair-relay-here [rounds N] [stop "フレーズ"] [stop-side codex|claude|both]
```

- 引数なし = `aipair-relay --adopt`（既存ペアに乗る）＋ relay 既定（stop=完了です / stop-side=codex / max-rounds=20）。
  ただし `AIPAIR_*` が環境にあればそれが既定になる（**優先順位: 引数 > env > 既定**）。
  何が実際に渡るかは必ず `--print` で確認する（env が効いていると見た目の引数と違う）。
- 自然言語 → 引数の対応:
  - 「10ラウンドで」 → `rounds 10`
  - 「私が OK と言ったら止めて」 → `stop OK stop-side claude`
  - 「Codex が納得したら終わり」 → 既定（stop-side codex）でよい
- relay は **bridge ペイン**で走り出す。呼んだ側のターンはすぐ戻る（ブロックしない）。
- **二重起動はガードされる**: bridge が busy（relay 走行中 / peer-log watch 中）なら exit 2 で中止する。その旨をユーザーに伝える。

## 連続モード（endless・2026-08-16 追加）

「全部終わるまで回して」「タスクリストが尽きるまで自走して」と**明示的に頼まれた時だけ**使う。

```bash
aipair-relay-here --print -- --endless --max-rounds 100     # まずドライラン
aipair-relay-here -- --endless --max-rounds 100
```

- 「完了です」は**終了ではなく「レビュー合格→次のタスクへ」**の合図になる。
- Claude 側の手持ちが尽きたら本文冒頭に「**次のタスクをください**」と書く → relay が Codex に
  `tasks/todo.md` の未チェック項目から次の1件を指示させる。
- **終端は Codex の「全タスク完了」宣言のみ**（+ `--max-rounds` の安全キャップ）。
  既定の 20 往復ではすぐキャップに当たるので `--max-rounds` を大きめに。
- 上の 🚫（無限ループにしない）は**既定モードの話**。連続モードはユーザーが明示的に選んだ時だけで、
  こちらから勝手に `--endless` を付けない。

## 点火後の振る舞い（重要）

- relay を起こしたら、**自分（Claude）はそのターンを終える**。以降は relay が「Codex にレビューさせる → その指摘を Claude に返す」を駆動する。点火後に自分で作業を続けると、relay の poke と衝突する。
- 会話の流れは `peer`（相手の最新）/ `peer-log both`（両者マージ）で追える。
- 止めたい時は bridge ペインで Ctrl-C（または relay が停止フレーズを検知して自然終了）。

## 失敗時

- `aipair-relay-here` が exit≠0 を返したら、その stderr（tmux外 / bridge が busy / bridge 未検出 等）を**そのままユーザーに伝えて停止**する。勝手に別手段で relay を起こそうとしない。
