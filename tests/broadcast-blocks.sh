#!/usr/bin/env bash
# The tmux guardrail (2026-08-21 incident) must stay in every place a fresh install
# broadcasts it, and the marker-delimited blocks must remain well-formed (the installer
# refuses a block with anything but exactly one start/end marker).
#   bash tests/broadcast-blocks.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"; cd "$REPO" || exit 1
fail=0; n=0
chk() { n=$((n+1)); if eval "$1"; then echo "ok   $2"; else echo "FAIL $2"; fail=1; fi; }

# The guardrail must survive in EVERY place a fresh install broadcasts it: the two marker
# blocks and the setup skill. Each must warn about un-targeted kill-server, name a private
# server (-L / -S), and explain the $TMUX / TMUX_TMPDIR trap — that combination is the whole
# lesson; any one of them going missing brings the 2026-08-21 incident back. (The blocks
# point at the README, and the skill at the public SECURITY.md, since tasks/lessons.md is not
# a tracked/public file — a reference to it is meaningless once installed or cloned.)
SITES="templates/claude-md-block.md templates/codex-agents-block.md .claude/skills/aipair-setup/SKILL.md"
for f in $SITES; do
  chk "grep -qF 'kill-server' '$f'"                    "$f warns about kill-server"
  chk "grep -qE 'tmux -L|tmux .*-S|[-]L <|[-]S <' '$f'" "$f names a private server (-L / -S)"
  chk "grep -qF 'TMUX_TMPDIR' '$f'"                    "$f explains the \$TMUX / TMUX_TMPDIR trap"
done
chk "grep -qF 'SECURITY.md' .claude/skills/aipair-setup/SKILL.md" "setup skill points at the public SECURITY.md (tasks/lessons.md is not tracked)"
for f in templates/claude-md-block.md templates/codex-agents-block.md; do
  chk "[ \"\$(grep -c 'aipair:start' '$f')\" = 1 ]" "$f has exactly one start marker"
  chk "[ \"\$(grep -c 'aipair:end' '$f')\" = 1 ]"   "$f has exactly one end marker"
done

# D1: no launch path is unsafe-by-default. The VS Code single-launch tasks must NOT hardcode
# the permission-bypass flags (the relay-loop task opts in explicitly via `aipair loop --unsafe`).
chk "! grep -q 'claude --dangerously-skip-permissions' templates/vscode-tasks.json" "vscode: claude single-launch is not bypass-by-default"
chk "! grep -q 'codex --dangerously-bypass-approvals-and-sandbox' templates/vscode-tasks.json" "vscode: codex single-launch is not bypass-by-default"
chk "grep -q 'aipair loop --unsafe' templates/vscode-tasks.json" "vscode: the loop task opts into --unsafe explicitly"

# endless 新契約（社長指示 2026-08-24 §11 Phase 6）: 旧契約「終端は ALL_DONE のみ」を配布ドキュメントへ
# 再発させない＋新契約（HUMAN_REQUIRED）が存在すること。ドキュメントが実装から drift しないよう固定する。
for f in templates/claude-md-block.md .claude/skills/aipair-relay/SKILL.md .claude/skills/aipair-setup/SKILL.md; do
  chk "! grep -qE '終端は Codex の.*宣言(のみ|だけ)|ends \*\*only\*\* when|終端 \[AIPAIR_ALL_DONE\]|全タスク完了」宣言のみ' '$f'" "$f: 旧契約「ALL_DONE のみ」が再発していない"
  # 新契約の要素を全て固定（1 語 HUMAN_REQUIRED だけでは、分類/[!]/exit 8 の欠落を検出できない）
  chk "grep -qF 'HUMAN_REQUIRED' '$f'"                          "$f: HUMAN_REQUIRED を記載"
  chk "grep -qF 'exit 8' '$f'"                                 "$f: exit 8 を記載"
  chk "grep -qF '[!]' '$f'"                                    "$f: 保留記法 [!] を記載"
  chk "grep -qE 'READY.*BLOCKED.*ALL_DONE' '$f'"               "$f: 3 状態 READY/BLOCKED/ALL_DONE を明示（「分類」1語では不十分）"
done

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
