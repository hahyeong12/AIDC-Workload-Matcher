"""CLI for original/accepted allocation, evidence, GPU history and return."""

import argparse
from contextlib import closing
from dataclasses import asdict
import json
import sys

from dotenv import load_dotenv

from app.allocation import AllocationError
from app.db import PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError, connect_database, database_step
from app.matching import MatchingError, parse_job_id
from app.proposal import ProposalError
from app.repositories.allocations import AllocationRepository
from app.services.allocations import AllocationService, get_availability


def main(argv=None):
    parser = argparse.ArgumentParser(description="원본 또는 수락 대안의 GPU 배정·근거·반환·이력")
    parser.add_argument("--test", action="store_true", help="TEST_MYSQL_* 전용 DB 사용")
    parser.add_argument("operation", choices=("available", "allocate", "allocate-proposal", "detail",
                                              "complete", "fail", "history", "job-history"))
    parser.add_argument("id", help="available/allocate/allocate-proposal/job-history: Job ID; "
                                  "detail/complete/fail: 할당 ID; history: GPU ID")
    parser.add_argument("--proposal-id")
    parser.add_argument("--customer-id", help="원본 소유 고객 ID; 실제 사용자 인증을 대신하지 않음")
    args = parser.parse_args(argv)
    try:
        resource_id = parse_job_id(args.id)
        if args.operation == "allocate-proposal":
            proposal_id = parse_job_id(args.proposal_id)
            customer_id = parse_job_id(args.customer_id)
        elif args.proposal_id is not None or args.customer_id is not None:
            parser.error("대안·고객 옵션은 allocate-proposal에서만 사용합니다.")
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        with database_step("connect"):
            connection = connect_database("TEST_MYSQL_" if args.test else "MYSQL_")
        with closing(connection):
            if args.operation in ("available", "history", "detail", "job-history"):
                try:
                    with database_step("allocation-read-begin"):
                        connection.start_transaction(
                            isolation_level="REPEATABLE READ", readonly=True, consistent_snapshot=True)
                    repo = AllocationRepository(connection)
                    if args.operation == "available":
                        result = asdict(get_availability(repo, resource_id))
                    elif args.operation == "history":
                        fields = ("allocation_id", "job_request_id", "status", "server_id", "assigned_at", "released_at")
                        result = {"gpu_id": resource_id,
                                  "history": [dict(zip(fields, row)) for row in repo.history_for_gpu(resource_id)]}
                    elif args.operation == "detail":
                        result = asdict(AllocationService(connection).detail(resource_id))
                    else:
                        service = AllocationService(connection)
                        result = {"job_request_id": resource_id, "history": [
                            asdict(service.detail(item.allocation_id)) for item in repo.history_for_job(resource_id)]}
                finally:
                    with database_step("allocation-read-end"):
                        connection.rollback()
            else:
                service = AllocationService(connection)
                if args.operation == "allocate-proposal":
                    record = service.allocate_proposal(resource_id, proposal_id, customer_id)
                else:
                    record = (service.allocate(resource_id) if args.operation == "allocate"
                              else service.finish(resource_id, "FAILED" if args.operation == "fail" else "COMPLETED"))
                result = asdict(record)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0
    except (MatchingError, AllocationError, ProposalError) as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (DatabaseConfigError, DatabaseOperationError) as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("GPU 할당 작업에 실패했습니다. DB 설정과 상태를 확인하세요.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
