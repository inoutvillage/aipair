#!/usr/bin/env bash
#
# aipair-install.sh — install the aipair toolset (Claude Code × Codex CLI pair runner)
#
# Usage:
#   ./aipair-install.sh                      install (never runs sudo; tmux must already be present)
#   ./aipair-install.sh --install-tmux       opt-in: install tmux with the system package manager
#                                            (as-is when root / brew, otherwise via sudo)
#   ./aipair-install.sh --check              diagnose only, print KEY=VALUE lines, change nothing
#   ./aipair-install.sh --vscode-tasks DIR   also copy templates/vscode-tasks.json to DIR/.vscode/tasks.json
#                                            (never overwrites an existing tasks.json)
#   ./aipair-install.sh --help
#
# Exit codes:
#   0  success (warnings allowed)
#   1  failure (reason on stderr)
#   2  usage error
#   3  a required dependency is missing or too old (instructions were printed; nothing installed)
#      --check also returns 3 when a dependency is missing, after printing the full report
#
# Every step prints exactly one line tagged [ok] / [skip] / [warn] / [fail]. Nothing is skipped silently.
#
# Notes:
#   - Install dir is fixed to ~/.local/bin: aipair-relay-here resolves $HOME/.local/bin/aipair-relay,
#     and aipair-relay / peer-log / aipair-corelib / aipair-loglib / aipair-tmuxlib / aipair-deliverylib / aipair-dialoglib must live in the same directory (they import each other
#     by path). Files are copied, never symlinked.
#   - Notice blocks for ~/.claude/CLAUDE.md and ~/.codex/AGENTS.md are delimited by
#     <!-- aipair:start --> / <!-- aipair:end -->. Re-running replaces the block in place; it never
#     appends twice. The file is backed up first and the result is verified byte-for-byte
#     (file minus block == old file minus block) — on mismatch the backup is restored and this fails.
#   - Testing seam: AIPAIR_HOME overrides the home directory used for install targets (default $HOME).
#     Only for tests; the tools themselves still expect ~/.local/bin.
set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd -P)"
AH="${AIPAIR_HOME:-$HOME}"
BIN_DIR="$AH/.local/bin"
SKILLS_DIR="$AH/.claude/skills"
CLAUDE_MD="$AH/.claude/CLAUDE.md"
CODEX_AGENTS="$AH/.codex/AGENTS.md"
TMUX_MIN="3.1"; TMUX_MIN_MAJOR=3; TMUX_MIN_MINOR=1
PY_MIN="3.8";   PY_MIN_MAJOR=3;   PY_MIN_MINOR=8
FILES=(aipair aipair-relay aipair-relay-here peer peer-log aipair-corelib aipair-loglib aipair-tmuxlib aipair-deliverylib aipair-dialoglib)
SKILLS=(aipair-setup aipair-relay)
MARK_START='<!-- aipair:start -->'
MARK_END='<!-- aipair:end -->'
TS="$(date +%Y%m%d-%H%M%S)"

N_OK=0; N_SKIP=0; N_WARN=0; N_FAIL=0
ok()   { N_OK=$((N_OK+1));     printf '[ok]   %s\n' "$*"; }
skip() { N_SKIP=$((N_SKIP+1)); printf '[skip] %s\n' "$*"; }
warn() { N_WARN=$((N_WARN+1)); printf '[warn] %s\n' "$*"; }
fail() { N_FAIL=$((N_FAIL+1)); printf '[fail] %s\n' "$*" >&2; }
note() { printf '       %s\n' "$*"; }
usage() {
  # print the leading comment block from 'aipair-install.sh —' down to the blank
  # comment line that closes the Exit-codes section (so 0/1/2/3 all show); strip '# '.
  awk '
    /^# aipair-install\.sh —/ { on=1 }
    on {
      if ($0 !~ /^#/) exit
      line=$0; sub(/^# ?/, "", line)
      if (line=="" && seen_codes) exit
      if (line ~ /^Exit codes:/) seen_codes=1
      print line
    }
  ' "$0"
}

# --- args --------------------------------------------------------------------
MODE=install; INSTALL_TMUX=0; VSCODE_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check)         MODE=check; shift ;;
    --install-tmux)  INSTALL_TMUX=1; shift ;;
    --vscode-tasks)  [ $# -ge 2 ] || { echo "error: --vscode-tasks needs a directory" >&2; usage >&2; exit 2; }
                     VSCODE_DIR="$2"; shift 2 ;;
    -h|--help)       usage; exit 0 ;;
    *)               echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# Verify every bundled artifact up front — before anything is written. Otherwise a
# missing templates/codex-agents-block.md would surface only AFTER CLAUDE.md was
# already rewritten, as a Python traceback (PM review #2).
_missing=""
for _f in bin/aipair bin/aipair-relay bin/aipair-relay-here bin/peer bin/peer-log bin/aipair-corelib bin/aipair-loglib bin/aipair-tmuxlib bin/aipair-deliverylib bin/aipair-dialoglib \
          templates/vscode-tasks.json templates/claude-md-block.md templates/codex-agents-block.md \
          .claude/skills/aipair-setup/SKILL.md .claude/skills/aipair-relay/SKILL.md; do
  [ -f "$REPO_DIR/$_f" ] || _missing="$_missing $_f"
done
if [ -n "$_missing" ]; then
  printf '[fail] incomplete aipair checkout — missing:%s\n' "$_missing" >&2
  printf '       run this script from a complete clone of the aipair repository (nothing was changed)\n' >&2
  exit 1
fi

# --- detection helpers ---------------------------------------------------------
detect_os() {
  case "$(uname -s 2>/dev/null)" in
    Darwin) OS=macos ;;
    # WSL2: the kernel string alone is not enough (Docker Desktop containers share the WSL2 kernel),
    # so look for WSL's own markers.
    Linux)  if [ -n "${WSL_DISTRO_NAME:-}" ] || [ -d /run/WSL ] || [ -e /proc/sys/fs/binfmt_misc/WSLInterop ]; then OS=wsl2; else OS=linux; fi ;;
    *)      OS=unknown ;;
  esac
  OS_DETAIL="$(uname -s 2>/dev/null) $(uname -r 2>/dev/null)"
  if [ -r /etc/os-release ]; then
    OS_DETAIL="$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-${ID:-unknown}}")"
  elif [ "$OS" = macos ] && command -v sw_vers >/dev/null 2>&1; then
    OS_DETAIL="macOS $(sw_vers -productVersion 2>/dev/null)"
  fi
}
detect_pkg() {
  PKG=none
  if   command -v dnf     >/dev/null 2>&1; then PKG=dnf
  elif command -v apt-get >/dev/null 2>&1; then PKG=apt
  elif command -v pacman  >/dev/null 2>&1; then PKG=pacman
  elif command -v brew    >/dev/null 2>&1; then PKG=brew
  fi
}
IS_ROOT=0; [ "$(id -u)" = 0 ] && IS_ROOT=1
HAS_SUDO=0; command -v sudo >/dev/null 2>&1 && HAS_SUDO=1

# ver_ge VERSION MAJOR MINOR  — VERSION like "3.2a" / "3.9.25" / "3"
ver_ge() {
  local v="$1" maj min
  maj=$(printf '%s' "$v" | sed -E 's/^([0-9]+).*/\1/')
  case "$v" in *.*) min=$(printf '%s' "$v" | sed -E 's/^[0-9]+\.([0-9]+).*/\1/') ;; *) min=0 ;; esac
  case "$maj" in ''|*[!0-9]*) return 1 ;; esac
  case "$min" in ''|*[!0-9]*) min=0 ;; esac
  [ "$maj" -gt "$2" ] || { [ "$maj" -eq "$2" ] && [ "$min" -ge "$3" ]; }
}
tmux_version()    { tmux -V 2>/dev/null | sed -E 's/^tmux[[:space:]]+(next-)?//' | head -1; }
python3_version() { python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null; }
# `timeout` bounds a CLI that blocks on auth; fall back to no timeout where coreutils
# timeout is absent (e.g. macOS without it) so behaviour is unchanged there (PM review #4).
if command -v timeout >/dev/null 2>&1; then _vt() { timeout 10 "$@"; }; else _vt() { "$@"; }; fi
claude_version()  { _vt claude --version 2>/dev/null | head -1 | sed -E 's/[[:space:]]+\(.*\)$//'; }
codex_version()   { _vt codex --version 2>/dev/null | head -1 | sed -E 's/^codex(-cli)?[[:space:]]+//'; }

locale_utf8() {
  local l="${LC_ALL:-${LC_CTYPE:-${LANG:-}}}"
  case "$l" in *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*) return 0 ;; *) return 1 ;; esac
}
path_has_bin() { case ":${PATH}:" in *":${BIN_DIR}:"*) return 0 ;; *) return 1 ;; esac; }

# same_file A B — byte-identical? (diffutils may be absent; python3 is a hard dependency anyway)
same_file() {
  [ -f "$1" ] && [ -f "$2" ] || return 1
  python3 - "$1" "$2" <<'PY'
import filecmp, sys
sys.exit(0 if filecmp.cmp(sys.argv[1], sys.argv[2], shallow=False) else 1)
PY
}

# block_status FILE -> present | absent | broken | missing-file
block_status() {
  local f="$1" s e
  [ -f "$f" ] || { echo missing-file; return; }
  s=$(grep -cF -- "$MARK_START" "$f"); e=$(grep -cF -- "$MARK_END" "$f")
  if [ "$s" -eq 0 ] && [ "$e" -eq 0 ]; then echo absent
  elif [ "$s" -eq 1 ] && [ "$e" -eq 1 ]; then echo present
  else echo broken; fi
}
# block_current FILE TEMPLATE -> 1 if the present block equals the template, else 0
block_current() {
  python3 - "$1" "$2" "$MARK_START" "$MARK_END" <<'PY' 2>/dev/null || echo 0
import sys
p, t, S, E = sys.argv[1:5]
S = S.encode(); E = E.encode()
try:
    d = open(p, 'rb').read(); b = open(t, 'rb').read()
except OSError:
    print(0); sys.exit(0)
if not b.endswith(b"\n"): b += b"\n"
i = d.find(S); j = d.find(E)
if i < 0 or j < 0: print(0); sys.exit(0)
j += len(E)
if d[j:j+1] == b"\n": j += 1
print(1 if d[i:j] == b else 0)
PY
}

tmux_install_cmds() {
  case "$PKG" in
    dnf)    echo "dnf install -y tmux" ;;
    apt)    echo "apt-get update"; echo "apt-get install -y tmux" ;;
    pacman) echo "pacman -Syu --needed --noconfirm tmux" ;;
    brew)   echo "brew install tmux" ;;
  esac
}
tmux_install_hint() {
  local prefix=""
  if [ "$IS_ROOT" -eq 0 ] && [ "$PKG" != brew ]; then prefix="sudo "; fi
  case "$PKG" in
    none) note "no supported package manager found (dnf / apt / pacman / brew) — install tmux >= $TMUX_MIN manually" ;;
    *)    note "to install tmux, re-run with --install-tmux, or run:"; tmux_install_cmds | sed "s/^/         ${prefix}/" ;;
  esac
}

# --- detection -----------------------------------------------------------------
detect_os; detect_pkg
HAVE_TMUX=0; TMUX_VER="-"; TMUX_OK=0
if command -v tmux >/dev/null 2>&1; then HAVE_TMUX=1; TMUX_VER="$(tmux_version)"; [ -z "$TMUX_VER" ] && TMUX_VER="unknown"; ver_ge "$TMUX_VER" "$TMUX_MIN_MAJOR" "$TMUX_MIN_MINOR" && TMUX_OK=1; fi
HAVE_PY=0; PY_VER="-"; PY_OK=0
if command -v python3 >/dev/null 2>&1; then HAVE_PY=1; PY_VER="$(python3_version)"; [ -z "$PY_VER" ] && PY_VER="unknown"; ver_ge "$PY_VER" "$PY_MIN_MAJOR" "$PY_MIN_MINOR" && PY_OK=1; fi
HAVE_CLAUDE=0; CLAUDE_VER="-"; command -v claude >/dev/null 2>&1 && { HAVE_CLAUDE=1; CLAUDE_VER="$(claude_version)"; [ -z "$CLAUDE_VER" ] && CLAUDE_VER="unknown"; }
HAVE_CODEX=0;  CODEX_VER="-";  command -v codex  >/dev/null 2>&1 && { HAVE_CODEX=1;  CODEX_VER="$(codex_version)";   [ -z "$CODEX_VER" ]  && CODEX_VER="unknown"; }
LOCALE_OK=0; locale_utf8 && LOCALE_OK=1
PATH_OK=0; path_has_bin && PATH_OK=1
SHELL_NAME="$(basename "${SHELL:-sh}")"

installed_state() { # -> none | partial | all
  local n=0 f
  for f in "${FILES[@]}"; do [ -f "$BIN_DIR/$f" ] && n=$((n+1)); done
  if [ "$n" -eq 0 ]; then echo none; elif [ "$n" -eq "${#FILES[@]}" ]; then echo all; else echo partial; fi
}
installed_current() { # -> 1 if every installed copy equals the repo copy
  local f
  [ "$HAVE_PY" -eq 1 ] || { echo 0; return; }
  for f in "${FILES[@]}"; do same_file "$REPO_DIR/bin/$f" "$BIN_DIR/$f" || { echo 0; return; }; done
  echo 1
}

# --- --check -----------------------------------------------------------------
if [ "$MODE" = check ]; then
  CM_STATUS=$(block_status "$CLAUDE_MD"); CA_STATUS=$(block_status "$CODEX_AGENTS")
  CM_CUR=0; [ "$CM_STATUS" = present ] && [ "$HAVE_PY" -eq 1 ] && CM_CUR=$(block_current "$CLAUDE_MD" "$REPO_DIR/templates/claude-md-block.md")
  CA_CUR=0; [ "$CA_STATUS" = present ] && [ "$HAVE_PY" -eq 1 ] && CA_CUR=$(block_current "$CODEX_AGENTS" "$REPO_DIR/templates/codex-agents-block.md")
  DEPS_OK=1; { [ "$TMUX_OK" -eq 1 ] && [ "$PY_OK" -eq 1 ] && [ "$HAVE_CLAUDE" -eq 1 ] && [ "$HAVE_CODEX" -eq 1 ]; } || DEPS_OK=0
  cat <<EOF
os=$OS
os_detail=$OS_DETAIL
pkg_manager=$PKG
is_root=$IS_ROOT
sudo=$HAS_SUDO
tmux=$HAVE_TMUX
tmux_version=$TMUX_VER
tmux_min=$TMUX_MIN
tmux_ok=$TMUX_OK
python3=$HAVE_PY
python3_version=$PY_VER
python3_min=$PY_MIN
python3_ok=$PY_OK
claude=$HAVE_CLAUDE
claude_version=$CLAUDE_VER
codex=$HAVE_CODEX
codex_version=$CODEX_VER
locale_utf8=$LOCALE_OK
lang=${LC_ALL:-${LANG:-}}
shell=$SHELL_NAME
install_dir=$BIN_DIR
path_ok=$PATH_OK
aipair_installed=$(installed_state)
aipair_current=$(installed_current)
skills_dir=$SKILLS_DIR
skill_aipair_setup=$([ -f "$SKILLS_DIR/aipair-setup/SKILL.md" ] && echo 1 || echo 0)
skill_aipair_relay=$([ -f "$SKILLS_DIR/aipair-relay/SKILL.md" ] && echo 1 || echo 0)
claude_md=$CLAUDE_MD
claude_md_block=$CM_STATUS
claude_md_block_current=$CM_CUR
codex_agents=$CODEX_AGENTS
codex_agents_block=$CA_STATUS
codex_agents_block_current=$CA_CUR
deps_ok=$DEPS_OK
EOF
  [ "$DEPS_OK" -eq 1 ] && exit 0 || exit 3
fi

# =============================================================================
# install
# =============================================================================
echo "aipair-install.sh — repo: $REPO_DIR"
echo "                    home: $AH  (bin: $BIN_DIR)"
ok "os: $OS ($OS_DETAIL), package manager: $PKG, $([ "$IS_ROOT" -eq 1 ] && echo root || echo "non-root (sudo: $([ "$HAS_SUDO" -eq 1 ] && echo yes || echo no))")"

# --- tmux --------------------------------------------------------------------
tmux_step() {
  if [ "$HAVE_TMUX" -eq 1 ] && [ "$TMUX_OK" -eq 1 ]; then
    if [ "$INSTALL_TMUX" -eq 1 ]; then skip "tmux $TMUX_VER already installed (>= $TMUX_MIN); --install-tmux not needed"
    else ok "tmux $TMUX_VER (>= $TMUX_MIN)"; fi
    return 0
  fi
  if [ "$HAVE_TMUX" -eq 1 ]; then
    fail "tmux $TMUX_VER is too old: aipair needs >= $TMUX_MIN ('split-window -l 30%' percentage sizes)"
    note "--install-tmux does not upgrade an existing tmux; upgrade it with your package manager or build from source"
    return 3
  fi
  if [ "$INSTALL_TMUX" -eq 0 ]; then
    fail "tmux not found (required, >= $TMUX_MIN). This script never runs sudo on its own."
    tmux_install_hint
    return 3
  fi
  if [ "$PKG" = none ]; then
    fail "tmux not found and no supported package manager (dnf / apt / pacman / brew) — install tmux >= $TMUX_MIN manually"
    return 3
  fi
  local prefix="" c
  if [ "$IS_ROOT" -eq 0 ] && [ "$PKG" != brew ]; then
    if [ "$HAS_SUDO" -eq 0 ]; then
      fail "tmux: not root and 'sudo' not found — run as root: $(tmux_install_cmds | paste -sd ';' -)"
      return 3
    fi
    prefix="sudo "
  fi
  [ "$PKG" = apt ] && export DEBIAN_FRONTEND=noninteractive
  while IFS= read -r c; do
    printf '[run]  %s%s\n' "$prefix" "$c"
    # shellcheck disable=SC2086
    if ! ${prefix}$c; then
      fail "tmux: command failed: ${prefix}$c"
      return 3
    fi
  done < <(tmux_install_cmds)
  hash -r 2>/dev/null || true
  if ! command -v tmux >/dev/null 2>&1; then
    fail "tmux: install command finished but 'tmux' is still not on PATH ($PATH)"
    return 3
  fi
  TMUX_VER="$(tmux_version)"; HAVE_TMUX=1
  if ver_ge "$TMUX_VER" "$TMUX_MIN_MAJOR" "$TMUX_MIN_MINOR"; then
    TMUX_OK=1; ok "tmux $TMUX_VER installed via $PKG (>= $TMUX_MIN)"
  else
    fail "tmux $TMUX_VER installed via $PKG but < $TMUX_MIN — this distro's package is too old; build a newer tmux or use another source"
    return 3
  fi
}
DEP_FAIL=0
tmux_step || DEP_FAIL=1

# --- python3 / claude / codex ------------------------------------------------
if [ "$HAVE_PY" -eq 1 ] && [ "$PY_OK" -eq 1 ]; then ok "python3 $PY_VER (>= $PY_MIN)"
elif [ "$HAVE_PY" -eq 1 ]; then fail "python3 $PY_VER is too old: aipair-relay needs >= $PY_MIN (assignment expressions)"; note "install a newer python3 with your package manager"; DEP_FAIL=1
else fail "python3 not found (required, >= $PY_MIN — standard library only)"; note "install: dnf install python3 / apt-get install python3 / pacman -S python / brew install python"; DEP_FAIL=1; fi
if [ "$HAVE_CLAUDE" -eq 1 ] && [ "$CLAUDE_VER" != unknown ]; then ok "claude $CLAUDE_VER"
elif [ "$HAVE_CLAUDE" -eq 1 ]; then warn "claude found ($(command -v claude)) but 'claude --version' failed — the install may be broken (e.g. npm postinstall did not fetch the native binary); run 'claude --version' yourself"
else fail "claude (Claude Code CLI) not found"; note "install: npm install -g @anthropic-ai/claude-code   (docs: https://docs.anthropic.com/en/docs/claude-code)"; note "then log in once: claude"; DEP_FAIL=1; fi
if [ "$HAVE_CODEX" -eq 1 ] && [ "$CODEX_VER" != unknown ]; then ok "codex $CODEX_VER"
elif [ "$HAVE_CODEX" -eq 1 ]; then warn "codex found ($(command -v codex)) but 'codex --version' failed — the install may be broken; run 'codex --version' yourself"
else fail "codex (OpenAI Codex CLI) not found"; note "install: npm install -g @openai/codex   (docs: https://github.com/openai/codex)"; note "then log in once: codex"; DEP_FAIL=1; fi
if [ "$DEP_FAIL" -eq 1 ]; then
  echo "aborted: missing or too-old dependencies (see [fail] lines above). Nothing was installed." >&2
  exit 3
fi

# --- locale ------------------------------------------------------------------
if [ "$LOCALE_OK" -eq 1 ]; then ok "locale is UTF-8 (${LC_ALL:-${LANG:-}})"
else warn "locale is not UTF-8 (LANG=${LANG:-unset} LC_ALL=${LC_ALL:-unset}) — the Japanese stop phrase and box-drawing characters may break; export LANG=C.UTF-8 (or a UTF-8 locale)"; fi

# --- files -------------------------------------------------------------------
mkdir -p "$BIN_DIR" || { fail "cannot create $BIN_DIR"; exit 1; }
# Retire binaries this project no longer ships (D2: aipair-queue removed). An older install
# would otherwise leave a runnable, now-unsupported copy in PATH.
RETIRED=(aipair-queue)
for f in "${RETIRED[@]}"; do
  if [ -e "$BIN_DIR/$f" ]; then
    if mv "$BIN_DIR/$f" "$BIN_DIR/$f.removed-$TS"; then
      ok "retired $BIN_DIR/$f (no longer part of aipair; moved to $f.removed-$TS)"
    else
      # A stale, unsupported (and, for aipair-queue, dangerous) binary left runnable in PATH
      # is not a safe success — stop.
      fail "could not retire stale $BIN_DIR/$f — remove it by hand, then re-run"
      exit 1
    fi
  fi
done
for f in "${FILES[@]}"; do
  src="$REPO_DIR/bin/$f"; dst="$BIN_DIR/$f"
  if [ -f "$dst" ] && same_file "$src" "$dst"; then
    [ -x "$dst" ] || chmod +x "$dst"
    skip "$dst is up to date"
    continue
  fi
  if [ -e "$dst" ]; then
    cp -p "$dst" "$dst.bak-$TS" || { fail "cannot back up $dst"; exit 1; }
    cp "$src" "$dst" && chmod +x "$dst" || { fail "cannot install $dst"; exit 1; }
    ok "$dst updated (previous copy: $dst.bak-$TS)"
  else
    cp "$src" "$dst" && chmod +x "$dst" || { fail "cannot install $dst"; exit 1; }
    ok "$dst installed"
  fi
done
# same-directory import check (aipair-relay imports peer-log + aipair-corelib + aipair-loglib + aipair-tmuxlib + aipair-deliverylib + aipair-dialoglib by path)
for f in aipair-relay peer-log; do
  if ! "$BIN_DIR/$f" --help >/dev/null 2>&1; then
    fail "$BIN_DIR/$f --help failed — all ${#FILES[@]} bin files must sit together in $BIN_DIR (they import each other by path)"
    exit 1
  fi
done
ok "aipair-relay / peer-log start from $BIN_DIR (--help exits 0)"

# --- skills ------------------------------------------------------------------
for s in "${SKILLS[@]}"; do
  src="$REPO_DIR/.claude/skills/$s/SKILL.md"; dst="$SKILLS_DIR/$s/SKILL.md"
  [ -f "$src" ] || { fail "missing in repo: $src"; exit 1; }
  if [ -f "$dst" ] && same_file "$src" "$dst"; then skip "skill $s is up to date ($dst)"; continue; fi
  mkdir -p "$(dirname "$dst")" || { fail "cannot create $(dirname "$dst")"; exit 1; }
  if [ -e "$dst" ]; then
    cp -p "$dst" "$dst.bak-$TS" || { fail "cannot back up $dst"; exit 1; }
    cp "$src" "$dst" || { fail "cannot install $dst"; exit 1; }
    ok "skill $s updated ($dst; previous copy: $dst.bak-$TS)"
  else
    cp "$src" "$dst" || { fail "cannot install $dst"; exit 1; }
    ok "skill $s installed ($dst)"
  fi
done

# --- PATH --------------------------------------------------------------------
if path_has_bin; then ok "$BIN_DIR is on PATH"
else
  # shellcheck disable=SC2088  # rc is shown to the user, the literal "~" is intended
  case "$SHELL_NAME" in
    zsh)  line='export PATH="$HOME/.local/bin:$PATH"'; rc="~/.zshrc" ;;
    fish) line='fish_add_path -U ~/.local/bin';        rc="(run once)" ;;
    *)    line='export PATH="$HOME/.local/bin:$PATH"'; rc="~/.bashrc" ;;
  esac
  warn "$BIN_DIR is not on PATH — add it yourself (this script does not edit shell rc files):"
  note "$rc:  $line"
fi

# --- notice blocks -----------------------------------------------------------
# install_block FILE TEMPLATE — replace/append the marker-delimited block; prints the action.
# Implemented in python3 for byte-exact handling; result is verified before declaring success.
install_block() {
  local file="$1" tpl="$2" res
  res=$(python3 - "$file" "$tpl" "$TS" "$MARK_START" "$MARK_END" <<'PY'
import os, shutil, sys
path, tpl, ts, S, E = sys.argv[1:6]
S = S.encode(); E = E.encode()
block = open(tpl, 'rb').read()
if block.count(S) != 1 or block.count(E) != 1 or block.find(S) > block.find(E):
    print("template-broken"); sys.exit(4)
if not block.endswith(b"\n"):
    block += b"\n"
exists = os.path.exists(path)
old = open(path, 'rb').read() if exists else b""
s, e = old.count(S), old.count(E)
if s == 0 and e == 0:
    if not old:              sep = b""
    elif old.endswith(b"\n\n"): sep = b""
    elif old.endswith(b"\n"):   sep = b"\n"
    else:                       sep = b"\n\n"
    new = old + sep + block
    action = "appended" if exists else "created"
elif s == 1 and e == 1 and old.find(S) < old.find(E):
    i = old.find(S); j = old.find(E) + len(E)
    if old[j:j+1] == b"\n": j += 1
    new = old[:i] + block + old[j:]
    action = "replaced"
else:
    print("broken"); sys.exit(5)
if exists and new == old:
    print("same"); sys.exit(0)
bak = None
if exists:
    bak = f"{path}.aipair-bak-{ts}"
    shutil.copy2(path, bak)
else:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
tmp = path + ".aipair-tmp"
with open(tmp, 'wb') as f:
    f.write(new)
os.replace(tmp, path)
# verify from the file on disk, not from memory
chk = open(path, 'rb').read()
good = (chk == new and chk.count(S) == 1 and chk.count(E) == 1)
if good:
    if action == "replaced":
        ci = chk.find(S); cj = chk.find(E) + len(E)
        if chk[cj:cj+1] == b"\n": cj += 1
        good = (chk[:ci] == old[:i]) and (chk[cj:] == old[j:]) and (chk[ci:cj] == block)
    else:
        good = chk.startswith(old) and chk[len(old):] == sep + block
if not good:
    if bak: shutil.copy2(bak, path)
    else:   os.remove(path)
    print("verify-failed"); sys.exit(6)
print(action + ("" if bak is None else f" (backup: {bak})"))
PY
  ) ; local rc=$?
  case "$rc:$res" in
    0:same)              skip "$file: aipair block already up to date (untouched)" ;;
    0:*)                 ok "$file: aipair block $res; verified that the rest of the file is byte-identical" ;;
    *:broken)            fail "$file: found a damaged aipair block (markers missing or duplicated). Fix it by hand, then re-run. File left untouched."; return 1 ;;
    *:template-broken)   fail "$tpl: template must contain exactly one start and one end marker"; return 1 ;;
    *:verify-failed)     fail "$file: post-write verification failed — restored the backup, nothing changed"; return 1 ;;
    *)                   fail "$file: block install failed (python exit $rc: $res)"; return 1 ;;
  esac
}
install_block "$CLAUDE_MD"   "$REPO_DIR/templates/claude-md-block.md"   || exit 1
install_block "$CODEX_AGENTS" "$REPO_DIR/templates/codex-agents-block.md" || exit 1

# --- optional: VS Code tasks ---------------------------------------------------
if [ -n "$VSCODE_DIR" ]; then
  if [ ! -d "$VSCODE_DIR" ]; then fail "--vscode-tasks: not a directory: $VSCODE_DIR"; exit 1; fi
  dst="$VSCODE_DIR/.vscode/tasks.json"; src="$REPO_DIR/templates/vscode-tasks.json"
  if [ -f "$dst" ]; then
    if same_file "$src" "$dst"; then skip "$dst already equals the template"
    else skip "$dst exists — not overwriting. Merge the 'tasks' entries yourself, e.g.: cp $src $VSCODE_DIR/.vscode/tasks.aipair.json"; fi
  else
    mkdir -p "$VSCODE_DIR/.vscode" && cp "$src" "$dst" && ok "$dst installed (WSL2 launcher tasks; see README)" || { fail "cannot write $dst"; exit 1; }
  fi
fi

# --- smoke test ----------------------------------------------------------------
# Start a real pair in a throw-away directory with --version flags (so neither TUI actually starts),
# judge by tmux facts only (session exists, 3 panes), then stop it. aipair's own exit code is ignored:
# it ends with `tmux attach`, which fails on a non-TTY. Existing aipair-* sessions are never touched.
smoke_test() {
  local tmp name panes
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/aipair-smoke.XXXXXX") || { fail "smoke: mktemp failed"; return 1; }
  name=$(env -u TMUX "$BIN_DIR/aipair" name "$tmp" 2>/dev/null) || { fail "smoke: 'aipair name' failed"; rmdir "$tmp"; return 1; }
  case "$name" in aipair-aipair-smoke-*) ;; *) fail "smoke: unexpected session name '$name' — refusing to continue"; rmdir "$tmp"; return 1 ;; esac
  if tmux has-session -t "$name" 2>/dev/null; then fail "smoke: session '$name' already exists — left untouched"; rmdir "$tmp"; return 1; fi
  env -u TMUX PATH="$BIN_DIR:$PATH" AIPAIR_CLAUDE_FLAGS=--version AIPAIR_CODEX_FLAGS=--version \
    "$BIN_DIR/aipair" "$tmp" </dev/null >/dev/null 2>&1 || true
  sleep 1
  if ! tmux has-session -t "$name" 2>/dev/null; then fail "smoke: tmux session '$name' was not created by 'aipair $tmp'"; rmdir "$tmp"; return 1; fi
  panes=$(tmux list-panes -t "$name" 2>/dev/null | wc -l | tr -d ' ')
  # Tear down. Don't swallow failure silently (global rule #19): if the throw-away
  # session or dir survives, tell the user how to remove it (PM review #3).
  local cleaned=1
  env -u TMUX "$BIN_DIR/aipair" stop "$tmp" >/dev/null 2>&1 || tmux kill-session -t "$name" 2>/dev/null || true
  if tmux has-session -t "$name" 2>/dev/null; then
    cleaned=0
    warn "smoke: could not remove throw-away session '$name' — run: tmux kill-session -t $name"
  fi
  rmdir "$tmp" 2>/dev/null || true
  if [ -d "$tmp" ]; then
    cleaned=0
    warn "smoke: could not remove throw-away dir '$tmp' — run: rm -rf $tmp"
  fi
  if [ "$panes" != 3 ]; then fail "smoke: expected 3 panes (claude / codex / bridge), got $panes"; return 1; fi
  # Don't claim it was removed if a warning above said otherwise.
  if [ "$cleaned" -eq 1 ]; then
    ok "smoke: 'aipair <tmpdir>' created tmux session '$name' with 3 panes, then tore it down"
  else
    ok "smoke: 'aipair <tmpdir>' created tmux session '$name' with 3 panes (throw-away left behind — see warning above)"
  fi
}
smoke_test || exit 1

# --- summary -------------------------------------------------------------------
echo "done: $N_OK ok, $N_SKIP skip, $N_WARN warn, $N_FAIL fail"
if [ "$N_WARN" -gt 0 ]; then echo "      (warnings above need your attention, but the install itself is complete)"; fi
echo "next: open a NEW shell (so PATH applies), cd into a project, run: aipair"
echo "      safe by default (normal permission prompts); permission-bypass is opt-in via --unsafe / AIPAIR_UNSAFE=1, required for aipair loop"
exit 0
