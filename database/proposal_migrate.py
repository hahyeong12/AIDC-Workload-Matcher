"""Add only v0.3-1 tables; developer apply remains unavailable from the CLI."""

import argparse
from contextlib import closing
import json
import sys

from dotenv import load_dotenv

from app.db import PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError, connect_database, database_step
from database.initialize import execute
from database.inspect_schema import definitions, inspect
from database.migrate import base_fingerprint, preflight as allocation_preflight


EXTENSIONS = ("job_proposals", "proposal_responses")


def preflight(report, resume=False):
    if allocation_preflight(report):
        raise DatabaseConfigError("Complete v0.2-2 schema required before v0.3-1")
    present = []
    for name in EXTENSIONS:
        row = report["tables"].get(name, {})
        if row.get("exists"):
            if not row.get("matches"):
                raise DatabaseConfigError("Proposal table differs: " + name)
            present.append(name)
    if present and len(present) != 2:
        if not resume or present != [EXTENSIONS[0]]:
            raise DatabaseConfigError("Partial v0.3-1; inspect before explicit --resume")
        if report["tables"][EXTENSIONS[0]]["rows"]:
            raise DatabaseConfigError("Partial v0.3-1 contains data; preserve and inspect")
    return tuple(name for name in EXTENSIONS if name not in present)


def migrate(connection, apply=False, resume=False):
    if connection.in_transaction:
        raise DatabaseConfigError("Migration requires a fresh connection")
    # Same advisory lock as 002 so supported migration runners cannot overlap.
    with closing(connection.cursor()) as cursor, database_step("proposal-migration-lock"):
        cursor.execute("SELECT GET_LOCK(CONCAT(DATABASE(), %s), 0)", ("_v022_migration",))
        if cursor.fetchone()[0] != 1:
            raise DatabaseConfigError("Another migration is running")
    try:
        report = inspect(connection)
        pending = preflight(report, resume)
        with closing(connection.cursor()) as cursor:
            for catalog, column in (("ROUTINES", "ROUTINE_SCHEMA"), ("EVENTS", "EVENT_SCHEMA")):
                execute(cursor, f"SELECT COUNT(*) FROM information_schema.{catalog} "
                        f"WHERE {column} = DATABASE()", "proposal-preflight", catalog)
                if cursor.fetchone()[0]:
                    raise DatabaseConfigError("Unexpected routines/events; migration refused")
        before = base_fingerprint(connection, "v0.2-2")
        connection.rollback()
        if apply:
            sql, _ = definitions("v0.3-1")
            for name in pending:
                with closing(connection.cursor()) as cursor:
                    execute(cursor, sql[name], "proposal-create", name)
            report = inspect(connection)
            if preflight(report) or base_fingerprint(connection, "v0.2-2") != before:
                raise DatabaseConfigError("Post-migration audit failed; preserve objects and inspect")
        return {"applied": bool(apply and pending), "pending_tables": [] if apply else list(pending),
                "schema_version": report["schema_version"], "original_data_preserved": True}
    finally:
        connection.rollback()
        with closing(connection.cursor()) as cursor, database_step("proposal-migration-unlock"):
            cursor.execute("SELECT RELEASE_LOCK(CONCAT(DATABASE(), %s))", ("_v022_migration",))
            cursor.fetchone()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect v0.3-1; apply only with --test --apply")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.test:
        parser.error("Development migration requires separate user approval; only --test --apply is supported")
    if args.resume and not args.apply:
        parser.error("--resume requires --apply")
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        with database_step("connect"):
            connection = connect_database("TEST_MYSQL_" if args.test else "MYSQL_")
        with closing(connection):
            result = migrate(connection, args.apply, args.resume)
        print(json.dumps(result, indent=2))
        return 0
    except (DatabaseConfigError, DatabaseOperationError) as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("Proposal migration stopped; preserve partial DDL and inspect before explicit resume.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
