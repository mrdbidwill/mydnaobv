#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


RISK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bop\.drop_table\s*\("), "drop_table"),
    (re.compile(r"\bop\.drop_column\s*\("), "drop_column"),
    (re.compile(r"\bop\.rename_table\s*\("), "rename_table"),
    (re.compile(r"\bop\.alter_column\s*\(.*nullable\s*=\s*False"), "alter_column_nullable_false"),
    (re.compile(r"\bop\.execute\s*\(\s*[\"']\s*DROP\s+", re.IGNORECASE), "execute_drop_sql"),
]


def run_git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def changed_migration_files(base: str, head: str) -> list[Path]:
    output = run_git(["diff", "--name-only", f"{base}..{head}", "--", "alembic/versions"])
    out: list[Path] = []
    for raw in output.splitlines():
        path = Path(raw.strip())
        if not path:
            continue
        if path.suffix != ".py":
            continue
        if path.exists():
            out.append(path)
    return out


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    hits: list[tuple[int, str, str]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines, start=1):
        for pattern, label in RISK_PATTERNS:
            if pattern.search(line):
                hits.append((idx, label, line.strip()))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="Check alembic migrations for potentially breaking operations.")
    parser.add_argument("--base", required=True, help="Base git commit.")
    parser.add_argument("--head", required=True, help="Head git commit.")
    args = parser.parse_args()

    try:
        files = changed_migration_files(args.base, args.head)
    except subprocess.CalledProcessError as exc:
        print(f"[migration-guard] Failed to diff commits: {exc}", file=sys.stderr)
        return 2

    if not files:
        print("[migration-guard] No changed migration files.")
        return 0

    findings: list[tuple[Path, int, str, str]] = []
    for path in files:
        for line_no, label, snippet in scan_file(path):
            findings.append((path, line_no, label, snippet))

    if not findings:
        print(f"[migration-guard] Checked {len(files)} migration file(s); no risky patterns found.")
        return 0

    print("[migration-guard] Potentially breaking migration operations detected:")
    for path, line_no, label, snippet in findings:
        print(f"  - {path}:{line_no} [{label}] {snippet}")

    allow_breaking = os.getenv("ALLOW_BREAKING_MIGRATIONS", "0").strip() == "1"
    if allow_breaking:
        print("[migration-guard] ALLOW_BREAKING_MIGRATIONS=1 set; continuing despite findings.")
        return 0

    print(
        "[migration-guard] Blocking deploy. Set ALLOW_BREAKING_MIGRATIONS=1 only for planned breaking schema changes.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
