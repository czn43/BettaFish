#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BettaFish - MySQL 引导建库脚本（只建库，不建表）

作用：
    1. 读取项目根目录 .env 里的 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME
    2. 先用「不带 database」的连接登录，执行 CREATE DATABASE IF NOT EXISTS
    3. 打印 MySQL 版本、字符集、已存在表数量
    建表由 `python MindSpider/main.py --init-db`（SQLAlchemy create_all）负责。

用法：
    python scripts/init_mysql_db.py
    python scripts/init_mysql_db.py --name bettafish --host 127.0.0.1 --port 3306 --user root --password 123456789
    python scripts/init_mysql_db.py --dry-run        # 只打印将要执行的 SQL

幂等：重复执行不会删库/删表，也不改已有数据。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_$]{1,64}$")


def load_env(path: Path) -> dict:
    """极简 .env 解析，避免依赖 pydantic-settings。"""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data


def pick(args, env: dict, arg_name: str, env_key: str, default: str) -> str:
    value = getattr(args, arg_name, None)
    if value:
        return str(value)
    return env.get(env_key) or default


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the BettaFish MySQL database if missing.")
    parser.add_argument("--host")
    parser.add_argument("--port")
    parser.add_argument("--user")
    parser.add_argument("--password")
    parser.add_argument("--name", help="database name, default: DB_NAME in .env, else bettafish")
    parser.add_argument("--dry-run", action="store_true", help="print the SQL and exit")
    args = parser.parse_args()

    env = load_env(ENV_FILE)
    host = pick(args, env, "host", "DB_HOST", "127.0.0.1")
    port = int(pick(args, env, "port", "DB_PORT", "3306"))
    user = pick(args, env, "user", "DB_USER", "root")
    password = pick(args, env, "password", "DB_PASSWORD", "")
    name = pick(args, env, "name", "DB_NAME", "bettafish")

    if not SAFE_NAME.match(name):
        print(f"[error] illegal database name: {name!r} (allowed: letters, digits, _, $)")
        return 2

    ddl = (
        f"CREATE DATABASE IF NOT EXISTS `{name}` "
        "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    print(f"[info] .env          : {ENV_FILE if ENV_FILE.exists() else '(missing, using CLI/defaults)'}")
    print(f"[info] target server : mysql://{user}@{host}:{port}/ (no database)")
    print(f"[info] target schema : {name}")
    print(f"[info] sql           : {ddl}")

    if args.dry_run:
        print("[info] dry-run, nothing executed")
        return 0

    try:
        import pymysql
    except ImportError:
        print("[error] pymysql is not installed. run: .\\.venv\\Scripts\\python.exe -m pip install pymysql")
        return 3

    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            charset="utf8mb4",
            connect_timeout=6,
            autocommit=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[error] cannot connect to MySQL at {host}:{port} -> {type(exc).__name__}: {exc}")
        print("[hint] 1) is the MySQL service running?  2) DB_USER/DB_PASSWORD/DB_PORT in .env correct?")
        print("[hint] 3) root over TCP may be refused by your grant table (localhost-only account).")
        return 4

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            cur.execute("SHOW VARIABLES LIKE 'character_set_server'")
            row = cur.fetchone()
            server_charset = row[1] if row else "?"
            cur.execute("SHOW VARIABLES LIKE 'innodb_default_row_format'")
            row = cur.fetchone()
            row_format = row[1] if row else "?"

            print(f"[info] mysql version : {version}")
            print(f"[info] server charset: {server_charset}   row_format: {row_format}")

            cur.execute(ddl)

            cur.execute(
                "SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
                "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                (name,),
            )
            created = cur.fetchone()
            if not created:
                print(f"[error] database `{name}` is still missing after CREATE")
                return 5
            print(f"[ok]   database `{created[0]}` ready  ({created[1]} / {created[2]})")

            cur.execute(
                "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s",
                (name,),
            )
            tables = cur.fetchone()[0]
            print(f"[info] tables present: {tables} (0 means tables are created by --init-db)")
    finally:
        conn.close()

    print("[next] .\\.venv\\Scripts\\python.exe MindSpider\\main.py --init-db")
    return 0


if __name__ == "__main__":
    sys.exit(main())
