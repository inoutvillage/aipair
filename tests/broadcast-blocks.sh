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
# point at the README, not tasks/lessons.md, since a repo path is meaningless once the block
# is installed into a user's global config; only the in-repo skill cites the lessons log.)
SITES="templates/claude-md-block.md templates/codex-agents-block.md .claude/skills/aipair-setup/SKILL.md"
for f in $SITES; do
  chk "grep -qF 'kill-server' '$f'"                    "$f warns about kill-server"
  chk "grep -qE 'tmux -L|tmux .*-S|[-]L <|[-]S <' '$f'" "$f names a private server (-L / -S)"
  chk "grep -qF 'TMUX_TMPDIR' '$f'"                    "$f explains the \$TMUX / TMUX_TMPDIR trap"
done
chk "grep -qF 'tasks/lessons.md' .claude/skills/aipair-setup/SKILL.md" "setup skill points at the lessons log"
for f in templates/claude-md-block.md templates/codex-agents-block.md; do
  chk "[ \"\$(grep -c 'aipair:start' '$f')\" = 1 ]" "$f has exactly one start marker"
  chk "[ \"\$(grep -c 'aipair:end' '$f')\" = 1 ]"   "$f has exactly one end marker"
done

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
