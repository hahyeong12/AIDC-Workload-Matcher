"""Proposal generation, read-only views and terminal customer consent CLI."""

import argparse
from contextlib import closing
from dataclasses import asdict
import json
import sys

from dotenv import load_dotenv

from app.allocation import AllocationError
from app.db import PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError, connect_database, database_step
from app.matching import MatchingError, parse_job_id
from app.proposal import AUTHENTICATION_NOTICE, PROPOSAL_SCOPE, ProposalError
from app.services.proposals import ProposalService


def main(argv=None):
    parser = argparse.ArgumentParser(description="원본 조건의 대안과 고객 의사 기록; GPU 배정 없음")
    parser.add_argument("--test", action="store_true", help="TEST_MYSQL_* 전용 DB 사용")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("generate", "list", "history"):
        commands.add_parser(name).add_argument("job_id")
    commands.add_parser("show").add_argument("proposal_id")
    for name in ("accept", "reject"):
        sub = commands.add_parser(name)
        sub.add_argument("job_id")
        sub.add_argument("proposal_id")
        sub.add_argument("customer_id", help="업무 관계 검사 입력이며 로그인 인증이 아닙니다")
    args = parser.parse_args(argv)
    try:
        identifiers = {key: parse_job_id(value) for key, value in vars(args).items() if key.endswith("_id")}
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        with database_step("connect"):
            connection = connect_database("TEST_MYSQL_" if args.test else "MYSQL_")
        with closing(connection):
            service = ProposalService(connection)
            if args.operation == "generate":
                result = asdict(service.generate(identifiers["job_id"]))
            elif args.operation in ("accept", "reject"):
                result = asdict(service.respond(**identifiers, decision="ACCEPTED" if args.operation == "accept" else "REJECTED"))
            else:
                try:
                    with database_step("proposal-read-begin"):
                        connection.start_transaction(isolation_level="REPEATABLE READ", readonly=True, consistent_snapshot=True)
                    if args.operation == "show":
                        result = asdict(service.detail(identifiers["proposal_id"]))
                    elif args.operation == "list":
                        result = {"job_request_id": identifiers["job_id"],
                                  "proposals": [asdict(item) for item in service.list_for_job(identifiers["job_id"])]}
                    else:
                        result = {"job_request_id": identifiers["job_id"],
                                  "responses": [asdict(item) for item in service.history(identifiers["job_id"])]}
                finally:
                    with database_step("proposal-read-end"):
                        connection.rollback()
            result.update(scope=PROPOSAL_SCOPE, authentication_notice=AUTHENTICATION_NOTICE)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0
    except (MatchingError, ProposalError, AllocationError) as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (DatabaseConfigError, DatabaseOperationError) as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("대안 처리에 실패했습니다. DB 설정·스키마를 점검하세요.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
