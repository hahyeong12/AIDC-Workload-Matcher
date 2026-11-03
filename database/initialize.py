"""Initialize only an explicitly configured empty DB; never reset an existing DB."""

import argparse
from contextlib import closing
from pathlib import Path
import sys

from dotenv import load_dotenv

from app.db import (
    PROJECT_ROOT,
    DatabaseConfigError,
    DatabaseOperationError,
    connect_database,
    database_step,
    require_supported_mysql,
)


def statements(path):
    """Split these controlled SQL assets (no compound bodies or embedded ';')."""
    content = Path(path).read_text(encoding="utf-8")
    content = "\n".join(line for line in content.splitlines() if not line.lstrip().startswith("--"))
    return [part.strip() for part in content.split(";") if part.strip()]


def execute(cursor, statement, stage, object_name="database"):
    with database_step(stage, object_name):
        cursor.execute(statement)


def object_name(statement):
    """Only trusted repository SQL assets supply names for diagnostics."""
    return statement.split()[2]


def initialize(connection, tables_only=False):
    """DDL is not rollbackable; stop on any error and never clean up by deletion."""
    with closing(connection.cursor()) as cursor:
        execute(cursor, "SELECT VERSION()", "preflight")
        require_supported_mysql(cursor.fetchone()[0])
        for catalog in ("TABLES", "ROUTINES", "TRIGGERS", "EVENTS"):
            schema_column = {"TABLES": "TABLE_SCHEMA", "ROUTINES": "ROUTINE_SCHEMA",
                             "TRIGGERS": "TRIGGER_SCHEMA", "EVENTS": "EVENT_SCHEMA"}[catalog]
            execute(cursor, f"SELECT COUNT(*) FROM information_schema.{catalog} "
                    f"WHERE {schema_column} = DATABASE()", "preflight", catalog)
            if cursor.fetchone()[0]:
                raise DatabaseConfigError("Database is not empty; initialization refused")
        for statement in statements(PROJECT_ROOT / "database" / "schema.sql"):
            if tables_only and statement.startswith("CREATE TRIGGER"):
                continue
            stage = "create-table" if statement.startswith("CREATE TABLE") else "create-trigger"
            execute(cursor, statement, stage, object_name(statement))
        if tables_only:
            return
        # All seed INSERTs share one transaction. An error rolls all seed rows back.
        with database_step("seed-begin"):
            connection.start_transaction()
        try:
            for statement in statements(PROJECT_ROOT / "database" / "seed.sql"):
                execute(cursor, statement, "seed-insert", object_name(statement))
            with database_step("seed-commit"):
                connection.commit()
        except Exception:
            with database_step("seed-rollback"):
                connection.rollback()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Initialize an EMPTY pre-created project database")
    parser.add_argument("--test", action="store_true", help="Use the separate TEST_MYSQL_* database")
    parser.add_argument("--tables-only", action="store_true",
                        help="Prepare tables only; no triggers or seed (administrator workflow)")
    parser.add_argument("--confirm-empty", action="store_true", required=True,
                        help="Confirm this is a dedicated, pre-created empty database")
    args = parser.parse_args(argv)
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        with database_step("connect"):
            connection = connect_database("TEST_MYSQL_" if args.test else "MYSQL_")
        with closing(connection), database_step("initialize"):
            initialize(connection, tables_only=args.tables_only)
        if args.tables_only:
            print("Prepared 6 tables only. Triggers and seed are NOT installed; recovery is required.")
        else:
            print("Created 6 tables, 4 preservation triggers, and fictional seed rows.")
        return 0
    except DatabaseConfigError as exc:
        print(str(exc), file=sys.stderr)
    except DatabaseOperationError as exc:
        print(str(exc), file=sys.stderr)
        if exc.errno == 1419:
            print("Trigger creation requires administrator action under current binary logging policy. "
                  "Use the recovery inspection/export workflow; do not reset the database.", file=sys.stderr)
        print("Stopped; partial DDL may remain. No objects or existing rows were deleted.", file=sys.stderr)
    except ImportError:
        print("Install requirements.txt first.", file=sys.stderr)
    except Exception:
        print("Initialization stopped. Inspect the database locally; partial DDL may remain. "
              "No reset or deletion was attempted; seed INSERT errors are rolled back.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
