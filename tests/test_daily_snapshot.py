import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from ai_crypto_index.shared import daily_snapshot


def _build_settings(config_path: Path, runs_root: Path):
    return SimpleNamespace(config_path=config_path, runs_root=runs_root)


def _write_snapshot_csv(snapshot_root: Path, *, day: datetime, n_top_coins: int, df: pd.DataFrame) -> None:
    target_dir = snapshot_root / day.date().isoformat()
    target_dir.mkdir(parents=True, exist_ok=True)
    payload = df.copy()
    payload.index = pd.to_datetime(payload.index)
    payload.index.name = "Date"
    payload.to_csv(target_dir / f"n{n_top_coins}.csv")


def _write_config(config_path: Path, *, start_date: str, data_cfg: dict | None = None) -> None:
    cfg = {
        "data": {
            "dropna_all": True,
            "min_history_days": 1,
            "include_delisted": True,
            "allow_internal_gaps": True,
            "tail_grace_days": 3,
        },
        "market_data": {"start_date": start_date},
        "daily_snapshot": {"base_uri": "file:///snapshots"},
    }
    if data_cfg:
        cfg["data"].update(data_cfg)
    config_path.write_text(json.dumps(cfg), encoding="utf-8")


def _install_fake_parquet_writer(monkeypatch, target_now: datetime, captured: dict[str, pd.DataFrame]) -> None:
    def _fake_write_parquet(df: pd.DataFrame, path: Path) -> None:
        captured["df"] = df.copy()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fresh-{target_now.date().isoformat()}", encoding="utf-8")

    monkeypatch.setattr(daily_snapshot, "_write_parquet", _fake_write_parquet)


def test_refresh_daily_snapshot_prunes_old_date_dirs_and_updates_markers(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    snapshot_root = runs_root / "_daily_snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    target_now = datetime(2026, 2, 14, tzinfo=timezone.utc)
    target_date = target_now.date()

    existing_days = [target_date - timedelta(days=offset) for offset in (4, 3, 2, 1)]
    for day in existing_days:
        day_str = day.isoformat()
        daily_dir = snapshot_root / day_str
        daily_dir.mkdir(parents=True, exist_ok=True)
        (daily_dir / "n100.parquet").write_text(f"old-{day_str}", encoding="utf-8")

    custom_dir = snapshot_root / "custom" / "n115"
    custom_dir.mkdir(parents=True, exist_ok=True)
    custom_meta = custom_dir / "meta.json"
    custom_meta.write_text('{"ok": true}', encoding="utf-8")

    config_path = tmp_path / "pipeline.json"
    config_path.write_text(
        json.dumps(
            {
                "data": {},
                "market_data": {"start_date": "2021-01-01"},
                "daily_snapshot": {"base_uri": "file:///snapshots"},
            }
        ),
        encoding="utf-8",
    )

    def _fake_download_and_merge(symbols, *, start_date, end_date, tmp_dir, data_cfg):
        assert symbols == ["BTC", "ETH"]
        assert start_date == "2021-01-01"
        assert end_date == target_date.isoformat()
        assert tmp_dir.exists()
        return pd.DataFrame({"BTC": [1.0], "ETH": [0.5]}, index=pd.to_datetime([target_date.isoformat()]))

    monkeypatch.setattr(daily_snapshot, "get_top_n_cryptos_cmc", lambda n: ["BTC-USD", "ETH-USD"])
    monkeypatch.setattr(daily_snapshot, "_download_and_merge", _fake_download_and_merge)
    monkeypatch.setattr(
        daily_snapshot,
        "_write_parquet",
        lambda df, path: path.write_text(f"fresh-{target_date.isoformat()}", encoding="utf-8"),
    )

    settings = _build_settings(config_path, runs_root)
    meta = daily_snapshot.refresh_daily_snapshot(
        settings,
        n_top_coins=100,
        base_uri="file:///snapshots",
        retry_delays=(),
        now=target_now,
    )

    assert meta.source_date == target_date
    assert meta.stale is False

    assert (snapshot_root / (target_date - timedelta(days=4)).isoformat()).exists() is False
    assert (snapshot_root / (target_date - timedelta(days=3)).isoformat()).exists() is False
    assert (snapshot_root / (target_date - timedelta(days=2)).isoformat()).exists() is True
    assert (snapshot_root / (target_date - timedelta(days=1)).isoformat()).exists() is True
    assert (snapshot_root / target_date.isoformat()).exists() is True

    assert custom_meta.exists()

    state_payload = json.loads((snapshot_root / "state.json").read_text(encoding="utf-8"))
    expected_snapshot = snapshot_root / target_date.isoformat() / "n100.parquet"
    assert Path(state_payload["local_path"]).resolve() == expected_snapshot.resolve()

    marker_path = snapshot_root / "latest_n100.parquet"
    assert marker_path.exists()
    assert marker_path.read_text(encoding="utf-8") == f"fresh-{target_date.isoformat()}"


def test_refresh_daily_snapshot_incremental_updates_only_tail(tmp_path, monkeypatch, caplog):
    runs_root = tmp_path / "runs"
    snapshot_root = runs_root / "_daily_snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    target_now = datetime(2026, 2, 14, tzinfo=timezone.utc)

    old_index = pd.date_range("2026-02-10", "2026-02-13", freq="D")
    old_df = pd.DataFrame({"BTC": [1.0, 2.0, 3.0, 4.0], "ETH": [11.0, 12.0, 13.0, 14.0]}, index=old_index)
    _write_snapshot_csv(snapshot_root, day=target_now - timedelta(days=1), n_top_coins=100, df=old_df)

    config_path = tmp_path / "pipeline.json"
    _write_config(config_path, start_date="2026-02-10")
    settings = _build_settings(config_path, runs_root)

    download_calls: list[tuple[list[str], str, str]] = []

    def _fake_download(symbols, start_date, end_date, data_folder="data", progress_callback=None):
        download_calls.append((list(symbols), start_date, end_date))
        assert list(symbols) == ["BTC", "ETH"]
        assert start_date == "2026-02-11"
        assert end_date == "2026-02-14"
        folder = Path(data_folder)
        folder.mkdir(parents=True, exist_ok=True)
        idx = pd.date_range(start=start_date, end=end_date, freq="D")
        btc = pd.DataFrame({"Date": idx, "BTC": [101.0, 102.0, 103.0, 104.0]})
        eth = pd.DataFrame({"Date": idx, "ETH": [201.0, 202.0, 203.0, 204.0]})
        btc.to_csv(folder / "BTC.csv", index=False)
        eth.to_csv(folder / "ETH.csv", index=False)

    monkeypatch.setattr(daily_snapshot, "get_top_n_cryptos_cmc", lambda n: ["BTC-USD", "ETH-USD"])
    monkeypatch.setattr(daily_snapshot, "download_multiple_cryptos", _fake_download)
    monkeypatch.setattr(daily_snapshot, "_download_and_merge", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full flow should not run")))
    captured: dict[str, pd.DataFrame] = {}
    _install_fake_parquet_writer(monkeypatch, target_now, captured)
    caplog.set_level(logging.INFO, logger="ai_crypto_index.daily_snapshot")

    meta = daily_snapshot.refresh_daily_snapshot(
        settings,
        n_top_coins=100,
        base_uri="file:///snapshots",
        retry_delays=(),
        now=target_now,
    )

    assert meta.stale is False
    assert len(download_calls) == 1
    assert "mode=incremental" in caplog.text
    result = captured["df"]
    assert list(result.columns) == ["BTC", "ETH"]
    assert result.loc[pd.Timestamp("2026-02-10"), "BTC"] == 1.0
    assert result.loc[pd.Timestamp("2026-02-11"), "BTC"] == 101.0
    assert result.loc[pd.Timestamp("2026-02-14"), "ETH"] == 204.0


def test_refresh_daily_snapshot_incremental_adds_new_ticker_with_full_history(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    snapshot_root = runs_root / "_daily_snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    target_now = datetime(2026, 2, 14, tzinfo=timezone.utc)

    old_index = pd.date_range("2026-02-10", "2026-02-13", freq="D")
    old_df = pd.DataFrame({"BTC": [1.0, 2.0, 3.0, 4.0]}, index=old_index)
    _write_snapshot_csv(snapshot_root, day=target_now - timedelta(days=1), n_top_coins=100, df=old_df)

    config_path = tmp_path / "pipeline.json"
    _write_config(config_path, start_date="2026-02-10")
    settings = _build_settings(config_path, runs_root)

    download_calls: list[tuple[list[str], str, str]] = []

    def _fake_download(symbols, start_date, end_date, data_folder="data", progress_callback=None):
        download_calls.append((list(symbols), start_date, end_date))
        folder = Path(data_folder)
        folder.mkdir(parents=True, exist_ok=True)
        idx = pd.date_range(start=start_date, end=end_date, freq="D")
        for symbol in symbols:
            values = [50.0 + i for i in range(len(idx))] if symbol == "BTC" else [500.0 + i for i in range(len(idx))]
            pd.DataFrame({"Date": idx, symbol: values}).to_csv(folder / f"{symbol}.csv", index=False)

    monkeypatch.setattr(daily_snapshot, "get_top_n_cryptos_cmc", lambda n: ["BTC", "ETH"])
    monkeypatch.setattr(daily_snapshot, "download_multiple_cryptos", _fake_download)
    captured: dict[str, pd.DataFrame] = {}
    _install_fake_parquet_writer(monkeypatch, target_now, captured)

    daily_snapshot.refresh_daily_snapshot(
        settings,
        n_top_coins=100,
        base_uri="file:///snapshots",
        retry_delays=(),
        now=target_now,
    )

    assert download_calls[0] == (["BTC"], "2026-02-11", "2026-02-14")
    assert download_calls[1] == (["ETH"], "2026-02-10", "2026-02-14")
    result = captured["df"]
    assert list(result.columns) == ["BTC", "ETH"]
    assert pd.notna(result.loc[pd.Timestamp("2026-02-10"), "ETH"])


def test_refresh_daily_snapshot_incremental_removes_ticker_out_of_top(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    snapshot_root = runs_root / "_daily_snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    target_now = datetime(2026, 2, 14, tzinfo=timezone.utc)

    old_index = pd.date_range("2026-02-10", "2026-02-13", freq="D")
    old_df = pd.DataFrame({"BTC": [1.0, 2.0, 3.0, 4.0], "ETH": [11.0, 12.0, 13.0, 14.0]}, index=old_index)
    _write_snapshot_csv(snapshot_root, day=target_now - timedelta(days=1), n_top_coins=100, df=old_df)

    config_path = tmp_path / "pipeline.json"
    _write_config(config_path, start_date="2026-02-10")
    settings = _build_settings(config_path, runs_root)

    def _fake_download(symbols, start_date, end_date, data_folder="data", progress_callback=None):
        assert list(symbols) == ["BTC"]
        idx = pd.date_range(start=start_date, end=end_date, freq="D")
        folder = Path(data_folder)
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"Date": idx, "BTC": [91.0, 92.0, 93.0, 94.0]}).to_csv(folder / "BTC.csv", index=False)

    monkeypatch.setattr(daily_snapshot, "get_top_n_cryptos_cmc", lambda n: ["BTC-USD"])
    monkeypatch.setattr(daily_snapshot, "download_multiple_cryptos", _fake_download)
    captured: dict[str, pd.DataFrame] = {}
    _install_fake_parquet_writer(monkeypatch, target_now, captured)

    daily_snapshot.refresh_daily_snapshot(
        settings,
        n_top_coins=100,
        base_uri="file:///snapshots",
        retry_delays=(),
        now=target_now,
    )

    assert list(captured["df"].columns) == ["BTC"]


def test_refresh_daily_snapshot_incremental_preserves_existing_ticker_when_tail_missing(tmp_path, monkeypatch, caplog):
    runs_root = tmp_path / "runs"
    snapshot_root = runs_root / "_daily_snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    target_now = datetime(2026, 2, 14, tzinfo=timezone.utc)

    old_index = pd.date_range("2026-02-10", "2026-02-13", freq="D")
    old_df = pd.DataFrame({"BTC": [1.0, 2.0, 3.0, 4.0], "LEO": [11.0, 12.0, 13.0, 14.0]}, index=old_index)
    _write_snapshot_csv(snapshot_root, day=target_now - timedelta(days=1), n_top_coins=100, df=old_df)

    config_path = tmp_path / "pipeline.json"
    _write_config(config_path, start_date="2026-02-10")
    settings = _build_settings(config_path, runs_root)

    def _fake_download(symbols, start_date, end_date, data_folder="data", progress_callback=None):
        assert list(symbols) == ["BTC", "LEO"]
        idx = pd.date_range(start=start_date, end=end_date, freq="D")
        folder = Path(data_folder)
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"Date": idx, "BTC": [101.0, 102.0, 103.0, 104.0]}).to_csv(folder / "BTC.csv", index=False)

    monkeypatch.setattr(daily_snapshot, "get_top_n_cryptos_cmc", lambda n: ["BTC-USD", "LEO-USD"])
    monkeypatch.setattr(daily_snapshot, "download_multiple_cryptos", _fake_download)
    monkeypatch.setattr(
        daily_snapshot,
        "_download_and_merge",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full flow should not run")),
    )
    captured: dict[str, pd.DataFrame] = {}
    _install_fake_parquet_writer(monkeypatch, target_now, captured)
    caplog.set_level(logging.INFO, logger="ai_crypto_index.daily_snapshot")

    daily_snapshot.refresh_daily_snapshot(
        settings,
        n_top_coins=100,
        base_uri="file:///snapshots",
        retry_delays=(),
        now=target_now,
    )

    assert "mode=incremental" in caplog.text
    assert "Incremental tail missing for LEO" in caplog.text
    result = captured["df"]
    assert list(result.columns) == ["BTC", "LEO"]
    assert result.loc[pd.Timestamp("2026-02-13"), "LEO"] == 14.0
    assert pd.isna(result.loc[pd.Timestamp("2026-02-14"), "LEO"])


def test_refresh_daily_snapshot_runs_scheduled_full_rebuild(tmp_path, monkeypatch, caplog):
    runs_root = tmp_path / "runs"
    snapshot_root = runs_root / "_daily_snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    target_now = datetime(2026, 2, 15, tzinfo=timezone.utc)

    old_index = pd.date_range("2026-02-10", "2026-02-14", freq="D")
    old_df = pd.DataFrame({"BTC": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=old_index)
    _write_snapshot_csv(snapshot_root, day=target_now - timedelta(days=1), n_top_coins=100, df=old_df)

    config_path = tmp_path / "pipeline.json"
    _write_config(config_path, start_date="2026-02-10")
    settings = _build_settings(config_path, runs_root)

    monkeypatch.setattr(daily_snapshot, "get_top_n_cryptos_cmc", lambda n: ["BTC"])
    monkeypatch.setattr(
        daily_snapshot,
        "_build_incremental_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("incremental should not run on scheduled full rebuild day")),
    )
    monkeypatch.setattr(
        daily_snapshot,
        "_download_and_merge",
        lambda symbols, **kwargs: pd.DataFrame({"BTC": [100.0]}, index=pd.to_datetime(["2026-02-15"])),
    )
    captured: dict[str, pd.DataFrame] = {}
    _install_fake_parquet_writer(monkeypatch, target_now, captured)
    caplog.set_level(logging.INFO, logger="ai_crypto_index.daily_snapshot")

    daily_snapshot.refresh_daily_snapshot(
        settings,
        n_top_coins=100,
        base_uri="file:///snapshots",
        retry_delays=(),
        now=target_now,
    )

    assert "mode=full_rebuild" in caplog.text
    assert list(captured["df"].columns) == ["BTC"]


def test_refresh_daily_snapshot_fallbacks_to_full_when_incremental_fails(tmp_path, monkeypatch, caplog):
    runs_root = tmp_path / "runs"
    snapshot_root = runs_root / "_daily_snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    target_now = datetime(2026, 2, 14, tzinfo=timezone.utc)

    old_index = pd.date_range("2026-02-10", "2026-02-13", freq="D")
    old_df = pd.DataFrame({"BTC": [1.0, 2.0, 3.0, 4.0]}, index=old_index)
    _write_snapshot_csv(snapshot_root, day=target_now - timedelta(days=1), n_top_coins=100, df=old_df)

    config_path = tmp_path / "pipeline.json"
    _write_config(config_path, start_date="2026-02-10")
    settings = _build_settings(config_path, runs_root)

    monkeypatch.setattr(daily_snapshot, "get_top_n_cryptos_cmc", lambda n: ["BTC"])
    monkeypatch.setattr(
        daily_snapshot,
        "_build_incremental_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(daily_snapshot.DailySnapshotError("incremental failed")),
    )
    monkeypatch.setattr(
        daily_snapshot,
        "_download_and_merge",
        lambda symbols, **kwargs: pd.DataFrame({"BTC": [999.0]}, index=pd.to_datetime(["2026-02-14"])),
    )
    captured: dict[str, pd.DataFrame] = {}
    _install_fake_parquet_writer(monkeypatch, target_now, captured)
    caplog.set_level(logging.INFO, logger="ai_crypto_index.daily_snapshot")

    meta = daily_snapshot.refresh_daily_snapshot(
        settings,
        n_top_coins=100,
        base_uri="file:///snapshots",
        retry_delays=(),
        now=target_now,
    )

    assert meta.stale is False
    assert "mode=fallback_full" in caplog.text
    assert list(captured["df"].columns) == ["BTC"]
