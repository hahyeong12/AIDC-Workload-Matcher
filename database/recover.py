"""Inspect by default; add missing triggers and optionally seed empty tables only."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sys

from dotenv import load_dotenv

from app.db import PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError, connect_database, database_step
from database.initialize import execute, object_name, statements
from database.inspect_schema import definitions, inspect, require_matching_tables


def export_admin_sql(connection, report, output):
    require_matching_tables(report)
    with closing(connection.cursor()) as cursor:
        execute(cursor, "SELECT DATABASE()", "export-admin")
        database = cursor.fetchone()[0]
    if not re.fullmatch(r"[a-zA-Z0-9_]+", database):
        raise DatabaseConfigError("Cannot safely export this database identifier")
    _, triggers = definitions()
    lines = ["-- Generated snapshot: only triggers missing during read-only inspection.",
             "-- Administrator: review the selected schema and enable stop-on-error in Workbench.",
             "-- No accounts, passwords, grants, global settings, seed, or replacement statements.",
             "-- Re-inspect/re-export if the database has changed. Do not execute schema.sql again.",
             f"USE `{database}`;", "SELECT DATABASE();"]
    for name, row in report["triggers"].items():
        if not row["exists"]:
            sql = triggers[name]
            table = re.search(r" ON (\w+) FOR EACH ROW", sql).group(1)
            sql = sql.replace("CREATE TRIGGER " + name, f"CREATE TRIGGER `{database}`.`{name}`", 1)
            sql = sql.replace(" ON " + table + " FOR EACH ROW", f" ON `{database}`.`{table}` FOR EACH ROW", 1)
            lines.append(sql + ";")
    path = Path(output).resolve()
    if not path.is_relative_to(PROJECT_ROOT / "database") or path.suffix != ".sql":
        raise DatabaseConfigError("Admin SQL output must be a .sql file inside database/")
    content = "\n\n".join(lines) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return
        raise DatabaseConfigError("Admin SQL output exists with different content; choose a new filename")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def recover(connection, seed_if_empty=False):
    report = inspect(connection)
    require_matching_tables(report)
    _, triggers = definitions()
    with closing(connection.cursor()) as cursor:
        for name, row in report["triggers"].items():
            if not row["exists"]:
                execute(cursor, triggers[name], "create-trigger", name)
    # Reinspect rather than assuming success. No seed while protection is incomplete.
    report = inspect(connection)
    require_matching_tables(report)
    if not all(row["matches"] for row in report["triggers"].values()):
        raise DatabaseConfigError("All preservation triggers must match before seeding")
    if seed_if_empty:
        connection.rollback()  # End only this CLI's metadata read transaction.
        with database_step("seed-begin"):
            connection.start_transaction(isolation_level="SERIALIZABLE")
        try:
            with closing(connection.cursor()) as cursor:
                # Under SERIALIZABLE, these reads lock ranges to protect the empty check.
                for name in report["tables"]:
                    execute(cursor, f"SELECT COUNT(*) FROM `{name}`", "seed-empty-check", name)
                    if cursor.fetchone()[0]:
                        raise DatabaseConfigError("Seed refused: existing rows found in " + name)
                for sql in statements(PROJECT_ROOT / "database" / "seed.sql"):
                    execute(cursor, sql, "seed-insert", object_name(sql))
            with database_step("seed-commit"):
                connection.commit()
        except Exception:
            with database_step("seed-rollback"):
                connection.rollback()
            raise
    return inspect(connection)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only schema audit unless --apply is explicit")
    parser.add_argument("--test", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="Create missing triggers only")
    group.add_argument("--export-admin-sql", metavar="PATH", help="Export missing triggers after read-only audit")
    parser.add_argument("--seed-if-empty", action="store_true", help="With --apply only, seed entirely empty tables")
    args = parser.parse_args(argv)
    if args.seed_if_empty and not args.apply:
        parser.error("--seed-if-empty requires --apply")
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        with database_step("connect"):
            connection = connect_database("TEST_MYSQL_" if args.test else "MYSQL_")
        with closing(connection), database_step("recovery"):
            report = recover(connection, args.seed_if_empty) if args.apply else inspect(connection)
            if args.export_admin_sql:
                export_admin_sql(connection, report, args.export_admin_sql)
                print("Exported missing-trigger SQL; administrator execution has NOT occurred.")
            print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0
    except (DatabaseConfigError, DatabaseOperationError) as exc:
        print(str(exc), file=sys.stderr)
        if isinstance(exc, DatabaseOperationError) and exc.errno == 1419:
            print("Administrator must create missing triggers in Workbench; do not change global settings.", file=sys.stderr)
    except Exception:
        print("Recovery stopped; no reset or deletion attempted. Inspect the database before retrying.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
