#!/usr/bin/env python3
"""P2-3: doc↔code sync. README / SECURITY.md must not drift from the code's source of truth — the
tested CLI versions, the stop/next/all-done/plan sentinels, the relay exit codes, and the schema
fail-closed protocol. These are exactly the values a user reads to operate aipair safely, so a
silent drift (bump TESTED_VERSIONS but forget the README table; rename a sentinel default but leave
the docs) is a real hazard. Each test pins one doc claim to the constant it must equal.
    python3 tests/doc-sync.py
"""
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "bin"))
import aipairlib.corelib as corelib   # noqa: E402
import aipairlib.cli as cli           # noqa: E402


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


README = _read("README.md")
SECURITY = _read("SECURITY.md")
VER_RE = re.compile(r"\d+\.\d+\.\d+")


def _rows_with_first_cell(text, first_cell):
    """Markdown table rows (| a | b | …) whose FIRST cell trims to first_cell."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and cells[0] == first_cell:
            out.append(ln)
    return out


class Versions(unittest.TestCase):
    """README「必要環境」表の版 == corelib.TESTED_VERSIONS（bump 忘れ・stale 版の両方を捕捉）。"""
    def test_readme_version_table_matches_TESTED_VERSIONS(self):
        self.assertTrue(corelib.TESTED_VERSIONS, "TESTED_VERSIONS is empty")
        for agent, ver in corelib.TESTED_VERSIONS.items():
            rows = _rows_with_first_cell(README, "`%s`" % agent)
            hits = [r for r in rows if VER_RE.search(r)]
            self.assertTrue(hits, "no README version-table row for `%s`" % agent)
            for r in hits:
                self.assertEqual(set(VER_RE.findall(r)), {ver},
                                 "README `%s` row lists %s but TESTED_VERSIONS=%s — update the README 表"
                                 % (agent, VER_RE.findall(r), ver))


class Sentinels(unittest.TestCase):
    """停止・状態遷移 sentinel の《既定値》== build_parser の argparse 既定（README がドキュメント）。"""
    # argparse dest → the env var README documents it under
    CASES = [("stop", "AIPAIR_STOP"), ("next_ask", "AIPAIR_NEXT_ASK"),
             ("all_done", "AIPAIR_ALL_DONE"), ("plan_ok", "AIPAIR_PLAN_OK")]

    def setUp(self):
        # Test the BUILT-IN argparse defaults, not this shell's AIPAIR_* overrides — build the
        # parser with every AIPAIR_* env var removed (else `AIPAIR_STOP=CUSTOM` makes the "default"
        # CUSTOM and the sync check reads the environment instead of the code).
        with mock.patch.dict(os.environ):
            for _k in [k for k in os.environ if k.startswith("AIPAIR_")]:
                del os.environ[_k]
            self.defaults = vars(cli.build_parser("x").parse_args([]))

    def test_defaults_are_bracketed_sentinels(self):
        for attr, _env in self.CASES:
            self.assertRegex(self.defaults[attr], r"^\[AIPAIR_[A-Z_]+\]$",
                             "%s default is not a [AIPAIR_*] sentinel: %r" % (attr, self.defaults[attr]))

    def test_readme_documents_each_default_next_to_its_env_var(self):
        # a precise sync: one README line must mention BOTH the env var AND the code's default value,
        # so renaming the default in code (without touching README) fails here.
        for attr, env in self.CASES:
            val = self.defaults[attr]
            lines = [ln for ln in README.splitlines() if env in ln and val in ln]
            self.assertTrue(lines, "README has no line documenting %s's default as %s" % (env, val))


class ExitCodes(unittest.TestCase):
    """README の exit code 表 == 正準集合、かつ state_machine の reason dict のコードは全て記載済み。"""
    CANON = {0, 2, 3, 4, 5, 6, 7, 130}

    def _readme_exit_codes(self):
        # README has TWO『| code |』tables — the installer\'s (0/1/2/3) and the relay\'s. Anchor on
        # the relay heading so this pins the RELAY exit codes, not the installer\'s.
        lines = README.splitlines()
        start = next((i for i, ln in enumerate(lines) if "relay の exit code" in ln), None)
        self.assertIsNotNone(start, "README must have a『relay の exit code』section")
        codes, in_tbl = set(), False
        for ln in lines[start:]:
            if re.match(r"\|\s*code\s*\|", ln):
                in_tbl = True
                continue
            if in_tbl:
                if ln.strip().startswith("|---"):
                    continue
                m = re.match(r"\|\s*(\d+)\s*\|", ln)
                if m:
                    codes.add(int(m.group(1)))
                elif not ln.strip().startswith("|"):
                    break
        return codes

    def test_readme_exit_table_is_the_canonical_set(self):
        self.assertEqual(self._readme_exit_codes(), self.CANON,
                         "README exit-code 表が正準集合とずれている")

    def test_state_machine_reason_codes_are_all_documented(self):
        src = _read("bin/aipairlib/state_machine.py")
        m = re.search(r"reason = \{([^}]*)\}", src)
        self.assertTrue(m, "could not find the reason dict in state_machine.py")
        keys = set(int(k) for k in re.findall(r"(\d+):", m.group(1)))
        self.assertTrue(keys, "no reason-dict keys parsed")
        self.assertTrue(keys <= self._readme_exit_codes(),
                        "state_machine reason codes %s not all in the README exit 表" % sorted(keys))


class SchemaProtocol(unittest.TestCase):
    """schema fail-closed（exit 7）と --allow-untested-schema がコード・README・SECURITY で一致。"""
    def test_exit_7_is_the_schema_fail_closed_stop(self):
        relay_src = _read("bin/aipairlib/relay.py")
        self.assertIn("return 7", relay_src, "relay must return 7 on a startup schema mismatch")
        self.assertIn("exit 7", SECURITY, "SECURITY.md must describe the schema exit 7")
        for doc, name in ((README, "README"), (SECURITY, "SECURITY")):
            self.assertIn("fail-closed", doc, "%s must describe the fail-closed schema stop" % name)

    def test_allow_untested_schema_flag_exists_and_is_documented(self):
        self.assertIn("allow_untested_schema", vars(cli.build_parser("x").parse_args([])),
                      "--allow-untested-schema missing from build_parser")
        for doc, name in ((README, "README"), (SECURITY, "SECURITY")):
            self.assertIn("allow-untested-schema", doc, "%s must document --allow-untested-schema" % name)


def _yaml_jobs(rel):
    """Top-level job keys under a workflow's `jobs:` block (regex, no yaml dep)."""
    lines = _read(rel).splitlines()
    i = next((i for i, l in enumerate(lines) if l.rstrip() == "jobs:"), None)
    if i is None:
        return set()
    jobs = set()
    for l in lines[i + 1:]:
        if re.match(r"^\S", l) and l.strip():
            break
        m = re.match(r"^  ([a-z][a-z0-9_-]+):\s*$", l)
        if m:
            jobs.add(m.group(1))
    return jobs


class TodoAndWorkflows(unittest.TestCase):
    """P2-3 が対象に挙げた todo / CI 説明も、コード・workflow の実態に同期していることを固定する。"""
    def test_todo_does_not_assert_manual_version_sync(self):
        # tests/doc-sync.py が TESTED_VERSIONS↔README を強制するので、それを《手動同期》と断定する
        # 古い注記（原文パターン「…表は手動同期」）が todo に残っていてはならない（貢献者を誤誘導する）。
        # メタ記述（「手動同期」を引用して更新履歴に触れる等）は copula「は手動同期」を含まないので誤検知しない。
        for i, ln in enumerate(_read("tasks/todo.md").splitlines(), 1):
            if "TESTED_VERSIONS" in ln and "は手動同期" in ln:
                self.fail("tasks/todo.md:%d が TESTED_VERSIONS を《手動同期》と断定している — doc-sync.py が"
                          " 強制するので更新すること: %r" % (i, ln.strip()))

    def test_nightly_jobs_are_defined_and_documented(self):
        jobs = _yaml_jobs(".github/workflows/nightly.yml")
        self.assertTrue(jobs, "could not parse nightly.yml jobs")
        for want in ("upstream-latest-smoke", "authenticated-e2e"):
            self.assertIn(want, jobs, "nightly.yml must define the job %s" % want)
        for job in jobs:   # every nightly job must be written up in the README nightly 表
            self.assertIn(job, README, "nightly job %s is undocumented in README" % job)

    def test_ci_matrix_versions_match_readme(self):
        ci = _read(".github/workflows/ci.yml")
        # README「必要環境」/CI 説明: python floor 3.8 + current 3.13、tmux floor 3.1
        for v in ("3.8", "3.13"):
            self.assertIn("'%s'" % v, ci, "ci.yml matrix must include python %s" % v)
        self.assertIn("'3.1'", ci, "ci.yml matrix must include the tmux 3.1 floor")
        for v in ("3.8", "3.13", "3.1"):
            self.assertIn(v, README, "README must document version %s used by CI" % v)



if __name__ == "__main__":
    unittest.main(verbosity=2)
