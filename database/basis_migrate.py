"""004: test-only additive evidence table; never ALTER/backfill old records."""

import argparse
from contextlib import closing
import json

from dotenv import load_dotenv

from app.db import PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError, connect_database
from database.initialize import execute
from database.inspect_schema import definitions, inspect, require_matching_tables
from database.migrate import base_fingerprint


def preflight(report):
    require_matching_tables(report)
    if report['schema_version'] not in ('v0.3-1', 'v0.3-2'):
        raise DatabaseConfigError('Complete 003 schema required before 004')
    if not all(t['matches'] for t in report['triggers'].values()) or len(report['triggers']) != 4:
        raise DatabaseConfigError('All original triggers must match')
    return report['schema_version'] == 'v0.3-1'


def migrate(connection, apply=False):
    if connection.in_transaction:
        raise DatabaseConfigError('Migration requires a fresh connection')
    with closing(connection.cursor()) as cursor:
        execute(cursor, 'SELECT DATABASE()', 'basis-target')
        database = cursor.fetchone()[0]
        if apply and not database.endswith('_test'):
            raise DatabaseConfigError('004 apply is restricted to the test database')
        cursor.execute('SELECT GET_LOCK(CONCAT(DATABASE(), %s), 0)', ('_v022_migration',))
        if cursor.fetchone()[0] != 1:
            raise DatabaseConfigError('Another migration is running')
    try:
        report = inspect(connection)
        pending = preflight(report)
        with closing(connection.cursor()) as cursor:
            for catalog, column in (('ROUTINES', 'ROUTINE_SCHEMA'), ('EVENTS', 'EVENT_SCHEMA')):
                execute(cursor, f'SELECT COUNT(*) FROM information_schema.{catalog} WHERE {column}=DATABASE()', 'basis-preflight')
                if cursor.fetchone()[0]:
                    raise DatabaseConfigError('Unexpected routines/events')
        before = base_fingerprint(connection, 'v0.3-1')
        connection.rollback()
        if apply and pending:
            with closing(connection.cursor()) as cursor:
                execute(cursor, definitions('v0.3-2')[0]['allocation_bases'], 'basis-create', 'allocation_bases')
            report = inspect(connection)
            if preflight(report) or base_fingerprint(connection, 'v0.3-1') != before:
                raise DatabaseConfigError('Post-004 audit failed; preserve objects and inspect')
        return dict(applied=bool(apply and pending), pending_tables=['allocation_bases'] if pending and not apply else [],
                    schema_version=report['schema_version'], original_data_preserved=True)
    finally:
        connection.rollback()
        with closing(connection.cursor()) as cursor:
            cursor.execute('SELECT RELEASE_LOCK(CONCAT(DATABASE(), %s))', ('_v022_migration',))
            cursor.fetchone()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect/apply 004; writes only with --test --apply')
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if args.apply and not args.test:
        parser.error('004 applies only to the dedicated test database')
    load_dotenv(PROJECT_ROOT / '.env', override=False)
    try:
        with closing(connect_database('TEST_MYSQL_' if args.test else 'MYSQL_')) as cn:
            print(json.dumps(migrate(cn, args.apply), indent=2))
        return 0
    except (DatabaseConfigError, DatabaseOperationError) as exc:
        print(str(exc))
    except Exception:
        print('004 stopped; preserve objects and inspect locally. No automatic reset.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
