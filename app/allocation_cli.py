"""python -m app.allocation_cli --test {available|allocate|complete|fail|history} ID."""

import argparse
from contextlib import closing
from dataclasses import asdict
import json
import sys

from dotenv import load_dotenv

from app.allocation import AllocationError
from app.db import PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError, connect_database, database_step
from app.matching import MatchingError, parse_job_id
from app.repositories.allocations import AllocationRepository
from app.services.allocations import AllocationService, get_availability


def main(argv=None):
    parser = argparse.ArgumentParser(description="원본 요구조건에 따른 GPU 가용 조회·배정·반환·이력")
    parser.add_argument("--test", action="store_true", help="TEST_MYSQL_* 전용 DB 사용")
    parser.add_argument("operation", choices=("available", "allocate", "complete", "fail", "history"))
    parser.add_argument("id", help="available/allocate: Job ID; complete/fail: 할당 ID; history: GPU ID")
    args = parser.parse_args(argv)
    try:
        resource_id = parse_job_id(args.id)
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        with database_step("connect"):
            connection = connect_database("TEST_MYSQL_" if args.test else "MYSQL_")
        with closing(connection):
            if args.operation in ("available", "history"):
                try:
                    with database_step("allocation-read-begin"):
                        connection.start_transaction(
                            isolation_level="REPEATABLE READ", readonly=True, consistent_snapshot=True)
                    repo = AllocationRepository(connection)
                    if args.operation == "available":
                        result = asdict(get_availability(repo, resource_id))
                    else:
                        fields = ("allocation_id", "job_request_id", "status", "server_id", "assigned_at", "released_at")
                        result = {"gpu_id": resource_id,
                                  "history": [dict(zip(fields, row)) for row in repo.history_for_gpu(resource_id)]}
                finally:
                    with database_step("allocation-read-end"):
                        connection.rollback()
            else:
                service = AllocationService(connection)
                record = (service.allocate(resource_id) if args.operation == "allocate"
                          else service.finish(resource_id, "FAILED" if args.operation == "fail" else "COMPLETED"))
                result = asdict(record)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0
    except (MatchingError, AllocationError) as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (DatabaseConfigError, DatabaseOperationError) as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("GPU 할당 작업에 실패했습니다. DB 설정과 상태를 확인하세요.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
