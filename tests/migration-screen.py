#!/usr/bin/env python3
"""Fixture tests for aipair-queue's migration allowlist (the safety gate before
`prisma migrate deploy`). Pure SQL screening — no DB, no git, no gh.
    python3 tests/migration-screen.py
"""
import importlib.machinery, importlib.util, os, tempfile, types, unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
loader = importlib.machinery.SourceFileLoader("queue_mig_under_test", os.path.join(BIN, "aipair-queue"))
spec = importlib.util.spec_from_loader("queue_mig_under_test", loader)
queue = importlib.util.module_from_spec(spec)
loader.exec_module(queue)


ALLOWED = [
    ('CREATE TABLE "User" ("id" TEXT NOT NULL, PRIMARY KEY ("id"));', "create table"),
    ('create table if not exists "T" ("id" int);', "create table if not exists, lowercase"),
    ('ALTER TABLE "User" ADD COLUMN "age" INTEGER NOT NULL DEFAULT 0;', "add NOT NULL column WITH default"),
    ('ALTER TABLE "User" ADD COLUMN "bio" TEXT;', "add nullable column"),
    ('ALTER TABLE users ADD COLUMN age INTEGER;', "bare (unquoted) identifiers"),
    ('ALTER TABLE public.users ADD COLUMN "age" INTEGER;', "schema-qualified table name"),
    ('ALTER TABLE "U" ADD COLUMN "a" TEXT, ADD COLUMN "b" INT NOT NULL DEFAULT 0;', "several add-column actions"),
    ("ALTER TABLE users ADD COLUMN tags TEXT[] DEFAULT ARRAY['a','b'];", "array type + array DEFAULT (brackets)"),
    ('CREATE UNIQUE INDEX CONCURRENTLY "u_email" ON "User"("email");', "unique index concurrently"),
    ('CREATE INDEX "i" ON "User"("name");', "plain index"),
    ('CREATE INDEX "i" ON "X" USING gin ("d");', "index USING method"),
    ('CREATE TYPE "Role" AS ENUM (\'A\', \'B\');', "create enum type"),
    ('CREATE TYPE "Comp" AS ("a" int, "b" text);', "composite type"),
    ('CREATE TYPE "Shell";', "shell type (name only)"),
    ('ALTER TYPE "Role" ADD VALUE \'C\';', "grow an enum"),
    ('ALTER TYPE "Role" ADD VALUE \'C\' BEFORE \'B\';', "grow an enum with BEFORE"),
    ('CREATE EXTENSION IF NOT EXISTS "pgcrypto";', "create extension"),
    ('CREATE EXTENSION "uuid-ossp" WITH SCHEMA "public";', "extension with a schema option"),
    ('COMMENT ON TABLE "User" IS \'people\';', "comment on table"),
    ('COMMENT ON COLUMN "t"."c" IS NULL;', "comment set to NULL"),
    ('COMMENT ON TABLE "U" IS $$dollar string$$;', "a dollar-quoted STRING literal is data"),
    ('-- header\nCREATE TABLE "X" ("id" INT);  -- trailing note', "line comments stripped"),
    ('/* block\n comment */ CREATE TABLE "Y" ("id" INT);', "block comment stripped"),
    ('COMMENT ON TABLE "X" IS \'safe -- not a comment\';', "a '--' inside a string literal is data"),
    ('CREATE TABLE "X" ("uid" TEXT REFERENCES "U"("id") ON DELETE CASCADE ON UPDATE CASCADE);', "FK with ON DELETE/UPDATE"),
]

REJECTED = [
    ('DROP TABLE "User";', "drop table"),
    ('ALTER TABLE "User" DROP COLUMN "age";', "drop column"),
    ('ALTER TABLE "User" ALTER COLUMN "age" SET NOT NULL;', "set not null on existing column"),
    ('ALTER TABLE "User" ALTER COLUMN "age" TYPE BIGINT;', "type change"),
    ('ALTER TABLE "User" RENAME COLUMN "a" TO "b";', "rename column"),
    ('TRUNCATE "User";', "truncate"),
    ('DELETE FROM "User";', "delete DML"),
    ('UPDATE "User" SET "x" = 1;', "update DML"),
    ('ALTER TABLE "User" ADD COLUMN "req" TEXT NOT NULL;', "required column with NO default"),
    ('ALTER TABLE users ADD COLUMN age;', "ADD COLUMN with no type"),
    ('ALTER TABLE users ADD COLUMN age INTEGER,;', "trailing comma → empty action"),
    ('ALTER TABLE users ADD COLUMN age INTEGER DROP COLUMN old;', "a DROP glued onto an ADD COLUMN action"),
    ('ALTER TABLE "User" ADD CONSTRAINT "boom" CHECK (false);', "ADD CONSTRAINT is not ADD COLUMN"),
    ('ALTER TYPE "Role" RENAME VALUE \'ADD VALUE\' TO \'x\';', "RENAME VALUE even with the string 'ADD VALUE'"),
    ('ALTER TYPE "Role" ADD VALUE;', "ADD VALUE with no value"),
    ('CREATE TABLE "Z" ("id" INT);\nDROP TABLE "old";', "a forbidden statement among allowed ones"),
    # residual tokens after a balanced canonical form (matched to END)
    ('CREATE TABLE "X" ("id" int) DROP TABLE "Y";', "junk after the column list"),
    ('CREATE INDEX "i" ON "X"("id") DROP TABLE "Y";', "junk after an index"),
    ('CREATE TYPE "Role" AS ENUM (\'A\') DROP TABLE "Y";', "junk after an enum type"),
    ('CREATE EXTENSION "pgcrypto" DROP TABLE "Y";', "junk after CREATE EXTENSION"),
    ('COMMENT ON TABLE "X" IS \'ok\' DROP TABLE "Y";', "junk after COMMENT ON value"),
    # a destructive verb hidden INSIDE a group / as a COMMENT target
    ('CREATE TABLE "X" (DROP TABLE "Y");', "DROP inside the column list"),
    ('CREATE INDEX "i" ON "X"(DROP TABLE "Y");', "DROP inside an index expression"),
    ('COMMENT ON DROP TABLE "X" IS \'ok\';', "DROP as the COMMENT target"),
    # unparseable / other
    ('DO $$ BEGIN PERFORM 1; END $$;', "DO block (leading keyword not allowed)"),
    ('CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql;', "CREATE FUNCTION not on the allowlist"),
    ("SELECT 'unterminated", "unterminated string literal"),
    ("/* unterminated", "unterminated block comment"),
    ('CREATE TABLE "X" ("id" int) /* outer /* inner */', "nested block comment leaves outer unterminated"),
    ('CREATE TABLE "X" ("id" int;', "unbalanced parentheses"),
    ('honk honk not sql', "unparseable garbage"),
]


class ScreenSql(unittest.TestCase):
    def test_allowed_statements_pass(self):
        for sql, label in ALLOWED:
            ok, offending = queue.screen_migration_sql(sql)
            self.assertTrue(ok, f"{label}: unexpectedly rejected {offending}")
            self.assertEqual(offending, [])

    def test_forbidden_statements_are_rejected(self):
        for sql, label in REJECTED:
            ok, offending = queue.screen_migration_sql(sql)
            self.assertFalse(ok, f"{label}: should have been rejected")
            self.assertTrue(offending, f"{label}: offending list should name the statement")

    def test_empty_and_comment_only_are_vacuously_ok(self):
        for sql in ("", "   \n\t", "-- just a comment\n", "/* only a block */"):
            ok, offending = queue.screen_migration_sql(sql)
            self.assertTrue(ok); self.assertEqual(offending, [])

    def test_one_bad_statement_taints_the_file_and_is_reported(self):
        ok, offending = queue.screen_migration_sql(
            'CREATE TABLE "A" ("id" INT);\nALTER TABLE "A" DROP COLUMN "id";\nCREATE INDEX "i" ON "A"("id");')
        self.assertFalse(ok)
        self.assertEqual(len(offending), 1)
        self.assertIn("DROP COLUMN", offending[0].upper())

    def test_semicolon_inside_a_string_is_not_a_split(self):
        ok, _ = queue.screen_migration_sql("COMMENT ON TABLE \"U\" IS 'a; b; c';")
        self.assertTrue(ok, "a ';' inside a quoted literal must not split the statement")


class ScreenTexts(unittest.TestCase):
    def test_all_safe_texts_pass(self):
        ok, reason = queue.screen_migration_texts([
            ("001/migration.sql", 'CREATE TABLE "A" ("id" INT);'),
            ("002/migration.sql", 'ALTER TABLE "A" ADD COLUMN "b" TEXT;')])
        self.assertTrue(ok, reason); self.assertEqual(reason, "")

    def test_one_unsafe_text_holds_the_task_and_names_it(self):
        ok, reason = queue.screen_migration_texts([
            ("001/migration.sql", 'CREATE TABLE "A" ("id" INT);'),
            ("002/migration.sql", 'DROP TABLE "A";')])
        self.assertFalse(ok)
        self.assertIn("002", reason); self.assertIn("allowlist", reason)


class PendingSet(unittest.TestCase):
    """The gate must screen the DEPLOY set (every pending migration), not just the PR diff:
    `prisma migrate deploy` applies all unapplied migrations."""
    def test_parse_pending_names(self):
        txt = ("Following migrations have not yet been applied:\n"
               "20230101000000_init\n20230102000000_add_col\n\nTo apply migrations run:\nnpx prisma migrate deploy\n")
        names, why = queue.parse_pending_migrations(1, txt)
        self.assertEqual(names, ["20230101000000_init", "20230102000000_add_col"]); self.assertEqual(why, "")

    def test_parse_up_to_date_requires_rc0_and_exact_line(self):
        self.assertEqual(queue.parse_pending_migrations(0, "Database schema is up to date!\n"), ([], ""))
        # substring 'up to date' inside a NEGATIVE message must NOT count as clean
        names, _ = queue.parse_pending_migrations(1, "Database schema is not up to date!")
        self.assertIsNone(names, "'…is not up to date!' is not up-to-date")
        # the exact up-to-date line but a non-zero exit → hold
        self.assertIsNone(queue.parse_pending_migrations(1, "Database schema is up to date!")[0])

    def test_parse_unknown_holds(self):
        names, why = queue.parse_pending_migrations(0, "something unexpected happened")
        self.assertIsNone(names); self.assertTrue(why)

    def test_parse_partial_list_fails_closed(self):
        txt = "Following migrations have not yet been applied:\n001_safe\nUNEXPECTED LINE!\n"
        names, why = queue.parse_pending_migrations(1, txt)
        self.assertIsNone(names, "an unclassifiable line must hold, not silently skip"); self.assertTrue(why)

    def test_parse_non_ascii_name_fails_closed(self):
        names, _ = queue.parse_pending_migrations(1, "have not yet been applied:\n001_\u30c6\u30b9\u30c8\n")
        self.assertIsNone(names)

    def test_parse_anomaly_after_list_fails_closed(self):
        txt = "have not yet been applied:\n001_a\n\nERROR: listing was truncated\n"
        names, _ = queue.parse_pending_migrations(1, txt)
        self.assertIsNone(names, "an error/truncation after the list must hold")

    def test_migrate_status_clean_needs_rc0(self):
        self.assertTrue(queue.migrate_status_clean(0, "Database schema is up to date!"))
        self.assertFalse(queue.migrate_status_clean(1, "Database schema is up to date!"))
        self.assertFalse(queue.migrate_status_clean(0, "Following migrations have not yet been applied:\n001\n"))

    def test_clean_line_with_a_trailing_error_is_not_clean(self):
        self.assertFalse(queue.migrate_status_clean(0, "Database schema is up to date!\nERROR: connection failed"))
        self.assertIsNone(queue.parse_pending_migrations(0, "Database schema is up to date!\nERROR: connection failed")[0])

    def test_pending_requires_the_expected_exit_code(self):
        for rc in (-1, 0, 2, 137):
            names, _ = queue.parse_pending_migrations(rc, "Following migrations have not yet been applied:\n001_safe\n")
            self.assertIsNone(names, f"rc={rc} with a pending list must hold")
        names, _ = queue.parse_pending_migrations(1, "have not yet been applied:\n001_safe\n\nprisma migrate deploy")
        self.assertEqual(names, ["001_safe"])

    def test_footer_is_matched_exactly_not_by_substring(self):
        txt = "have not yet been applied:\n001_a\n\nWarning: migrate deploy could not list all migrations"
        self.assertIsNone(queue.parse_pending_migrations(1, txt)[0], "a Warning footer must hold")

    def test_footer_trailing_sentence_holds(self):
        txt = "have not yet been applied:\n001_a\n\nTo apply only the listed migrations; others were omitted"
        self.assertIsNone(queue.parse_pending_migrations(1, txt)[0], "an arbitrary 'To apply …' sentence must hold")
        good = "have not yet been applied:\n001_a\n\nTo apply migrations run:\nprisma migrate deploy"
        self.assertEqual(queue.parse_pending_migrations(1, good)[0], ["001_a"])

    def test_real_prisma_footers_pass_and_shell_commands_hold(self):
        mk = lambda foot: "have not yet been applied:\n001_a\n\n" + foot
        self.assertEqual(queue.parse_pending_migrations(
            1, mk("To apply migrations in production run prisma migrate deploy."))[0], ["001_a"])
        for bad in ("$ prisma migrate reset --force", "$ rm -rf /", "$ prisma migrate deploy && curl evil"):
            self.assertIsNone(queue.parse_pending_migrations(1, mk(bad))[0], bad)

    def test_real_two_line_dev_and_prod_footer_passes(self):
        txt = ("have not yet been applied:\n001_a\n\n"
               "To apply migrations in development run prisma migrate dev.\n"
               "To apply migrations in production run prisma migrate deploy.")
        self.assertEqual(queue.parse_pending_migrations(1, txt)[0], ["001_a"])

    def test_db_state_diagnostics_before_the_list_hold(self):
        for diag in ("migrate found failed migrations in the target database",
                     "The migration history is different from the database",
                     "migration history diverged", "drift detected",
                     "not found locally", "not in a valid state"):
            txt = diag + "\n\nFollowing migrations have not yet been applied:\n001_a\n\nprisma migrate deploy"
            self.assertIsNone(queue.parse_pending_migrations(1, txt)[0], diag)

    def test_anomaly_does_not_flag_migration_names_or_hosts(self):
        # a name/host that merely contains "fail" must not read as an error line
        names, _ = queue.parse_pending_migrations(
            1, "have not yet been applied:\n20260822000000_add_failed_login_count\n\nprisma migrate deploy")
        self.assertEqual(names, ["20260822000000_add_failed_login_count"])
        self.assertTrue(queue.migrate_status_clean(
            0, 'Datasource "db" at db-failover.example.com\nDatabase schema is up to date!'))

    def test_loopback_guard(self):
        loopback = ("postgres://u@localhost/db", "postgres://u@LOCALHOST/db",
                    "postgres://u@127.0.0.1/db", "postgres://u@127.0.0.2/db", "postgres://u@[::1]/db",
                    "postgres://u@localhost./db", "postgres://u@foo.localhost/db",
                    "postgres://u@127.1/db", "postgres://u@2130706433/db",
                    "postgres://u@local%68ost/db", "postgresql://db.invalid,localhost/db",
                    "postgresql://%2Fvar%2Frun%2Fpostgresql/app", "postgresql://%2Ftmp/app",
                    "postgres://u@0.0.0.0/db", "postgres://u@[::]/db")
        for url in loopback:
            self.assertTrue(queue._url_is_loopback(url), url)
        # libpq/Prisma query params can redirect to a local socket/address regardless of netloc
        for url in ("postgresql://db.prod.example/app?host=/tmp",
                    "postgresql://db.prod.example/app?host=%2Fvar%2Frun%2Fpostgresql",
                    "postgresql://db.prod.example/app?hostaddr=127.0.0.1",
                    "postgresql://db.prod.example/app?hostaddr=::1",
                    # multi-valued and blank query hosts (libpq: blank host = default socket)
                    "postgresql://db.prod/app?host=db.prod,localhost",
                    "postgresql://db.prod/app?host=db.prod,%2Ftmp",
                    "postgresql://db.prod/app?hostaddr=10.0.0.5,127.0.0.1",
                    "postgresql://db.prod/app?host="):
            self.assertTrue(queue._url_is_loopback(url), url)
        for url in ("postgres://u@db.prod.example.com:5432/db", "postgres://u@10.0.0.5/db",
                    "postgresql://a.example.com,b.example.com/db",
                    "postgresql://db.prod.example/app?sslmode=require",
                    "postgresql://db.prod.example/app?host=db.prod.example",
                    "postgresql://db.prod/app?host=db.prod,other.prod"):
            self.assertFalse(queue._url_is_loopback(url), url)

    def test_prod_env_non_utf8_holds(self):
        tmp = tempfile.TemporaryDirectory(prefix="aipair-envprod.")
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, ".env.production"), "wb") as fh:
            fh.write(b"DATABASE_URL=postgres://u@h/db\n\xff\xfe bad bytes\n")
        env, reason = queue.prod_env(tmp.name)
        self.assertIsNone(env); self.assertIn("UTF-8", reason)

    def test_prod_env_rejects_loopback(self):
        tmp = tempfile.TemporaryDirectory(prefix="aipair-envprod.")
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, ".env.production"), "w") as fh:
            fh.write("DATABASE_URL=postgres://u@127.0.0.2/db\nDATABASE_URL_UNPOOLED=postgres://u@127.0.0.2/db\n")
        env, reason = queue.prod_env(tmp.name)
        self.assertIsNone(env); self.assertIn("loopback", reason)

    def test_screen_pending_non_utf8_holds(self):
        tmp = tempfile.TemporaryDirectory(prefix="aipair-pending.")
        self.addCleanup(tmp.cleanup)
        d = os.path.join(tmp.name, "prisma", "migrations", "001_x"); os.makedirs(d)
        with open(os.path.join(d, "migration.sql"), "wb") as fh:
            fh.write(b"CREATE TABLE \xff;")           # invalid UTF-8
        with mock.patch.object(queue, "run",
                               return_value=(1, "have not yet been applied:\n001_x\n")):
            ok, reason = queue.screen_pending_migrations(tmp.name, {})
        self.assertFalse(ok); self.assertIn("UTF-8", reason)

    def test_screen_pending_reads_the_whole_deploy_set(self):
        tmp = tempfile.TemporaryDirectory(prefix="aipair-pending.")
        self.addCleanup(tmp.cleanup)
        for name, sql in [("001_safe", 'CREATE TABLE "A" ("id" INT);'),
                          ("002_evil", 'DROP TABLE "A";')]:       # pre-existing destructive one
            d = os.path.join(tmp.name, "prisma", "migrations", name); os.makedirs(d)
            with open(os.path.join(d, "migration.sql"), "w") as fh:
                fh.write(sql)
        status = "Following migrations have not yet been applied:\n001_safe\n002_evil\n"
        with mock.patch.object(queue, "run", return_value=(1, status)):
            ok, reason = queue.screen_pending_migrations(tmp.name, {"DATABASE_URL": "x"})
        self.assertFalse(ok, "a pre-existing pending DROP (not in the PR diff) must be caught")
        self.assertIn("002_evil", reason)

    def test_screen_pending_all_safe(self):
        tmp = tempfile.TemporaryDirectory(prefix="aipair-pending.")
        self.addCleanup(tmp.cleanup)
        d = os.path.join(tmp.name, "prisma", "migrations", "001_safe"); os.makedirs(d)
        with open(os.path.join(d, "migration.sql"), "w") as fh:
            fh.write('CREATE TABLE "A" ("id" INT);')
        with mock.patch.object(queue, "run",
                               return_value=(1, "Following migrations have not yet been applied:\n001_safe\n")):
            ok, reason = queue.screen_pending_migrations(tmp.name, {})
        self.assertTrue(ok, reason)


class MergePhaseHeadPinning(unittest.TestCase):
    """The SHA gate must fail CLOSED: an unreadable head, a moved head, or a merge without a
    head match must never let a different commit be deployed or merged."""
    def _args(self):
        return types.SimpleNamespace(no_merge=False, allow_unsafe_migrations=True,
                                     ci_timeout=1, dir=".")

    def _patch(self, head_seq, merged=False):
        """Drive merge_phase with scripted gh/git; head_seq feeds successive pr_head_sha()."""
        import itertools
        calls = {"merge_args": []}
        heads = iter(head_seq)
        def fake_gh(args, cwd, **kw):
            a = args
            if a[:2] == ["pr", "list"]:
                return 0, "42\nhttps://example/pr/42"
            if a[:2] == ["pr", "view"] and "headRefOid" in a:
                try: return 0, next(heads)
                except StopIteration: return 0, head_seq[-1]
            if a[:2] == ["pr", "checks"]:
                return 0, "all green"
            if a[:2] == ["pr", "diff"]:
                return 0, "src/app.ts"          # no migrations → skip deploy path
            if a[:2] == ["pr", "merge"]:
                calls["merge_args"].append(a)
                return (0, "merged") if merged else (1, "not merged")
            if a[:2] == ["pr", "view"]:
                # jq returns "state\theadRefOid"
                merged_head = getattr(self, "_merged_head", head_seq[-1])
                if merged:
                    return 0, f"MERGED\t{merged_head}"
                return 0, "OPEN\t" + head_seq[-1]
            return 0, ""
        def fake_git(args, cwd, **kw):
            if args[:1] == ["branch"]:
                return 0, "feature/x"
            return 0, ""
        return calls, fake_gh, fake_git

    def run_phase(self, head_seq, merged=False):
        calls, fake_gh, fake_git = self._patch(head_seq, merged)
        with mock.patch.object(queue, "gh", side_effect=fake_gh), \
             mock.patch.object(queue, "gh_retry", side_effect=fake_gh), \
             mock.patch.object(queue, "git", side_effect=fake_git), \
             mock.patch.object(queue, "log"), mock.patch.object(queue, "ok"), \
             mock.patch.object(queue, "warn"), mock.patch.object(queue.time, "sleep"):
            res = queue.merge_phase(self._args(), ".")
        return res, calls

    def test_unreadable_initial_head_holds(self):
        (okk, note, reason), _ = self.run_phase([""])
        self.assertFalse(okk); self.assertIn("head", reason)

    def test_head_moved_before_merge_holds_and_never_merges(self):
        # head at detect = A, but changed to B at the pre-merge recheck (no migrations →
        # the only rechecks are the initial detect and the pre-merge `final`)
        (okk, note, reason), calls = self.run_phase(["aaaaaaa", "bbbbbbb"])
        self.assertFalse(okk); self.assertIn("更新", reason)
        self.assertEqual(calls["merge_args"], [], "must not attempt a merge after head moved")

    def test_head_recheck_failure_holds(self):
        (okk, note, reason), calls = self.run_phase(["aaaaaaa", ""])
        self.assertFalse(okk); self.assertIn("再確認に失敗", reason)
        self.assertEqual(calls["merge_args"], [])

    def test_stable_head_merges_with_match_head_commit(self):
        self._merged_head = "aaaaaaa"
        (okk, note, reason), calls = self.run_phase(["aaaaaaa"] * 4, merged=True)
        self.assertTrue(okk, reason)
        self.assertTrue(calls["merge_args"], "a merge was attempted")
        for a in calls["merge_args"]:
            self.assertIn("--match-head-commit", a)
            self.assertEqual(a[a.index("--match-head-commit") + 1], "aaaaaaa")

    def test_migration_pr_screens_pending_and_deploys_with_shared_env(self):
        # a migration-touching PR must run: checkout head → prod_env once → screen pending → deploy
        seen = {"deploy_env": None, "screen_env": None}
        self._merged_head = "aaaaaaa"
        calls, fake_gh, fake_git = self._patch(["aaaaaaa"] * 4, merged=True)
        def gh_with_mig(args, cwd, **kw):
            if args[:2] == ["pr", "diff"]:
                return 0, "prisma/migrations/001_x/migration.sql"
            return fake_gh(args, cwd, **kw)
        with mock.patch.object(queue, "gh", side_effect=gh_with_mig), \
             mock.patch.object(queue, "gh_retry", side_effect=gh_with_mig), \
             mock.patch.object(queue, "git", side_effect=fake_git), \
             mock.patch.object(queue, "pr_head_sha", return_value="aaaaaaa"), \
             mock.patch.object(queue, "checkout_detached", return_value=(True, "")), \
             mock.patch.object(queue, "prod_env", return_value=({"DATABASE_URL_UNPOOLED": "postgres://h/db"}, "")), \
             mock.patch.object(queue, "screen_pending_migrations", side_effect=lambda c, e: (seen.__setitem__("screen_env", e) or (True, ""))), \
             mock.patch.object(queue, "deploy_migrations", side_effect=lambda c, e: (seen.__setitem__("deploy_env", e) or (True, ""))), \
             mock.patch.object(queue, "log"), mock.patch.object(queue, "ok"), \
             mock.patch.object(queue, "warn"), mock.patch.object(queue.time, "sleep"):
            okk, note, reason = queue.merge_phase(self._args_merge(), ".")
        self.assertTrue(okk, reason)
        self.assertIs(seen["screen_env"], seen["deploy_env"], "same env object for screen and deploy")
        self.assertIsNotNone(seen["deploy_env"])

    def _args_merge(self):
        return types.SimpleNamespace(no_merge=False, allow_unsafe_migrations=False, ci_timeout=1, dir=".")

    def test_foreign_merge_of_a_different_head_is_not_success(self):
        # state becomes MERGED, but the merged head is B, not our pinned A → hold, not ok
        self._merged_head = "bbbbbbb"
        (okk, note, reason), _ = self.run_phase(["aaaaaaa"] * 4, merged=True)
        self.assertFalse(okk, "a MERGED state with a different head must not count as our merge")
        self.assertIn("外部", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
