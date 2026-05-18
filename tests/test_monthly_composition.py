from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from ai_crypto_index.shared.monthly_composition import (
    load_monthly_snapshots_store,
    refresh_monthly_snapshots_store,
)


def _settings(runs_root):
    return SimpleNamespace(runs_root=runs_root)


def _write_run(
    runs_root,
    *,
    run_id: str,
    mtime: datetime,
    weights: list[tuple[str, float]],
    equity_dates: list[str] | None = None,
) -> None:
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(weights, columns=["asset", "weight"]).to_csv(
        run_dir / "weights.csv",
        index=False,
    )
    if equity_dates:
        pd.DataFrame(
            {
                "date": equity_dates,
                "equity": [1.0 for _ in equity_dates],
            }
        ).to_csv(run_dir / "equity_curve.csv", index=False)
    ts = mtime.timestamp()
    os.utime(run_dir, (ts, ts))
    os.utime(run_dir / "weights.csv", (ts, ts))
    if equity_dates:
        os.utime(run_dir / "equity_curve.csv", (ts, ts))


def test_refresh_monthly_snapshots_store_persists_and_splits(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    _write_run(
        runs_root,
        run_id="auto-classic-2026-01-04T16-44-38Z",
        mtime=datetime(2026, 1, 4, 16, 44, tzinfo=timezone.utc),
        weights=[("BTC", 0.7), ("ETH", 0.3)],
    )
    _write_run(
        runs_root,
        run_id="auto-classic-2026-01-20T11-00-00Z",
        mtime=datetime(2026, 1, 20, 11, 0, tzinfo=timezone.utc),
        weights=[("BTC", 0.6), ("ETH", 0.4)],
    )
    _write_run(
        runs_root,
        run_id="auto-classic-2026-02-03T15-14-32Z",
        mtime=datetime(2026, 2, 3, 15, 14, tzinfo=timezone.utc),
        weights=[("BTC", 0.5), ("ETH", 0.5)],
    )

    store = refresh_monthly_snapshots_store(
        _settings(runs_root),
        live_start_date="2026-02-01",
        run_prefix="auto-classic",
    )

    assert store.current_month == "2026-02"
    assert len(store.snapshots) == 4
    jan_btc = next(item for item in store.snapshots if item.month == "2026-01" and item.asset == "BTC")
    assert jan_btc.weight == 0.7
    assert len(store.backtest_snapshots) == 2
    assert all(item.month == "2026-01" for item in store.backtest_snapshots)
    assert len(store.live_snapshots) == 2
    assert all(item.month == "2026-02" for item in store.live_snapshots)

    restored = load_monthly_snapshots_store(_settings(runs_root))
    assert restored.current_month == "2026-02"
    assert len(restored.snapshots) == 4


def test_refresh_monthly_snapshots_store_merges_backtest_weights_with_live_runs(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    checkpoint_path = runs_root / "_performance" / "runs" / "classic" / "checkpoint_weights.csv"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"date": "2025-12-01", "BTC": 0.6, "ETH": 0.4},
            {"date": "2026-01-01", "BTC": 0.55, "ETH": 0.45},
            {"date": "2026-01-20", "BTC": 0.8, "ETH": 0.2},
        ]
    ).set_index("date").to_csv(checkpoint_path)

    _write_run(
        runs_root,
        run_id="auto-classic-2026-02-03T15-14-32Z",
        mtime=datetime(2026, 2, 3, 15, 14, tzinfo=timezone.utc),
        weights=[("BTC", 0.5), ("ETH", 0.5)],
    )

    store = refresh_monthly_snapshots_store(
        _settings(runs_root),
        live_start_date="2026-02-01",
        run_prefix="auto-classic",
    )

    months = sorted({item.month for item in store.snapshots})
    assert months == ["2025-12", "2026-01", "2026-02"]
    assert store.current_month == "2026-02"

    jan_backtest = next(item for item in store.backtest_snapshots if item.month == "2026-01")
    assert jan_backtest.source == "backtest"
    assert jan_backtest.run_id == "backtest-classic-2026-01"

    feb_live = next(item for item in store.live_snapshots if item.month == "2026-02")
    assert feb_live.source == "auto"


def test_refresh_monthly_snapshots_store_extends_backtest_with_results_backup(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    checkpoint_path = runs_root / "_performance" / "runs" / "classic" / "checkpoint_weights.csv"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"date": "2025-09-01", "BTC": 0.6, "ETH": 0.4},
            {"date": "2025-10-01", "BTC": 0.55, "ETH": 0.45},
        ]
    ).set_index("date").to_csv(checkpoint_path)

    backup_root = runs_root.parent / "Results_Backup" / "Backup_without_naive"
    backup_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"date": "2021-08-01", "BTC": 0.7, "ETH": 0.3},
            {"date": "2025-08-01", "BTC": 0.65, "ETH": 0.35},
        ]
    ).set_index("date").to_csv(backup_root / "checkpoint_weights.csv")

    _write_run(
        runs_root,
        run_id="auto-classic-2026-02-03T15-14-32Z",
        mtime=datetime(2026, 2, 3, 15, 14, tzinfo=timezone.utc),
        weights=[("BTC", 0.5), ("ETH", 0.5)],
    )

    store = refresh_monthly_snapshots_store(
        _settings(runs_root),
        live_start_date="2026-02-01",
        run_prefix="auto-classic",
    )

    months = sorted({item.month for item in store.snapshots})
    assert months == ["2021-08", "2025-08", "2025-09", "2025-10", "2026-02"]
    assert store.current_month == "2026-02"

    assert any(
        item.month == "2021-08" and item.source == "backtest"
        for item in store.backtest_snapshots
    )
    assert any(
        item.month == "2025-09" and item.source == "backtest"
        for item in store.backtest_snapshots
    )
    assert any(
        item.month == "2026-02" and item.source == "auto"
        for item in store.live_snapshots
    )


def test_refresh_monthly_snapshots_store_does_not_fallback_to_other_prefix_runs(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    _write_run(
        runs_root,
        run_id="auto-classic-2026-02-03T15-14-32Z",
        mtime=datetime(2026, 2, 3, 15, 14, tzinfo=timezone.utc),
        weights=[("BTC", 0.5), ("ETH", 0.5)],
    )

    store = refresh_monthly_snapshots_store(
        _settings(runs_root),
        live_start_date="2026-02-01",
        run_prefix="auto-conservative",
    )

    assert store.current_month is None
    assert store.snapshots == []
    assert store.live_snapshots == []
    assert store.backtest_snapshots == []


def test_refresh_monthly_snapshots_store_uses_equity_curve_month_for_first_day_run(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    _write_run(
        runs_root,
        run_id="auto-conservative-2026-03-01T03-21-10Z",
        mtime=datetime(2026, 3, 1, 3, 21, tzinfo=timezone.utc),
        weights=[("BTC", 0.6), ("ETH", 0.4)],
        equity_dates=["2026-02-01", "2026-02-28"],
    )

    store = refresh_monthly_snapshots_store(
        _settings(runs_root),
        live_start_date="2026-02-01",
        run_prefix="auto-conservative",
    )

    months = sorted({item.month for item in store.snapshots})
    assert months == ["2026-02"]
    assert store.current_month == "2026-02"
    assert all(item.month == "2026-02" for item in store.live_snapshots)
