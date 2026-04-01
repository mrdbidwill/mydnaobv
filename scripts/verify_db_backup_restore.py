#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlparse


@dataclass
class ParsedDBURL:
    drivername: str
    username: str | None
    password: str | None
    host: str | None
    port: int | None
    database: str | None


def parse_database_url(raw: str) -> ParsedDBURL:
    parsed = urlparse(raw)
    if not parsed.scheme:
        raise RuntimeError("Invalid DATABASE_URL: missing scheme.")

    database = unquote(parsed.path.lstrip("/")) if parsed.path else None
    return ParsedDBURL(
        drivername=parsed.scheme,
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        host=parsed.hostname,
        port=parsed.port,
        database=database,
    )


def run(cmd: list[str], *, env: dict[str, str] | None = None, stdout=None) -> None:
    subprocess.run(cmd, check=True, env=env, stdout=stdout)


def require_tools(*tools: str) -> None:
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(f"Missing required tools: {', '.join(missing)}")


def safe_name(base: str, max_len: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_")
    if not cleaned:
        cleaned = "db"
    return cleaned[:max_len]


def verify_postgres(url, keep_dump: bool, dump_path: Path | None) -> None:
    require_tools("pg_dump", "pg_restore", "psql", "createdb", "dropdb")
    if not url.database:
        raise RuntimeError("DATABASE_URL is missing database name.")

    ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    base_name = safe_name(url.database, 40)
    temp_db = safe_name(f"{base_name}_restorecheck_{ts}", 63)

    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password

    host = url.host or ""
    port = str(url.port) if url.port else ""
    user = url.username or ""
    dbname = url.database
    dump_file = dump_path or Path(tempfile.gettempdir()) / f"{base_name}_{ts}.dump"

    dump_cmd = ["pg_dump", "-Fc", "-f", str(dump_file), "-d", dbname]
    create_cmd = ["createdb", temp_db]
    restore_cmd = ["pg_restore", "--no-owner", "--no-privileges", "-d", temp_db, str(dump_file)]
    query_cmd = [
        "psql",
        "-d",
        temp_db,
        "-At",
        "-c",
        (
            "SELECT COUNT(*) "
            "FROM information_schema.tables "
            "WHERE table_schema='public' "
            "AND table_name IN ('observation_lists','export_jobs','alembic_version');"
        ),
    ]
    drop_cmd = ["dropdb", temp_db]

    if host:
        dump_cmd.extend(["-h", host])
        create_cmd.extend(["-h", host])
        restore_cmd.extend(["-h", host])
        query_cmd.extend(["-h", host])
        drop_cmd.extend(["-h", host])
    if port:
        dump_cmd.extend(["-p", port])
        create_cmd.extend(["-p", port])
        restore_cmd.extend(["-p", port])
        query_cmd.extend(["-p", port])
        drop_cmd.extend(["-p", port])
    if user:
        dump_cmd.extend(["-U", user])
        create_cmd.extend(["-U", user])
        restore_cmd.extend(["-U", user])
        query_cmd.extend(["-U", user])
        drop_cmd.extend(["-U", user])

    try:
        run(dump_cmd, env=env)
        run(create_cmd, env=env)
        run(restore_cmd, env=env)

        proc = subprocess.run(query_cmd, check=True, capture_output=True, text=True, env=env)
        table_count = int((proc.stdout or "0").strip() or 0)
        if table_count < 3:
            raise RuntimeError(f"Restore validation failed. Expected 3 core tables, got {table_count}.")
    finally:
        subprocess.run(drop_cmd, env=env, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not keep_dump:
            dump_file.unlink(missing_ok=True)


def verify_mysql(url, keep_dump: bool, dump_path: Path | None) -> None:
    require_tools("mysqldump", "mysql")
    if not url.database:
        raise RuntimeError("DATABASE_URL is missing database name.")

    ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    base_name = safe_name(url.database, 40)
    temp_db = safe_name(f"{base_name}_restorecheck_{ts}", 63)
    dump_file = dump_path or Path(tempfile.gettempdir()) / f"{base_name}_{ts}.sql"

    env = os.environ.copy()
    if url.password:
        env["MYSQL_PWD"] = url.password

    host = url.host or "127.0.0.1"
    port = str(url.port) if url.port else "3306"
    user = url.username or ""
    dbname = url.database

    common = ["-h", host, "-P", port]
    if user:
        common.extend(["-u", user])

    dump_cmd = ["mysqldump", *common, "--single-transaction", "--routines", "--events", "--triggers", dbname]
    create_cmd = ["mysql", *common, "-e", f"CREATE DATABASE `{temp_db}`"]
    drop_cmd = ["mysql", *common, "-e", f"DROP DATABASE IF EXISTS `{temp_db}`"]
    check_cmd = [
        "mysql",
        *common,
        "-Nse",
        (
            "SELECT COUNT(*) "
            "FROM information_schema.tables "
            "WHERE table_schema=%s "
            "AND table_name IN ('observation_lists','export_jobs','alembic_version')"
        )
        % repr(temp_db),
    ]

    try:
        with dump_file.open("wb") as fp:
            run(dump_cmd, env=env, stdout=fp)
        run(create_cmd, env=env)
        with dump_file.open("rb") as fp:
            subprocess.run(["mysql", *common, temp_db], check=True, env=env, stdin=fp)

        proc = subprocess.run(check_cmd, check=True, capture_output=True, text=True, env=env)
        table_count = int((proc.stdout or "0").strip() or 0)
        if table_count < 3:
            raise RuntimeError(f"Restore validation failed. Expected 3 core tables, got {table_count}.")
    finally:
        subprocess.run(drop_cmd, env=env, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not keep_dump:
            dump_file.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup + restore verification for DATABASE_URL.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""), help="DB URL. Defaults to env DATABASE_URL.")
    parser.add_argument("--keep-dump", action="store_true", help="Keep backup dump file after verification.")
    parser.add_argument("--dump-path", default="", help="Optional explicit dump file path.")
    args = parser.parse_args()

    raw = (args.database_url or "").strip()
    if not raw:
        print("[db-verify] DATABASE_URL not provided.", file=sys.stderr)
        return 2

    url = parse_database_url(raw)
    driver = (url.drivername or "").lower()
    dump_path = Path(args.dump_path).expanduser() if args.dump_path else None

    try:
        if driver.startswith("postgresql"):
            print("[db-verify] Engine: PostgreSQL")
            verify_postgres(url, keep_dump=args.keep_dump, dump_path=dump_path)
        elif driver.startswith("mysql"):
            print("[db-verify] Engine: MySQL")
            verify_mysql(url, keep_dump=args.keep_dump, dump_path=dump_path)
        else:
            print(f"[db-verify] Unsupported DATABASE_URL driver: {url.drivername}", file=sys.stderr)
            return 2
    except Exception as exc:
        print(f"[db-verify] FAILED: {exc}", file=sys.stderr)
        return 1

    print("[db-verify] Backup + restore verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
