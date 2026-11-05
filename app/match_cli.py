"""Terminal entry point: python -m app.match_cli 001 [--test]."""

import argparse
from contextlib import closing
from dataclasses import asdict
import json
import sys

from dotenv import load_dotenv

from app.db import (
    PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError,
    connect_database, database_step,
)
from app.matching import MatchingError, parse_job_id
from app.repositories.hardware import HardwareRepository
from app.services.hardware import check_hardware


def main(argv=None):
    parser = argparse.ArgumentParser(description="원본 GPU 요구조건의 하드웨어 수량·배치 검사")
    parser.add_argument("job_id", help="양의 정수 Job ID (예: 001)")
    parser.add_argument("--test", action="store_true", help="분리된 TEST_MYSQL_* DB만 조회")
    args = parser.parse_args(argv)
    try:
        job_id = parse_job_id(args.job_id)
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        with database_step("connect"):
            connection = connect_database("TEST_MYSQL_" if args.test else "MYSQL_")
        with closing(connection):
            try:
                # All reads share one snapshot, even if equipment changes while
                # the CLI is running. The server also enforces read-only access.
                with database_step("start-read-only"):
                    connection.start_transaction(
                        isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
                result = check_hardware(HardwareRepository(connection), job_id)
            finally:
                with database_step("end-read-only"):
                    connection.rollback()
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    except MatchingError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (DatabaseConfigError, DatabaseOperationError) as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("하드웨어 조회에 실패했습니다. 로컬 MySQL 설정과 접근 권한을 확인하세요.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
