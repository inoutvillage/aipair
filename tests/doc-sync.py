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
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "bin"))
import aipairlib                       # noqa: E402  (for __version__)
import aipairlib.corelib as corelib   # noqa: E402
import aipairlib.cli as cli           # noqa: E402


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


README = _read("README.md")
SECURITY = _read("SECURITY.md")
VER_RE = re.compile(r"\d+\.\d+\.\d+")
# Official SemVer 2.0.0 grammar (semver.org): rejects 1.2.3.foo / 1.2.3-.. / leading zeros,
# accepts optional -prerelease and +build. Used to validate aipairlib.__version__.
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$")


def _builtin_defaults():
    """argparse defaults with ALL AIPAIR_* env vars removed — the code's BUILT-IN values, not this
    shell's overrides. Without this, `AIPAIR_STOP=X` fakes a sentinel default and `AIPAIR_MAX_ROUNDS=bad`
    makes build_parser sys.exit(2), so every doc↔default check must go through here."""
    with mock.patch.dict(os.environ):
        for _k in [k for k in os.environ if k.startswith("AIPAIR_")]:
            del os.environ[_k]
        return vars(cli.build_parser("x").parse_args([]))


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
        self.defaults = _builtin_defaults()

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
        self.assertIn("allow_untested_schema", _builtin_defaults(),
                      "--allow-untested-schema missing from build_parser")
        for doc, name in ((README, "README"), (SECURITY, "SECURITY")):
            self.assertIn("allow-untested-schema", doc, "%s must document --allow-untested-schema" % name)


class Protocol(unittest.TestCase):
    """stop/plan sentinel の《契約》— 最終回答の先頭行に単独・完全一致・誤停止/誤承認しない —
    が README/SECURITY に固定され、コード（corelib.hit_stop）の実挙動と一致すること。値の存在だけ
    でなく契約文を消したら落ちるように固定する。"""
    def test_readme_pins_the_head_line_only_contract(self):
        self.assertIn("先頭行に単独", README,
                      "README must state sentinels hold only when alone on the leading line")
        self.assertIn("否定文・引用・文中言及", README,
                      "README must state 否定文/引用/文中言及 do NOT trigger a stop")
        self.assertIn("先頭行に `[AIPAIR_PLAN_APPROVED]` を単独で", README,
                      "README must state plan approval needs the sentinel alone on the leading line")

    def test_security_pins_the_no_false_stop_contract(self):
        self.assertIn("先頭行に単独", SECURITY)
        self.assertIn("誤停止/誤承認しない", SECURITY,
                      "SECURITY must state the head-line-only rule prevents 誤停止/誤承認")

    def test_code_enforces_the_documented_head_line_contract(self):
        # the doc contract must match code: corelib.hit_stop fires ONLY on a lone leading-line match.
        self.assertTrue(corelib.hit_stop(["[AIPAIR_REVIEW_OK]"], ["[AIPAIR_REVIEW_OK]"]),
                        "a lone leading-line sentinel must stop")
        self.assertFalse(corelib.hit_stop(["なお [AIPAIR_REVIEW_OK] とは限らない"], ["[AIPAIR_REVIEW_OK]"]),
                         "an inline mention must NOT stop (誤停止しない)")
        self.assertFalse(corelib.hit_stop(["前置き\n[AIPAIR_REVIEW_OK]"], ["[AIPAIR_REVIEW_OK]"]),
                         "a sentinel on the 2nd line must NOT stop")


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

    def test_nightly_authenticated_e2e_pins_from_TESTED_VERSIONS(self):
        # job 名だけでは、authenticated-e2e が TESTED_VERSIONS 読取をやめて版をハードコードしても通る。
        # 実際に corelib.TESTED_VERSIONS を実行時に読み、その $CV/$XV で npm 導入することまで固定する。
        ny = _read(".github/workflows/nightly.yml")
        self.assertIn("from aipairlib.corelib import TESTED_VERSIONS", ny,
                      "authenticated-e2e must read the pins from corelib.TESTED_VERSIONS at runtime")
        self.assertIn('T["claude"]', ny)
        self.assertIn('T["codex"]', ny)
        self.assertIn("@anthropic-ai/claude-code@$CV", ny,
                      "must install the PINNED claude via the TESTED_VERSIONS value ($CV), not a hardcoded version")
        self.assertIn("@openai/codex@$XV", ny,
                      "must install the PINNED codex via the TESTED_VERSIONS value ($XV), not a hardcoded version")

    def _ci_matrix_rows(self):
        ci = _read(".github/workflows/ci.yml")
        return set(re.findall(r"\{\s*python:\s*'([^']+)',\s*tmux:\s*'?([^',}\s]+)'?\s*\}", ci))

    def _readme_lane_rows(self):
        lines = README.splitlines()
        start = next((i for i, l in enumerate(lines)
                      if re.match(r"\|\s*lane\s*\|\s*Python\s*\|\s*tmux\s*\|", l)), None)
        self.assertIsNotNone(start, "README must have a『| lane | Python | tmux |』3-lane table")
        rows = set()
        for l in lines[start + 1:]:
            if l.strip().startswith("|---"):
                continue
            if not l.strip().startswith("|"):
                break
            cells = [c.strip() for c in l.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            pym = re.search(r"\d+\.\d+", cells[1])
            tm = ("distro" if "distro" in cells[2]
                  else (re.search(r"\d+\.\d+", cells[2]).group(0) if re.search(r"\d+\.\d+", cells[2]) else cells[2]))
            if pym:
                rows.add((pym.group(0), tm))
        return rows

    def test_ci_matrix_rows_match_readme(self):
        # correlate PER ROW: ci.yml の実 matrix 行 == README 3-lane 表の (Python, tmux) 行 == 期待集合。
        expect = {("3.8", "distro"), ("3.13", "distro"), ("3.13", "3.1")}
        self.assertEqual(self._ci_matrix_rows(), expect,
                         "ci.yml matrix rows drifted: %s" % sorted(self._ci_matrix_rows()))
        self.assertEqual(self._readme_lane_rows(), expect,
                         "README 3-lane 表の行が ci.yml matrix とずれている: %s" % sorted(self._readme_lane_rows()))

    def test_current_behaviour_docs_avoid_the_stale_module_count(self):
        # the aipairlib package grew past the old「5 libs / 6 sibling modules」count (P2-1), so the
        # CURRENT-behaviour docs (ci.yml comment, README) must describe it count-free. (todo.md keeps
        # historical PR notes that legitimately record the old count, so it is not scanned here.)
        for text, name in ((_read(".github/workflows/ci.yml"), "ci.yml"), (README, "README")):
            for stale in ("5 lib", "6 sibling module", "6 module"):
                self.assertNotIn(stale, text, "%s still uses the stale module count %r — describe the "
                                 "aipairlib package count-free" % (name, stale))



class Version(unittest.TestCase):
    """P2-4: aipairlib.__version__ が唯一の source of truth で、CHANGELOG の最上位の版セクション
    （発行前=prepared / 発行後=Unreleased+dated の両状態）・`aipair --version` /
    `aipair-relay --version` の出力・release workflow の tag 検査が一致する。"""
    def test_version_is_semver(self):
        self.assertRegex(aipairlib.__version__, SEMVER,
                         "__version__ %r is not SemVer 2.0.0" % aipairlib.__version__)

    @staticmethod
    def _top_version(text):
        # the topmost VERSION-numbered『## [X.Y.Z]』section, skipping a『## [Unreleased]』heading.
        vers = [m for m in re.findall(r"^## \[([^\]]+)\]", text, re.M) if m.lower() != "unreleased"]
        return vers[0] if vers else None

    def test_changelog_top_version_section_matches_version(self):
        # the TOP version section must equal __version__ (bumping one without the other fails here).
        top = self._top_version(_read("CHANGELOG.md"))
        self.assertIsNotNone(top, "CHANGELOG.md has no『## [X.Y.Z]』version section")
        self.assertEqual(top, aipairlib.__version__,
                         "CHANGELOG.md top version section %s != __version__ %s (bump them together)"
                         % (top, aipairlib.__version__))

    def test_both_changelog_lifecycle_states_resolve_the_version(self):
        # BOTH layouts are valid and must resolve the same top version:
        preparing = "# CL\n\n## [0.2.0] — unreleased (prepared)\n\nwip\n"       # being prepared (undated)
        released  = "# CL\n\n## [Unreleased]\n\n## [0.2.0] - 2026-09-01\n\nshipped\n"  # right after a release
        self.assertEqual(self._top_version(preparing), "0.2.0", "prepared layout must resolve the version")
        self.assertEqual(self._top_version(released), "0.2.0", "post-release layout must resolve the version")
        # a bare Unreleased with no version section resolves to None (would fail the match test above)
        self.assertIsNone(self._top_version("# CL\n\n## [Unreleased]\n\nwip\n"))

    def test_aipair_version_reports_the_source_of_truth(self):
        r = subprocess.run([os.path.join(REPO, "bin", "aipair"), "--version"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "aipair " + aipairlib.__version__)

    def test_aipair_relay_version_reports_the_source_of_truth(self):
        r = subprocess.run([sys.executable, os.path.join(REPO, "bin", "aipair-relay"), "--version"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "aipair-relay " + aipairlib.__version__)

    def test_release_workflow_pins_the_tag_to_version(self):
        wf = _read(".github/workflows/release.yml")
        self.assertIn("aipairlib.__version__", wf, "release.yml must verify the tag against __version__")
        self.assertIn("gh release create", wf, "release.yml must create the GitHub Release")
        self.assertIn("tags:", wf)   # triggered by a tag push

    @staticmethod
    def _extract_changelog_section(text, version):
        # mirror release.yml's awk: STRING-match the『## [version]』header (so a SemVer +build's `+`
        # is not treated as a regex metachar), take lines until the next『## [』, skip link defs.
        out, grab = [], False
        for ln in text.splitlines():
            if ln.startswith("## [" + version + "]"):
                grab = True
                continue
            if grab and ln.startswith("## ["):
                break
            if grab and re.match(r"^\[[^\]]+\]:", ln):
                continue
            if grab:
                out.append(ln)
        return "\n".join(out).strip()

    def test_release_notes_extraction_handles_build_metadata(self):
        rl = _read(".github/workflows/release.yml")
        # must STRING-match the header, not embed the version into an awk REGEX (where a SemVer
        # +build's `+` would be a metachar → the section never matches → empty release notes).
        self.assertIn('index($0, "## [" v "]")', rl,
                      "release.yml must string-match the CHANGELOG header (index), not regex-match the version")
        self.assertNotIn('$0 ~ "^## ', rl, "release.yml must not embed the version into an awk regex")
        # functional (mirrors the awk): a +build section extracts non-empty
        synthetic = ("# Changelog\n\n## [1.2.3+build.5] - 2026-01-01\n\nnotes body\n\n"
                     "## [1.2.2] - 2025-12-31\nold\n")
        self.assertEqual(self._extract_changelog_section(synthetic, "1.2.3+build.5"), "notes body")
        # and the real CHANGELOG's current version extracts non-empty
        self.assertTrue(self._extract_changelog_section(_read("CHANGELOG.md"), aipairlib.__version__),
                        "the current CHANGELOG section for __version__ must extract non-empty")

    @staticmethod
    def _section_is_dated(text, version):
        # mirror release.yml's date guard: the header must be『## [version] - YYYY-MM-DD』(dated),
        # not『## [version] — unreleased (prepared)』.
        for ln in text.splitlines():
            if ln.startswith("## [" + version + "]"):
                return bool(re.match(r"^ - \d{4}-\d{2}-\d{2}\s*$", ln[len("## [" + version + "]"):]))
        return False

    def test_release_refuses_to_publish_an_undated_section(self):
        rl = _read(".github/workflows/release.yml")
        # release.yml must reject an undated (still 'unreleased'/prepared) heading before publishing,
        # so pushing a tag before dating the CHANGELOG in the release commit fails loudly.
        self.assertIn("is not dated as", rl, "release.yml must verify the section is dated before publishing")
        self.assertIn("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]", rl,
                      "release.yml must check the YYYY-MM-DD date format of the section header")
        # functional (mirrors the awk): dated passes, prepared/undated fails — including +build versions.
        self.assertTrue(self._section_is_dated("## [0.1.0] - 2026-08-23\nx\n", "0.1.0"))
        self.assertFalse(self._section_is_dated("## [0.1.0] — unreleased (prepared)\nx\n", "0.1.0"))
        self.assertTrue(self._section_is_dated("## [1.2.3+build.5] - 2026-01-01\nx\n", "1.2.3+build.5"))
        # a released version's section is dated; a being-prepared one is『## [X.Y.Z] — unreleased』
        # (undated). Both are valid per the release lifecycle, so we don't pin the current state here
        # — the release.yml guard (checked above) is what refuses to PUBLISH an undated section.

    def test_security_release_policy_is_consistent(self):
        # SECURITY.md must not keep the old「タグ付きリリース運用は今のところありません」that now
        # contradicts RELEASING.md / CHANGELOG / the release workflow.
        self.assertNotIn("タグ付きリリース運用は今のところありません", SECURITY,
                         "SECURITY.md still says there is no tagged-release operation — update the 対象バージョン policy")
        # link to the release process, and with the CORRECT relative path (both files are at repo
        # root, so ../RELEASING.md would be broken).
        self.assertIn("(RELEASING.md)", SECURITY,
                      "SECURITY.md must link the release process as (RELEASING.md) — same-dir relative path")
        self.assertNotIn("(../RELEASING.md)", SECURITY, "broken link: SECURITY.md and RELEASING.md are both at repo root")


if __name__ == "__main__":
    unittest.main(verbosity=2)
