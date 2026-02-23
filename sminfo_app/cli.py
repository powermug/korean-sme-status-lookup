from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sminfo-cli",
        description="중소기업현황정보시스템 조회를 위한 CLI",
    )
    parser.add_argument(
        "--state-path",
        default=None,
        help="로그인 세션 저장 파일 경로 (기본: ./.data/storage_state.json)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=45000,
        help="Playwright 기본 타임아웃(ms)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="저장된 로그인 세션 상태 확인")

    login = sub.add_parser("login", help="로그인 세션 저장")
    login.add_argument("--id", dest="username", default=os.getenv("SMINFO_ID"))
    login.add_argument("--password", default=os.getenv("SMINFO_PASSWORD"))
    login.add_argument(
        "--headless",
        action="store_true",
        help="headless 모드에서 로그인 시도(자동 로그인시에만 권장)",
    )
    login.add_argument(
        "--manual-wait-seconds",
        type=int,
        default=300,
        help="수동 로그인 감지 대기 시간(초)",
    )

    search = sub.add_parser("search", help="회사 검색 및 실적 표 추출")
    search.add_argument("query", help="검색어")
    search.add_argument("--company", default=None, help="후보 중 정확히 선택할 회사명")
    search.add_argument(
        "--json",
        dest="json_output",
        default=None,
        help="결과 JSON 파일 출력 경로",
    )
    search.add_argument(
        "--show-rows",
        type=int,
        default=6,
        help="터미널에 표 미리보기로 출력할 최대 row 수",
    )

    return parser


def _print_result(result: dict, show_rows: int) -> None:
    print(f"검색어: {result['query']}")
    print(f"후보 수: {len(result['candidates'])}")

    if result["candidates"]:
        print("\n후보 목록:")
        for idx, candidate in enumerate(result["candidates"][:10], start=1):
            table_title = candidate["table_title"] or "(제목 없음)"
            print(
                f"{idx:>2}. {candidate['name']}  | score={candidate['match_score']} | table={table_title}"
            )

    if result["selected"]:
        print(f"\n선택 회사: {result['selected']['name']}")

    tables = result["performance_tables"]
    print(f"추출 표 수: {len(tables)}")

    for idx, table in enumerate(tables, start=1):
        title = table["title"] or f"Table {idx}"
        print(f"\n[{idx}] {title}")
        if table["headers"]:
            print("  " + " | ".join(table["headers"]))

        for row in table["rows"][:show_rows]:
            print("  " + " | ".join(row))

        if len(table["rows"]) > show_rows:
            remain = len(table["rows"]) - show_rows
            print(f"  ... {remain} rows more")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    from .config import DEFAULT_STATE_PATH

    state_path = Path(args.state_path) if args.state_path else DEFAULT_STATE_PATH

    if args.command == "status":
        if state_path.exists():
            print(f"세션 파일이 존재합니다: {state_path}")
            return 0
        print(f"세션 파일이 없습니다: {state_path}")
        return 1

    try:
        from .sminfo_client import NotLoggedInError, SearchError, SminfoClient
    except ModuleNotFoundError as exc:
        print(
            "필수 패키지가 없습니다. 먼저 `pip install -r requirements.txt` 를 실행하세요.",
            file=sys.stderr,
        )
        print(f"상세: {exc}", file=sys.stderr)
        return 3

    client = SminfoClient(
        state_path=state_path,
        timeout_ms=args.timeout_ms,
        headless=True,
    )

    try:
        if args.command == "login":
            state_path = client.login(
                username=args.username,
                password=args.password,
                headless=args.headless,
                manual_wait_seconds=args.manual_wait_seconds,
            )
            print(f"로그인 세션 저장 완료: {state_path}")
            return 0

        if args.command == "search":
            result = client.search_company(
                query=args.query,
                company_name=args.company,
            ).to_dict()

            _print_result(result, show_rows=args.show_rows)

            if args.json_output:
                output_path = Path(args.json_output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"\nJSON 저장: {output_path}")

            return 0

    except (NotLoggedInError, SearchError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
