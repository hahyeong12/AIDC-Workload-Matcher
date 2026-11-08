"""Add only the two v0.2-2 tables, after a strict, version-aware preflight."""

import argparse
from contextlib import closing
import hashlib
import json
import sys

from dotenv import load_dotenv

from app.db import PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError, connect_database, database_step
from database.initialize import execute
from database.inspect_schema import definitions, inspect, table_signature


EXTENSIONS = ("allocations", "allocation_gpus")


def preflight(report, resume=False):
    if report["unexpected_tables"] or report["unexpected_triggers"]:
        raise DatabaseConfigError("Unexpected objects; migration refused")
    for name in definitions()[0]:
        row = report["tables"].get(name, {})
        if not row.get("exists") or not row.get("matches"):
            raise DatabaseConfigError("Base table missing or changed: " + name)
    if not all(row["matches"] for row in report["triggers"].values()) or len(report["triggers"]) != 4:
        raise DatabaseConfigError("All four original triggers must match before migration")
    present = []
    for name in EXTENSIONS:
        row = report["tables"].get(name, {})
        if row.get("exists"):
            if not row.get("matches"):
                raise DatabaseConfigError("Existing allocation table differs: " + name)
            present.append(name)
    if present and len(present) != 2:
        if not resume or present != ["allocations"]:
            raise DatabaseConfigError("Partial migration; inspect then explicitly use --resume")
        if report["tables"]["allocations"]["rows"]:
            raise DatabaseConfigError("Partial migration contains allocation data; manual review required")
    return tuple(name for name in EXTENSIONS if name not in present)


def base_fingerprint(connection, version="v0.1"):
    """Compare all original values and definitions; never emit a data dump."""
    result = {}
    with closing(connection.cursor()) as cursor:
        for name in definitions(version)[0]:
            execute(cursor, f"SELECT * FROM `{name}` ORDER BY 1", "migration-audit", name)
            rows = cursor.fetchall()
            execute(cursor, f"SHOW CREATE TABLE `{name}`", "migration-audit", name)
            result[name] = (rows, table_signature(cursor.fetchone()[1]))
        execute(cursor, "SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, "
                "ACTION_STATEMENT FROM information_schema.TRIGGERS "
                "WHERE TRIGGER_SCHEMA = DATABASE() ORDER BY TRIGGER_NAME", "migration-audit")
        result["triggers"] = cursor.fetchall()
    return hashlib.sha256(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def migrate(connection, apply=False, resume=False):
    if connection.in_transaction:
        raise DatabaseConfigError("Migration requires a fresh connection without an active transaction")
    # Connection-level advisory lock survives implicit DDL commits. All supported
    # migration runners share it. Operators must also stop independent DDL/writes.
    with closing(connection.cursor()) as cursor:
        with database_step("migration-lock"):
            cursor.execute("SELECT GET_LOCK(CONCAT(DATABASE(), %s), 0)", ("_v022_migration",))
            locked = cursor.fetchone()[0] == 1
    if not locked:
        raise DatabaseConfigError("Another migration is running")
    try:
        report = inspect(connection)
        pending = preflight(report, resume)
        with closing(connection.cursor()) as cursor:
            for catalog, column in (("ROUTINES", "ROUTINE_SCHEMA"), ("EVENTS", "EVENT_SCHEMA")):
                execute(cursor, f"SELECT COUNT(*) FROM information_schema.{catalog} "
                        f"WHERE {column} = DATABASE()", "migration-preflight", catalog)
                if cursor.fetchone()[0]:
                    raise DatabaseConfigError("Unexpected routines/events; migration refused")
        before = base_fingerprint(connection)
        connection.rollback()
        if apply:
            sql, _ = definitions("v0.2-2")
            for name in pending:
                with closing(connection.cursor()) as cursor:
                    execute(cursor, sql[name], "migration-create", name)
            report = inspect(connection)
            if preflight(report) or base_fingerprint(connection) != before:
                raise DatabaseConfigError("Post-migration verification failed; preserve objects and inspect")
        return {"applied": bool(apply and pending), "pending_tables": list(pending) if not apply else [],
                "schema_version": report["schema_version"], "original_data_preserved": True}
    finally:
        connection.rollback()
        with closing(connection.cursor()) as cursor:
            with database_step("migration-unlock"):
                cursor.execute("SELECT RELEASE_LOCK(CONCAT(DATABASE(), %s))", ("_v022_migration",))
                cursor.fetchone()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect v0.2-2 migration; writes require --test --apply")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    # No developer-write switch. A separate user-approved procedure is required.
    if args.apply and not args.test:
        parser.error("Development migration requires separate user approval; this CLI applies only with --test")
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
        print("Migration stopped; DDL may remain. Inspect and use --resume only for matching partial tables.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
