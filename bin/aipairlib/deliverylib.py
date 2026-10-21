"""aipair-deliverylib — poke delivery + Enter submission extracted from aipair-relay
(D3 relay 分割・案A 増分4 = A4). No import of relay (no cycle back to it). Its tmux helpers, logging and the dialog probe
come from sibling package modules (normal imports; the delivery<->dialog cycle uses a module
import + call-time reference). The idle budget is an explicit `busy_wait` argument to poke().
Covered by tests/relay-parsers.py.
"""
import os
import subprocess
import sys
import time

from .logs import dim
from .tmuxlib import tmux, cancel_copy_mode, pane_busy
from . import dialoglib   # delivery <-> dialog cycle: import the module, use dialoglib.X at call-time


def submit_enter(pane, confirm=None, badge=True):
    """Enter を送り、送信の成立を検証。確認できなければ最大3回再打鍵する。

    長文投入直後の Enter は TUI の取り込みバースト（改行扱い）や copy-mode に
    食われて未送信のまま残ることが恒常的にある（2026-08-14 実測: クリーンな
    コンポーザでも1回目は食われた）。検証の source of truth は confirm()（呼び出し元が
    LogWatch で作る「ログへの追記」判定）。badge=True なら実行中バッジ
    「esc to interrupt」の画面一致も成功とみなす — これは **Codex ペイン専用**
    （Codex は実行中ずっと表示）。Claude Code は実行中でもバッジを出さず、逆に
    transcript 中の同語句で偽陽性になる（2026-08-16 実測）ので Claude 宛は badge=False。
    pane_busy の画面差分は使わない（食われた Enter が起こすコンポーザ再描画すら
    「送信成功」に見える）。偽陰性は無害: 再打鍵 Enter は空コンポーザで no-op、
    ダイアログ表示中は直前ガードで中止する。"""
    for retry in range(3):
        if retry and dialoglib.dialog_on_screen(pane):
            dim("再打鍵前にダイアログ表示を検知 → 送信済み（高速ターン）とみなし Enter を中止")
            return True
        cancel_copy_mode(pane)
        tmux("send-keys", "-t", pane, "Enter")
        verify_until = time.time() + 7
        while time.time() < verify_until:
            if confirm is not None and confirm():
                if retry:
                    dim(f"Enter 再打鍵 {retry + 1} 回目で送信を確認（ログ追記）")
                return True
            if badge:
                try:
                    cap = tmux("capture-pane", "-p", "-t", pane, capture=True).stdout
                except subprocess.CalledProcessError:
                    cap = ""
                if "esc to interrupt" in cap.lower():
                    if retry:
                        dim(f"Enter 再打鍵 {retry + 1} 回目で送信を確認（実行中バッジ）")
                    return True
            time.sleep(0.5)
        dim(f"Enter が効いていない（送信を確認できず）→ 再打鍵 {retry + 1}/3")
    sys.stdout.write("\a")
    sys.stdout.flush()
    dim(f"Enter を3回送っても送信を確認できず（pane {pane}）— コンポーザを確認し手動で送信してください")
    return False


def poke(pane, text, confirm=None, badge=True, busy_wait=90):
    """Inject text into a pane the way the agent TUIs expect (本文はブラケットペースト、確認用の
    nonce だけリテラル入力、そして Enter)．
    Verifies the text actually reached the composer before pressing Enter;
    if delivery can't be confirmed, never presses Enter (a blind Enter submits
    empty/partial input and strands the loop).
    確認は呼び出しごとのnonce(文末付与)で行う — 固定文面の先頭をprobeにすると、
    画面の過去ログに残った同一依頼文でfalse positiveになるため。"""
    # 相手が実行中(streaming)だと入力エコーが不安定で配達確認に失敗しやすいので、
    # 短時間だけアイドルを待つ（礼儀としての待機）。ただし待機はハードゲートに
    # しない: アイドル画面にも常時その場アニメする行があり pane_busy の偽busyは
    # 完全に潰せず、「上限満了→配達不可→relay死亡」が2度実発生した（2026-08-14/15、
    # 全一致比較でも差分3行閾値でも再発）。今の配達系は nonce 検証・Enter 送信検証・
    # 非破壊キーのみで自衛できるため、待ち切れなければ注入を続行する — 実行中の
    # TUI へ投入されたテキストはコンポーザに保持され、Enter でキュー投入されて
    # ターン終了後に通常のメッセージとして届く（Claude Code はこのセッションで
    # キュー配達を実測済み。失敗しても配達検証が3回試行→残置で loud stop する）。
    waited = 0
    while pane_busy(pane) and waited < busy_wait:
        if waited == 0:
            dim(f"相手ペイン({pane})が実行中 → 最大{busy_wait}秒だけアイドルを待つ")
        time.sleep(5)
        waited += 5
    if waited >= busy_wait and pane_busy(pane):
        dim(f"相手ペイン({pane})が{waited}秒経過後も実行中 → 配達検証に任せて注入を続行（キュー投入）")
    elif waited:
        dim(f"アイドル確認（{waited}s 待機）→ 注入開始")
    probe = "relay-id:" + os.urandom(4).hex()
    body = text
    delivered = False
    shown = ""
    for attempt in range(3):
        cancel_copy_mode(pane)
        if attempt:
            # 再試行前は、宙ぶらりんの bracketed paste を閉じるだけ（ESC[201~ 単発）。
            # 未終端ペースト（貼り付け事故等）が開いたままだと C-u もテキストも
            # Enter も全部ペーストバッファへ吸われ、何も画面に出ないまま失敗し続ける
            # （2026-08-14 実バグ: [Pasted Content] チップ残留で poke が空振り）。
            # 終端記号はペーストが開いていなければ TUI に無視され、開いていれば
            # バッファ内容がコンポーザへ文として現れるだけ＝何も消えない。
            # C-u による掃除は一切しない: チップは人間の貼り付け下書きでも表示され
            # relay 自身の残骸と確実に区別できない（Codex レビュー指摘）。既存内容が
            # 残っていても poke は末尾に追記され一緒に送信されるだけで transcript に
            # 残り失われない。3回失敗なら入力欄を保持したまま停止して人に委ねる。
            tmux("send-keys", "-t", pane, "-H",
                 "1b", "5b", "32", "30", "31", "7e", check=False)
            time.sleep(0.3)
        # 本文は《ブラケットペースト》で投入する。長文を send-keys -l で打つと、TUI の取り込み
        # バーストに後続の Enter が吸収され、本文がコンポーザに残ったまま送信されない（2026-09-14
        # 実測: 幅 57 のペインへ ~1,900 字の質問リレー本文を打つと再現。codex 0.153.4 / 0.154.0 とも。
        # 同じ長さでもブラケットペーストなら通常の Enter で即送信できる）。画面で確認する nonce だけ
        # 短いリテラル入力として足す — 狭い画面でも `[Pasted Content … chars] relay-id:xxxx` と見える。
        paste_text(pane, body)
        time.sleep(0.2)
        tmux("send-keys", "-t", pane, "-l", " " + probe)
        # 長文は TUI の取り込み・再描画が数秒続き、固定 0.3s の一発チェックでは
        # nonce がまだ描画されていない（2026-08-14 実測）。出現をポーリングで待つ。
        seen_deadline = time.time() + 3.5
        while True:
            time.sleep(0.4)
            shown = "".join(tmux("capture-pane", "-p", "-t", pane,
                                 capture=True).stdout.split())
            if probe in shown:
                delivered = True
                break
            if time.time() >= seen_deadline:
                break
        if delivered:
            break
        # 配達の成立条件は《nonce が画面に出たこと》だけ。本文ペーストと nonce 入力は別コマンドなので
        # 「本文だけ届き nonce が届かない」が起こりうるが、以前の《本文冒頭が見えたら配達とみなす》
        # フォールバックはそれを成功扱いにし、届いていない nonce で Enter を押して arm してしまう
        # （Codex レビュー 2026-09-14。Codex 宛は実行中バッジで submit_enter まで成功扱いになる）。
        # ブラケットペーストでは nonce が末尾＝カーソル位置に出るため、切詰め表示でも見える
        # （実測: コンポーザには `[Pasted Content … chars] relay-id:xxxx`）。
        dim(f"poke が画面に届いていない（copy-mode・未終端ペースト等）→ 再試行 {attempt + 1}/3")
        time.sleep(0.5)
    if not delivered:
        # 掃除（C-u）はしない: 入力欄に人間の下書きが混ざっている可能性があり、
        # 消すと復元できない（Codex レビュー指摘）。残したまま人間に委ねる。
        sys.stdout.write("\a")
        sys.stdout.flush()
        dim(f"poke 配達確認に失敗（pane {pane}）— 入力欄は残したまま中断。内容を確認し手動で送信/削除してください")
        return False
    # --- Enter（送信）--------------------------------------------------------
    # 長文 poke（質問リレー等）は send-keys 完了後も TUI 側の取り込み・再描画が
    # 続いており、そこへ Enter を撃つと入力バーストの一部（改行）として吸収され、
    # 本文がコンポーザに残ったまま送信されない（2026-08-14 実バグ: 質問リレーで
    # Codex 未送信のまま relay が回答待ちで停滞。短文の通常 poke は取り込みが
    # 一瞬のため露見しなかった）。画面静止＝取り込み完了を待ってから Enter を送り、
    # ターン開始（busy）で送信を検証。確認できなければ copy-mode 解除して再打鍵
    # （人間のスクロールで copy-mode に食われた Enter もこれで自己回復する）。
    stable = shown
    settle_deadline = time.time() + 15
    while time.time() < settle_deadline:
        time.sleep(0.6)
        try:
            cap = "".join(tmux("capture-pane", "-p", "-t", pane,
                               capture=True).stdout.split())
        except subprocess.CalledProcessError:
            break
        if cap == stable:
            break
        stable = cap
    # 成功時は nonce を返す（呼び出し元がログでの配達確認・応答帰属に使う）。
    # confirm(probe) は呼び出し元が LogWatch で作る「nonce がログに追記されたか」。
    # badge（画面の実行中バッジ）は Codex 宛のみ有効 — 相手が既に実行中だと Enter 前
    # から出ていて素通しになるため速報にすぎず、その場合の最終確認は応答帰属
    # （response_done）と no-show 監視（poke_noshow）が担う。
    ok = submit_enter(pane, confirm=(lambda: confirm(probe)) if confirm else None, badge=badge)
    return probe if ok else None


def press(pane, key):
    cancel_copy_mode(pane)
    tmux("send-keys", "-t", pane, key)


def paste_text(pane, text):
    """Bracketed paste: multi-line review text does not submit early, AND a long body does not swallow
    the Enter that follows it (2026-09-14 実測: 幅 57 のペインへ ~1,900 字を send-keys -l で打つと、
    後続の Enter が TUI の取り込みバーストに吸収されて未送信のまま残る。同じ長さでもブラケット
    ペーストなら通常の Enter で即送信できる — codex 0.153.4 / 0.154.0 の両方で確認)。

    バッファ名は呼び出しごとに一意にする: 固定名だと、同時に走る別 relay の貼り付けを上書きしうる
    （複数ペアを並行運用するのが前提のツールなので実際に起こりうる）。貼り付け後は -d で削除する。"""
    cancel_copy_mode(pane)
    buf = "aipair-relay-%d-%s" % (os.getpid(), os.urandom(3).hex())
    tmux("set-buffer", "-b", buf, text)
    try:
        tmux("paste-buffer", "-p", "-d", "-b", buf, "-t", pane)
    finally:
        # `paste-buffer -d` が消すのは《貼り付けが成功した時だけ》。ペイン消失等で失敗すると本文入りの
        # バッファが残り、名前が一意なぶん失敗のたびに溜まる（Codex レビュー 2026-09-14 実測）。
        # 削除は best-effort（check=False）で、元の例外を隠さない。capture=True で tmux のエラー出力を
        # 画面へ漏らさない（既に消えている時の "no buffer" は想定内）。
        tmux("delete-buffer", "-b", buf, check=False, capture=True)
