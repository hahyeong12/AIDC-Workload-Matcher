"""python -m app.job_cli [--test] register|detail|list ..."""

import argparse
from contextlib import closing
from dataclasses import asdict
import json
import sys

from dotenv import load_dotenv

from app.allocation import AllocationError
from app.db import PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError, connect_database, database_step
from app.job import parse_id, validate_registration
from app.matching import MatchingError, parse_job_id
from app.services.jobs import JobService


def main(argv=None):
    parser = argparse.ArgumentParser(description="신규 Job 등록 및 원본 조회")
    parser.add_argument("--test", action="store_true", help="TEST_MYSQL_* 전용 DB 사용")
    commands = parser.add_subparsers(dest="operation", required=True)
    register = commands.add_parser("register")
    register.add_argument("--customer-id", required=True)
    register.add_argument("--title", required=True)
    register.add_argument("--original-request", required=True)
    register.add_argument("--gpu-model-id", required=True)
    register.add_argument("--gpu-count", required=True, type=int)
    register.add_argument("--same-server-required", required=True, choices=("0", "1"))
    commands.add_parser("detail").add_argument("job_id")
    commands.add_parser("list").add_argument("customer_id")
    args = parser.parse_args(argv)
    try:
        if args.operation == "register":
            values = validate_registration(args.customer_id, args.title, args.original_request,
                                           args.gpu_model_id, args.gpu_count, int(args.same_server_required))
        elif args.operation == "detail":
            args.job_id = parse_job_id(args.job_id)
        else:
            args.customer_id = parse_id(args.customer_id, "customer_id")
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        with database_step("connect"):
            connection = connect_database("TEST_MYSQL_" if args.test else "MYSQL_")
        with closing(connection):
            service = JobService(connection)
            if args.operation == "register":
                result = {"job_request_id": service.register(*values)}
            else:
                try:
                    with database_step("job-read-begin"):
                        connection.start_transaction(
                            isolation_level="REPEATABLE READ", readonly=True, consistent_snapshot=True)
                    result = (asdict(service.detail(args.job_id)) if args.operation == "detail" else
                              {"customer_id": args.customer_id,
                               "jobs": [asdict(item) for item in service.list_for_customer(args.customer_id)]})
                finally:
                    with database_step("job-read-end"):
                        connection.rollback()
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0
    except (MatchingError, AllocationError) as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (DatabaseConfigError, DatabaseOperationError) as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("Job 작업에 실패했습니다. DB 설정과 상태를 확인하세요.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
