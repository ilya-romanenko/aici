from __future__ import annotations

import json
import os
import socket
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

LOCKS_ROOT_DIR = "_locks"
MONTHLY_LOCKS_DIR = "monthly_jobs"


class MonthlyJobLockBusyError(RuntimeError):
    def __init__(self, lock_path: Path, payload: dict[str, object] | None = None) -> None:
        self.lock_path = lock_path
        self.payload = payload or {}
        super().__init__(f"monthly job lock is already held: {lock_path}")


@dataclass(slots=True)
class MonthlyJobLock:
    path: Path
    token: str

    def release(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        if not isinstance(payload, dict):
            return
        if payload.get("token") != self.token:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            return


def _month_key(target_month: date | None) -> str:
    current = target_month or date.today()
    return f"{current.year:04d}-{current.month:02d}"


def _lock_path(runs_root: Path, *, contour: str, target_month: date | None) -> Path:
    normalized_contour = (contour or "").strip().lower()
    if not normalized_contour:
        raise ValueError("contour must be a non-empty string")
    root = Path(runs_root) / LOCKS_ROOT_DIR / MONTHLY_LOCKS_DIR / normalized_contour
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{_month_key(target_month)}.lock"


def _read_payload(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def _is_stale(path: Path, *, stale_after_seconds: int) -> bool:
    if stale_after_seconds <= 0 or not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds > stale_after_seconds


def acquire_monthly_job_lock(
    runs_root: Path,
    *,
    contour: str,
    target_month: date | None = None,
    stale_after_seconds: int = 0,
) -> MonthlyJobLock:
    path = _lock_path(runs_root, contour=contour, target_month=target_month)
    attempts_left = 2
    while attempts_left > 0:
        attempts_left -= 1
        token = uuid.uuid4().hex
        payload = {
            "token": token,
            "contour": contour,
            "target_month": _month_key(target_month),
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
        }
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if attempts_left > 0 and _is_stale(path, stale_after_seconds=stale_after_seconds):
                try:
                    path.unlink()
                    continue
                except FileNotFoundError:
                    continue
            raise MonthlyJobLockBusyError(path, payload=_read_payload(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception:  # noqa: BLE001
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        return MonthlyJobLock(path=path, token=token)
    raise MonthlyJobLockBusyError(path, payload=_read_payload(path))


@contextmanager
def hold_monthly_job_lock(
    runs_root: Path,
    *,
    contour: str,
    target_month: date | None = None,
    stale_after_seconds: int = 0,
) -> Iterator[MonthlyJobLock]:
    lock = acquire_monthly_job_lock(
        runs_root,
        contour=contour,
        target_month=target_month,
        stale_after_seconds=stale_after_seconds,
    )
    try:
        yield lock
    finally:
        lock.release()


__all__ = [
    "LOCKS_ROOT_DIR",
    "MONTHLY_LOCKS_DIR",
    "MonthlyJobLock",
    "MonthlyJobLockBusyError",
    "acquire_monthly_job_lock",
    "hold_monthly_job_lock",
]
