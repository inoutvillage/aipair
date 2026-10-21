"""aipairlib.logs — the shared colour/logging the relay and the delivery/dialog modules all use.

Previously relay defined `dim`/`c`/`log` and *injected* `dim` into deliverylib/dialoglib
(`deliverylib.dim = dim`). Here they are a real module everyone imports; colour stays a single
runtime flag the entrypoint sets once via `configure()` (TTY / --no-color), not a per-module
monkey-patch."""

USE_COLOR = True
C = {"claude": "\033[1;36m", "codex": "\033[1;32m", "relay": "\033[1;35m",
     "warn": "\033[1;31m", "ok": "\033[1;32m", "dim": "\033[2m", "off": "\033[0m"}


def configure(use_color):
    """Set colour on/off once (relay's main() passes `(not --no-color) and stdout.isatty()`)."""
    global USE_COLOR
    USE_COLOR = bool(use_color)


def c(key, s):
    return f"{C[key]}{s}{C['off']}" if USE_COLOR else s


def log(s):
    print(c("relay", "│ ") + s, flush=True)


def dim(s):
    print(c("dim", "│   " + s), flush=True)
