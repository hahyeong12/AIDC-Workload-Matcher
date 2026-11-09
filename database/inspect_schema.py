"""Read-only inventory and conservative comparison with the controlled SQL assets."""

from contextlib import closing
import re

from app.db import PROJECT_ROOT, DatabaseConfigError, database_step, require_supported_mysql
from database.initialize import statements, execute, object_name


def definitions(version="v0.1"):
    sql = statements(PROJECT_ROOT / "database" / "schema.sql")
    if version in ("v0.2-2", "v0.3-1", "v0.3-2"):
        sql += statements(PROJECT_ROOT / "database" / "migrations" / "002_transactional_allocations.sql")
    elif version != "v0.1":
        raise DatabaseConfigError("Unknown schema version")
    if version in ("v0.3-1", "v0.3-2"):
        sql += statements(PROJECT_ROOT / "database" / "migrations" / "003_workload_proposals.sql")
    if version == "v0.3-2":
        sql += statements(PROJECT_ROOT / "database" / "migrations" / "004_allocation_bases.sql")
    return ({object_name(s): s for s in sql if s.startswith("CREATE TABLE")},
            {object_name(s): s for s in sql if s.startswith("CREATE TRIGGER")})


def split_items(text):
    """Split a controlled CREATE TABLE body at unquoted, top-level commas."""
    result, start, depth, quote, escaped = [], 0, 0, None, False
    for pos, char in enumerate(text):
        if escaped:
            escaped = False
        elif quote:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(text[start:pos].strip())
            start = pos + 1
    if quote or depth:
        raise DatabaseConfigError("Cannot compare unfamiliar table definition; no repair attempted")
    return result + [text[start:].strip()]


def normalize(text):
    # Preserve literal case/content; remove only server-generated syntax differences.
    tokens = re.findall(r"'(?:''|\\.|[^'])*'|`[^`]*`|[a-zA-Z_][a-zA-Z_0-9]*|\d+|[^\s]", text)
    parts = []
    for token in tokens:
        if token.startswith("'"):
            parts.append(token)
        elif token.lower() in ("_utf8mb4", "_ascii"):
            continue
        else:
            parts.append(token.strip("`").lower())
    text = " ".join(parts)
    text = re.sub(r"\b(bigint|int|smallint|tinyint) \( \d+ \)", r"\1", text)
    return re.sub(r"\bcurrent_timestamp \( \)", "current_timestamp", text)


def strip_outer(tokens):
    while tokens.startswith("( ") and tokens.endswith(" )"):
        depth = 0
        for pos, token in enumerate(tokens.split()):
            depth += (token == "(") - (token == ")")
            if depth == 0:
                break
        if pos != len(tokens.split()) - 1:
            break
        tokens = tokens[2:-2]
    return tokens


def table_signature(sql):
    opening = sql.index("(")
    closing = sql.rindex(")")
    columns, constraints = [], []
    for item in split_items(sql[opening + 1:closing]):
        item = normalize(item)
        item = re.sub(r"^constraint (\w+) unique ", r"unique key \1 ", item)
        item = re.sub(r"^index ", "key ", item)
        if " foreign key " in item:
            # SHOW CREATE reverses the order of the two RESTRICT actions.
            item = re.sub(r" on (?:update|delete) restrict", "", item)
            item += " on delete restrict on update restrict"
        if " check " in item:
            prefix, expr = item.split(" check ", 1)
            item = prefix + " check " + strip_outer(expr)
        if " generated always as " in item:
            prefix, expression = item.split(" generated always as ", 1)
            expression, storage = expression.rsplit(" ", 1)
            item = prefix + " generated always as " + strip_outer(expression) + " " + storage
        if item.startswith(("primary key ", "unique key ", "key ", "constraint ")):
            constraints.append(item)
        else:
            columns.append(item)
    options = normalize(sql[closing + 1:].rstrip(";"))
    options = re.sub(r"\bauto_increment = \d+ ?", "", options).strip()
    return columns, sorted(constraints), options


def trigger_signature(name, sql):
    match = re.fullmatch(r"CREATE TRIGGER \w+\s+(BEFORE|AFTER) (UPDATE|DELETE|INSERT) ON (\w+) "
                         r"FOR EACH ROW\s+(.+)", sql, re.S)
    if not match:
        raise DatabaseConfigError("Unsupported trigger SQL asset")
    timing, event, table, action = match.groups()
    return table, timing, event, normalize(action)


def inspect(connection):
    """Return only fixed project object names, counts, and comparison results."""
    table_sql, trigger_sql = definitions()
    report = {"tables": {}, "triggers": {}, "unexpected_tables": 0, "unexpected_triggers": 0}
    with closing(connection.cursor()) as cursor:
        execute(cursor, "SELECT VERSION(), @@GLOBAL.log_bin, @@GLOBAL.log_bin_trust_function_creators", "inspect-server")
        version, log_bin, trust = cursor.fetchone()
        require_supported_mysql(version)
        report.update(version=version, log_bin=log_bin, trust_function_creators=trust)
        execute(cursor, "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE()", "inspect-tables")
        actual = dict(cursor.fetchall())
        extensions = {"allocations", "allocation_gpus"}
        present = extensions & actual.keys()
        report["schema_version"] = "v0.2-2" if present == extensions else "partial-v0.2-2" if present else "v0.1"
        if present:
            table_sql, _ = definitions("v0.2-2")
        proposal_tables = {"job_proposals", "proposal_responses"} & actual.keys()
        if proposal_tables:
            report["schema_version"] = ("v0.3-1" if len(proposal_tables) == 2 and present == extensions
                                        else "partial-v0.3-1")
            table_sql, _ = definitions("v0.3-1")
        if "allocation_bases" in actual:
            report["schema_version"] = "v0.3-2" if report["schema_version"] == "v0.3-1" else "partial-v0.3-2"
            table_sql, _ = definitions("v0.3-2")
        report["unexpected_tables"] = len(actual.keys() - table_sql.keys())
        for name, expected in table_sql.items():
            row = report["tables"][name] = {"exists": name in actual, "matches": False, "rows": None}
            if name in actual:
                execute(cursor, f"SHOW CREATE TABLE `{name}`", "inspect-definition", name)
                row["matches"] = actual[name] == "InnoDB" and table_signature(cursor.fetchone()[1]) == table_signature(expected)
                execute(cursor, f"SELECT COUNT(*) FROM `{name}`", "inspect-count", name)
                row["rows"] = cursor.fetchone()[0]
        execute(cursor, "SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, "
                "ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()", "inspect-triggers")
        actual_triggers = {r[0]: (r[1], r[2], r[3], normalize(r[4])) for r in cursor.fetchall()}
        report["unexpected_triggers"] = len(actual_triggers.keys() - trigger_sql.keys())
        for name, sql in trigger_sql.items():
            report["triggers"][name] = {"exists": name in actual_triggers,
                                        "matches": actual_triggers.get(name) == trigger_signature(name, sql)}
    return report


def require_matching_tables(report):
    if report.get("schema_version") == "partial-v0.3-2":
        raise DatabaseConfigError("Partial v0.3-2 schema; preserve objects and inspect")
    if report["unexpected_tables"] or report["unexpected_triggers"]:
        raise DatabaseConfigError("Unexpected objects found; manual review required")
    if report.get("schema_version") == "partial-v0.2-2":
        raise DatabaseConfigError("Partial v0.2-2 migration; use database.migrate --resume after inspection")
    if report.get("schema_version") == "partial-v0.3-1":
        raise DatabaseConfigError("Partial v0.3-1 migration; inspect then use database.proposal_migrate --resume")
    for name, row in report["tables"].items():
        if not row["exists"] or not row["matches"]:
            raise DatabaseConfigError("Table missing or differs from schema.sql: " + name)
    for name, row in report["triggers"].items():
        if row["exists"] and not row["matches"]:
            raise DatabaseConfigError("Existing trigger differs from schema.sql: " + name)
