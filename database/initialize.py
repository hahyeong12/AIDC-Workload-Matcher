"""Initialize only an explicitly configured empty DB; never reset an existing DB."""

import argparse
from contextlib import closing
from pathlib import Path
import sys

from dotenv import load_dotenv

from app.db import (
    PROJECT_ROOT,
    DatabaseConfigError,
    connect_database,
    require_supported_mysql,
)


def statements(path):
    """Split these controlled SQL assets (no compound bodies or embedded ';')."""
    content = Path(path).read_text(encoding="utf-8")
    content = "\n".join(line for line in content.splitlines() if not line.lstrip().startswith("--"))
    return [part.strip() for part in content.split(";") if part.strip()]


def initialize(connection):
    """DDL is not rollbackable; stop on any error and never clean up by deletion."""
    with closing(connection.cursor()) as cursor:
        cursor.execute("SELECT VERSION()")
        require_supported_mysql(cursor.fetchone()[0])
        for catalog in ("TABLES", "ROUTINES", "TRIGGERS", "EVENTS"):
            schema_column = {"TABLES": "TABLE_SCHEMA", "ROUTINES": "ROUTINE_SCHEMA",
                             "TRIGGERS": "TRIGGER_SCHEMA", "EVENTS": "EVENT_SCHEMA"}[catalog]
            cursor.execute(
                f"SELECT COUNT(*) FROM information_schema.{catalog} WHERE {schema_column} = DATABASE()"
            )
            if cursor.fetchone()[0]:
                raise DatabaseConfigError("Database is not empty; initialization refused")
        for statement in statements(PROJECT_ROOT / "database" / "schema.sql"):
            cursor.execute(statement)
        # All seed INSERTs share one transaction. An error rolls all seed rows back.
        connection.start_transaction()
        try:
            for statement in statements(PROJECT_ROOT / "database" / "seed.sql"):
                cursor.execute(statement)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Initialize an EMPTY pre-created project database")
    parser.add_argument("--test", action="store_true", help="Use the separate TEST_MYSQL_* database")
    parser.add_argument("--confirm-empty", action="store_true", required=True,
                        help="Confirm this is a dedicated, pre-created empty database")
    args = parser.parse_args(argv)
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        with closing(connect_database("TEST_MYSQL_" if args.test else "MYSQL_")) as connection:
            initialize(connection)
        print("Created 6 tables, 4 preservation triggers, and fictional seed rows.")
        return 0
    except DatabaseConfigError as exc:
        print(str(exc), file=sys.stderr)
    except ImportError:
        print("Install requirements.txt first.", file=sys.stderr)
    except Exception:
        print("Initialization stopped. Inspect the database locally; partial DDL may remain. "
              "No reset or deletion was attempted; seed INSERT errors are rolled back.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
