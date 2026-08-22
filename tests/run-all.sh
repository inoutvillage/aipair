#!/usr/bin/env bash
# Run every check aipair has: syntax (bash -n, python compile), shellcheck when installed,
# and all tests under tests/. Exit 1 if anything failed; skips are printed, never silent.
#   bash tests/run-all.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"; cd "$REPO" || exit 1
fail=0; results=()
note() { results+=("$1"); echo "$1"; }
step() { printf '\n=== %s ===\n' "$1"; }

# Which files get which check is decided by their shebang, not by a hand-kept list, so a
# new script cannot be forgotten. Anything in these locations that is not recognisably
# bash or python3 fails the run instead of being skipped silently.
SH=(); PY=()
step "classify scripts by shebang"
for f in aipair-install.sh bin/* tests/*; do
  [ -f "$f" ] || continue
  shebang="$(head -n1 "$f")"
  case "$shebang" in
    '#!/usr/bin/env bash'|'#!/bin/bash')       SH+=("$f"); echo "bash    $f" ;;
    '#!/usr/bin/env python3'|'#!/usr/bin/python3') PY+=("$f"); echo "python3 $f" ;;
    *) note "FAIL unclassified script (unknown shebang '$shebang'): $f"; fail=1 ;;
  esac
done
for must in bin/peer bin/aipair bin/aipair-relay-here aipair-install.sh tests/run-all.sh; do
  printf '%s\n' "${SH[@]}" | grep -qx "$must" || { note "FAIL $must missing from the bash set"; fail=1; }
done
for must in bin/peer-log bin/aipair-relay; do
  printf '%s\n' "${PY[@]}" | grep -qx "$must" || { note "FAIL $must missing from the python set"; fail=1; }
done

step "bash -n"
for f in "${SH[@]}"; do if bash -n "$f"; then echo "ok   $f"; else note "FAIL bash -n $f"; fail=1; fi; done

step "python compile"
for f in "${PY[@]}"; do
  if python3 -c 'import sys; compile(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1], "exec")' "$f"; then echo "ok   $f"; else note "FAIL compile $f"; fail=1; fi
done

step "shellcheck"
if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck -S warning "${SH[@]}"; then echo "ok   shellcheck -S warning (${#SH[@]} files)"; else note "FAIL shellcheck"; fail=1; fi
else
  note "skip shellcheck (not installed; CI runs it)"
fi

step "tests"
for t in tests/*.sh tests/*.py; do
  [ "$t" = tests/run-all.sh ] && continue          # the runner itself: checked above, not re-run
  case "$t" in *.sh) runner=bash ;; *) runner=python3 ;; esac
  log="$(mktemp "${TMPDIR:-/tmp}/aipair-test-log.XXXXXX")"
  if "$runner" "$t" >"$log" 2>&1; then
    note "ok   $t — $(grep -E '^(Ran [0-9]+ tests|[0-9]+ checks)' "$log" | tail -1)"
  else
    note "FAIL $t"; fail=1; sed 's/^/      /' "$log" | tail -40
  fi
  rm -f "$log"
done

printf '\n=== summary ===\n'; printf '%s\n' "${results[@]}"
[ "$fail" = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$fail"
