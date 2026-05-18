#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import sys


SAFE_HOST_PREFIXES = ("127.0.0.1:", "localhost:", "[::1]:", "::1:")
DEFAULT_FILES = ("docker-compose.yml", "docker-compose.prod.yml")


def is_risky_postgres_mapping(entry: str) -> bool:
    normalized = entry.strip().strip("'\"")
    lower = normalized.lower()

    if "postgresql://" in lower or "pg_isready" in lower:
        return False

    if normalized.startswith(SAFE_HOST_PREFIXES):
        return False

    if normalized in {"5432", "5432/tcp"}:
        return True

    if normalized.endswith(":5432") or normalized.endswith(":5432/tcp"):
        return True

    if ":5432:5432" in normalized:
        return True

    return False


def collect_findings(path: pathlib.Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line or "5432" not in line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        if is_risky_postgres_mapping(line):
            findings.append((line_number, raw_line.strip()))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Block insecure PostgreSQL port exposure in docker compose files."
    )
    parser.add_argument("files", nargs="*", default=list(DEFAULT_FILES))
    args = parser.parse_args()

    missing_files: list[str] = []
    unsafe_entries: list[tuple[str, int, str]] = []

    for file_name in args.files:
        path = pathlib.Path(file_name)
        if not path.exists():
            missing_files.append(file_name)
            continue
        for line_number, line in collect_findings(path):
            unsafe_entries.append((file_name, line_number, line))

    if missing_files:
        print("Missing required compose files:")
        for file_name in missing_files:
            print(f"  - {file_name}")
        return 2

    if unsafe_entries:
        print("Detected insecure PostgreSQL port mappings (5432 exposed beyond localhost):")
        for file_name, line_number, line in unsafe_entries:
            print(f"  - {file_name}:{line_number}: {line}")
        print("Use 127.0.0.1 binding, for example: 127.0.0.1:${POSTGRES_PORT:-5432}:5432")
        return 1

    print("Compose security check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
