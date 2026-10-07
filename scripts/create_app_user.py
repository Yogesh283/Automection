"""
Create an app login user (NOT the game mobile/password).

  python scripts/create_app_user.py --username admin --password "choose-a-strong-password"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from infra.db import ensure_auth_session_schema
from infra.users import create_user, get_user_by_username


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    ensure_auth_session_schema()
    if get_user_by_username(args.username.strip()):
        raise SystemExit("User already exists")

    user_id = create_user(args.username.strip(), args.password)
    print(f"Created app user id={user_id} username={args.username.strip()}")


if __name__ == "__main__":
    main()
