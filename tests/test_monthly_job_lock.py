from __future__ import annotations

from datetime import date

import pytest

from ai_crypto_index.shared.monthly_job_lock import (
    MonthlyJobLockBusyError,
    hold_monthly_job_lock,
)


def test_hold_monthly_job_lock_blocks_same_contour_and_month(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    with hold_monthly_job_lock(runs_root, contour="index-auto", target_month=date(2026, 2, 1)):
        with pytest.raises(MonthlyJobLockBusyError):
            with hold_monthly_job_lock(runs_root, contour="index-auto", target_month=date(2026, 2, 1)):
                pass


def test_hold_monthly_job_lock_separates_contours(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    with hold_monthly_job_lock(runs_root, contour="index-auto", target_month=date(2026, 2, 1)):
        with hold_monthly_job_lock(runs_root, contour="performance-auto", target_month=date(2026, 2, 1)):
            pass


def test_hold_monthly_job_lock_separates_months(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    with hold_monthly_job_lock(runs_root, contour="index-auto", target_month=date(2026, 2, 1)):
        with hold_monthly_job_lock(runs_root, contour="index-auto", target_month=date(2026, 3, 1)):
            pass
