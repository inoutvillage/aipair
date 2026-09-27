"""aipair autopilot_flow — 完全自走モード（--autopilot）の《収束の段》判定と質問回答の照合（純関数）。

社長指示 2026-09-25: 人間の判断待ちで relay を止めず、Codex が人間の代理として答えて完成まで走り切る。
プランレビュー／質問リレー／レビュー停滞は「上限で停止」せず、段階的に《ループにならない答え方》へ寄せ、
最後の段では relay 自身が必ず前へ進める（プランは付帯コメント付き承認・質問は安全側の固定回答・
停滞は強制合格）。ここは段の判定と回答の問番号照合だけを持つ純関数群で、poke / press / exit は
state_machine（StateMachine.run）側が適用する（plan_flow / question_flow と同じ流儀）。

段（stage）:
  normal   従来どおりの依頼
  converge 収束を促す依頼（致命的でなければ承認／全問を決め切る 等）
  force    relay が内容にかかわらず前へ進める
"""
import re
import unicodedata

from .plan_flow import PlanDecision


def stage(n, limit):
    """n = 今回が何回目か（1 始まり）、limit = 通常段の回数（--plan-rounds / --question-rounds）。
    1..limit → normal、limit+1 → converge、それ以降 → force。"""
    if n <= limit:
        return "normal"
    if n == limit + 1:
        return "converge"
    return "force"


def stall_action(streak, limit):
    """レビュー停滞（advance_stall の回数 streak が limit 以上）の時の段。
      break   Codex に膠着打破（具体的な差分指示か、許容して合格か）を依頼
      forward その返答を Claude へ通常どおり渡す（打破の指示を実行させる）
      final   Codex に合格 sentinel を出すよう最終依頼
      force   relay が合格として扱う"""
    s = streak - limit
    if s < 0:
        return "forward"
    return {0: "break", 1: "forward", 2: "final"}.get(s, "force")


FORCE_PLAN_NOTE = ("【autopilot】プランレビューが収束しないため relay が承認します。"
                   "以下は Codex の残る指摘です。実装の中で反映してください:\n")


def force_plan_decision(decision, dialog):
    """force 段: Codex の返答が修正要求でも承認へ倒す（修正要求の本文は付帯コメントとして添える）。
    返答が空（no_text）でも、今ダイアログが画面にあり承認肢があれば承認する — 空の返答が続くと
    プランレビューの再依頼が際限なく続くため（Codex レビュー 2026-09-25 P1）。
    承認系・no_dialog はそのまま（画面に無いダイアログは操作しない不変条件を崩さない）。"""
    if decision.action == "changes":
        return PlanDecision("approve_feedback", FORCE_PLAN_NOTE + decision.payload)
    if decision.action in ("no_tell_option", "no_text") and dialog and dialog.get("yes"):
        return PlanDecision("approve", None)
    return decision


def missing_answers(text, n):
    """質問 n 問に対する回答本文から、「N問目」の記載が無い問番号を返す（n<=1 は照合しない）。
    全角数字も拾う（NFKC）。「11問目」を「1問目」と誤認しないよう直前の数字を否定先読みで除く。"""
    if n <= 1:
        return []
    t = unicodedata.normalize("NFKC", text or "")
    return [i for i in range(1, n + 1) if not re.search(r"(?<!\d)%d\s*問目" % i, t)]


# 回答に欠番が残ったまま配達する時の注記・autopilot の回答末尾・force 段の固定回答で共通に使う
# 《安全側の選び方》。不可逆な選択肢を機械的に選ばない（Codex レビュー 2026-09-25）。
# 秘密情報（API キー・パスワード・トークン・認証情報）は捏造も入力もしない（Codex レビュー 2026-09-25 P1）。
SECRET_RULE = ("API キー・パスワード・トークン等の秘密情報や認証情報は、推測・捏造・入力をしないでください。"
               "それが無いと進められない作業は、秘密を要しない選択肢（スキップ・後回し・モックで代替）を選び、"
               "その作業は保留として残して次へ進んでください。")

SAFE_CHOICE_RULE = ("元に戻せる安全な選択肢を優先し、本番デプロイ・課金・公開 push・データ削除など"
                    "取り返しのつかない操作を含む選択肢は選ばないでください（全ての選択肢が不可逆なら、"
                    "実行しない・dry-run・ステージングで止める案を選ぶ）。" + SECRET_RULE +
                    "それ以外は (Recommended) の選択肢、無ければ 1 番で決めてください。")


def missing_note(missing):
    nums = "・".join("%d問目" % i for i in missing)
    return ("\n\n（relay 注記: %s への回答がありません。その問は次の方針で自分で決めて進めてください: %s）"
            % (nums, SAFE_CHOICE_RULE))


AUTOPILOT_ANSWER_FOOTER = ("\n\n（autopilot: 人間は不在です。以後は人間の確認を待たず、判断が要る点は"
                           "自分で決めて作業を最後まで進めてください。）")

FORCE_QUESTION_ANSWER = ("【autopilot】質問が続いているため relay が代わりに回答します。各質問は次の方針で"
                         "決めてください: " + SAFE_CHOICE_RULE + " 人間は不在です。以後は質問せず、"
                         "自分で判断して作業を最後まで進めてください。")
