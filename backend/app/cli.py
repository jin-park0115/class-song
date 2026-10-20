"""관리자 계정 CLI. 관리자 권한은 이 CLI로만 부여·회수한다(요구사항 2.1).

사용법:
  python -m app.cli create --login-id admin --name 운영자
  python -m app.cli promote --login-id student01
  python -m app.cli revoke --login-id student01
  python -m app.cli reset-password --login-id admin
  python -m app.cli list

비밀번호는 프롬프트로 두 번 입력받는다. 자동화에서는 --password-stdin 으로 표준입력 첫 줄을 사용한다.
"""

import argparse
import getpass
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password, validate_login_id, validate_password
from app.auth.sessions import revoke_user_sessions
from app.db import SessionLocal
from app.models import User
from app.services import audit


class CliError(Exception):
    pass


def _read_password(login_id: str, from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("비밀번호: ")
        if password != getpass.getpass("비밀번호 확인: "):
            raise CliError("비밀번호가 일치하지 않습니다.")
    problem = validate_password(login_id, password)
    if problem:
        raise CliError(problem)
    return password


def _get_user(db: Session, login_id: str) -> User:
    user = db.scalar(select(User).where(User.login_id == login_id))
    if user is None:
        raise CliError(f"사용자를 찾을 수 없습니다: {login_id}")
    return user


def cmd_create(db: Session, args: argparse.Namespace) -> str:
    login_id = args.login_id.strip().lower()
    problem = validate_login_id(login_id)
    if problem:
        raise CliError(problem)
    if db.scalar(select(User).where(User.login_id == login_id)):
        raise CliError(f"이미 존재하는 아이디입니다: {login_id} (기존 사용자는 promote 를 사용하세요)")
    password = _read_password(login_id, args.password_stdin)
    now = datetime.now(UTC)
    user = User(
        login_id=login_id,
        name=args.name or login_id,
        password_hash=hash_password(password),
        role="ADMIN",
        account_status="ACTIVE",
        status_changed_at=now,
    )
    db.add(user)
    db.flush()
    audit.record(db, None, "cli.admin_create", {"user_id": user.id, "login_id": login_id})
    return f"관리자 계정을 만들었습니다: {login_id}"


def cmd_promote(db: Session, args: argparse.Namespace) -> str:
    user = _get_user(db, args.login_id)
    if user.is_admin:
        return f"이미 관리자입니다: {user.login_id}"
    user.role = "ADMIN"
    user.account_status = "ACTIVE"
    user.status_changed_at = datetime.now(UTC)
    revoke_user_sessions(db, user.id)  # 새 권한·세션 만료 정책으로 다시 로그인하게 한다
    audit.record(db, None, "cli.admin_promote", {"user_id": user.id, "login_id": user.login_id})
    return f"관리자로 승격했습니다: {user.login_id}"


def cmd_revoke(db: Session, args: argparse.Namespace) -> str:
    user = _get_user(db, args.login_id)
    if not user.is_admin:
        return f"관리자가 아닙니다: {user.login_id}"
    others = db.scalar(
        select(User.id).where(
            User.role == "ADMIN", User.account_status == "ACTIVE", User.id != user.id
        )
    )
    if others is None and not args.force:
        raise CliError("마지막 관리자입니다. 계속하려면 --force 를 붙이세요.")
    user.role = "STUDENT"
    revoke_user_sessions(db, user.id)
    audit.record(db, None, "cli.admin_revoke", {"user_id": user.id, "login_id": user.login_id})
    return f"관리자 권한을 회수했습니다: {user.login_id}"


def cmd_reset_password(db: Session, args: argparse.Namespace) -> str:
    user = _get_user(db, args.login_id)
    if not user.is_admin:
        raise CliError("학생 비밀번호는 관리자 웹에서 임시 비밀번호로 초기화합니다.")
    user.password_hash = hash_password(_read_password(user.login_id, args.password_stdin))
    user.failed_login_count = 0
    user.locked_until = None
    revoke_user_sessions(db, user.id)
    audit.record(db, None, "cli.admin_reset_password", {"user_id": user.id, "login_id": user.login_id})
    return f"비밀번호를 재설정했습니다: {user.login_id} (기존 세션은 모두 로그아웃됨)"


def cmd_list(db: Session, _: argparse.Namespace) -> str:
    rows = db.scalars(select(User).where(User.role == "ADMIN").order_by(User.id)).all()
    if not rows:
        return "관리자 계정이 없습니다."
    return "\n".join(f"{u.id}\t{u.login_id}\t{u.name}\t{u.account_status}" for u in rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jukebox-admin", description="AX2 Jukebox 관리자 계정 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create", help="관리자 계정 생성")
    p.add_argument("--login-id", required=True)
    p.add_argument("--name")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("promote", help="기존 사용자를 관리자로 승격")
    p.add_argument("--login-id", required=True)
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("revoke", help="관리자 권한 회수")
    p.add_argument("--login-id", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_revoke)

    p = sub.add_parser("reset-password", help="관리자 비밀번호 재설정")
    p.add_argument("--login-id", required=True)
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("list", help="관리자 목록")
    p.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with SessionLocal() as db:
        try:
            message = args.func(db, args)
            db.commit()
        except CliError as exc:
            db.rollback()
            print(f"오류: {exc}", file=sys.stderr)
            return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
