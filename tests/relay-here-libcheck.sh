#!/usr/bin/env bash
# aipair-relay-here must refuse to fire a relay whose sibling libs are not all present
# (D3 A6): --help imports them, so a missing lib fails the load check before anything else.
#   bash tests/relay-here-libcheck.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -P)"; REPO="$(dirname "$HERE")"
command -v tmux >/dev/null 2>&1 || { echo "skip (no tmux)"; exit 0; }
W="$(mktemp -d "${TMPDIR:-/tmp}/aipair-lc.XXXXXX")"; trap 'rm -rf "$W"' EXIT
fail=0; n=0
chk() { n=$((n+1)); if eval "$1"; then echo "ok   $2"; else echo "FAIL $2"; fail=1; fi; }

ALL=(aipair-relay peer-log aipair-corelib aipair-loglib aipair-tmuxlib aipair-deliverylib aipair-dialoglib)

# complete set → relay loads → the lib check passes (relay-here then dies later on 'no session',
# which is a DIFFERENT failure, proving it got past the load gate)
mkdir -p "$W/full"; for f in "${ALL[@]}"; do cp "$REPO/bin/$f" "$W/full/"; done; chmod +x "$W/full/"*
out="$(env -u TMUX AIPAIR_RELAY_BIN="$W/full/aipair-relay" bash "$REPO/bin/aipair-relay-here" --session none 2>&1)" || true
echo "$out" | grep -q "ロードできない" && loaderr=1 || loaderr=0
chk "[ $loaderr -eq 0 ]" "complete install passes the lib-load gate"

# missing one lib (tmuxlib) → relay import fails → relay-here dies at the load gate
mkdir -p "$W/partial"; for f in "${ALL[@]}"; do [ "$f" = aipair-tmuxlib ] || cp "$REPO/bin/$f" "$W/partial/"; done; chmod +x "$W/partial/"*
rc=0; out="$(env -u TMUX AIPAIR_RELAY_BIN="$W/partial/aipair-relay" bash "$REPO/bin/aipair-relay-here" --session none 2>&1)" || rc=$?
chk "[ $rc -ne 0 ]" "missing lib → relay-here exits non-zero (got $rc)"
echo "$out" | grep -q "ロードできない" && loaderr2=1 || loaderr2=0
chk "[ $loaderr2 -eq 1 ]" "missing lib → reports the load failure, not a generic error"

echo; echo "$n checks, $([ $fail = 0 ] && echo ALL PASSED || echo SOME FAILED)"
exit $fail
