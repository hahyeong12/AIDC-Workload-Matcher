"""Read-only fingerprints of all v0.1 rows and schema, without private dumps."""

from contextlib import closing
import hashlib
import json

from database.inspect_schema import definitions, table_signature


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def snapshot(connection):
    result = {}
    with closing(connection.cursor()) as cursor:
        # Names come only from version-controlled schema.sql, never user input.
        for table in definitions()[0]:
            cursor.execute(f"SELECT * FROM `{table}` ORDER BY 1")
            rows = cursor.fetchall()
            cursor.execute(f"SHOW CREATE TABLE `{table}`")
            result[table] = {"rows": len(rows), "data_sha256": fingerprint(rows),
                             "schema_sha256": fingerprint(table_signature(cursor.fetchone()[1]))}
        cursor.execute(
            "SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
            "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE() ORDER BY TRIGGER_NAME")
        triggers = cursor.fetchall()
        result["triggers"] = {"count": len(triggers), "sha256": fingerprint(triggers)}
    return result
