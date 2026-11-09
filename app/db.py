"""Environment-only MySQL connection and a read-only connection check."""

import argparse
from contextlib import closing, contextmanager
from pathlib import Path
import os
import re
import sys

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DatabaseConfigError(ValueError):
    """Missing or unsafe database configuration (never includes values)."""


class DatabaseOperationError(RuntimeError):
    """Safe diagnostic fields only; never retain connector messages or SQL text."""

    def __init__(self, stage, object_name, error):
        self.stage = stage if re.fullmatch(r"[a-z_-]+", stage) else "unknown"
        self.object_name = object_name if re.fullmatch(r"[a-zA-Z0-9_]+", object_name) else "redacted"
        number = getattr(error, "errno", None)
        self.errno = number if type(number) is int else None
        state = getattr(error, "sqlstate", None)
        self.sqlstate = state if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state) else None
        super().__init__(f"stage={self.stage} object={self.object_name} "
                         f"errno={self.errno or 'unknown'} SQLSTATE={self.sqlstate or 'unknown'}")


@contextmanager
def database_step(stage, object_name="database"):
    """Keep diagnostics useful without echoing driver errors or connection values."""
    try:
        yield
    except (DatabaseConfigError, DatabaseOperationError):
        raise
    except Exception as exc:
        raise DatabaseOperationError(stage, object_name, exc) from None


def connection_settings(prefix="MYSQL_", environ=None):
    """Require explicit configuration; do not fall back to developer credentials."""
    values = os.environ if environ is None else environ
    required = ("HOST", "PORT", "DATABASE", "USER", "PASSWORD")
    missing = [prefix + key for key in required if not values.get(prefix + key)]
    if missing:
        raise DatabaseConfigError("Missing environment variables: " + ", ".join(missing))
    try:
        port = int(values[prefix + "PORT"])
    except ValueError:
        raise DatabaseConfigError(prefix + "PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise DatabaseConfigError(prefix + "PORT must be between 1 and 65535")
    database = values[prefix + "DATABASE"]
    if prefix == "TEST_MYSQL_":
        if not re.fullmatch(r"[a-zA-Z0-9_]+_test", database):
            raise DatabaseConfigError("TEST_MYSQL_DATABASE must end in _test")
        if database.casefold() == values.get("MYSQL_DATABASE", "").casefold():
            raise DatabaseConfigError("Test and development database names must differ")
    return {
        "host": values[prefix + "HOST"],
        "port": port,
        "database": database,
        "user": values[prefix + "USER"],
        "password": values[prefix + "PASSWORD"],
        "charset": "utf8mb4",
        # Use the Python implementation consistently across Windows machines;
        # the C extension can fail during authentication with an opaque RuntimeError.
        "use_pure": True,
        "autocommit": False,
        "connection_timeout": 5,
        "time_zone": "+00:00",
        "sql_mode": "STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION",
    }


def connect_database(prefix="MYSQL_"):
    """Open a connection; the caller owns commit, rollback and close."""
    settings = connection_settings(prefix)
    import mysql.connector

    return mysql.connector.connect(**settings)


def require_supported_mysql(version):
    """CHECK enforcement requires MySQL 8.0.16+, not MariaDB."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if "mariadb" in version.lower() or not match:
        raise DatabaseConfigError("MySQL 8.0.16 or later is required")
    if tuple(map(int, match.groups())) < (8, 0, 16):
        raise DatabaseConfigError("MySQL 8.0.16 or later is required")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only MySQL connectivity check")
    parser.add_argument("--test", action="store_true", help="Use TEST_MYSQL_* only")
    args = parser.parse_args(argv)
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        with database_step("connect"):
            connection = connect_database("TEST_MYSQL_" if args.test else "MYSQL_")
        with closing(connection), database_step("connection-check"):
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT VERSION(), 1")
                version, result = cursor.fetchone()
                require_supported_mysql(version)
                if result != 1:
                    raise RuntimeError("Connection check returned an unexpected result")
        print("MySQL connection check passed (read-only; no schema or data changed).")
        return 0
    except DatabaseConfigError as exc:
        print(str(exc), file=sys.stderr)
    except DatabaseOperationError as exc:
        print(str(exc), file=sys.stderr)
    except ImportError:
        print("Install requirements.txt before connecting to MySQL.", file=sys.stderr)
    except Exception:
        # Connector error text can contain usernames, hosts or other private data.
        print("MySQL connection check failed; check local configuration and access.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
