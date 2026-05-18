from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException

from ai_crypto_index.api import app as api_app
from ai_crypto_index.shared.monthly_job_lock import hold_monthly_job_lock


def _write_run_artifacts(run_dir) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "weights.csv").write_text("asset,weight\nBTC,0.6\nETH,0.4\n", encoding="utf-8")
    (run_dir / "perf.json").write_text(json.dumps({"sharpe": 1.2}), encoding="utf-8")
    (run_dir / "equity_curve.csv").write_text(
        "date,equity_curve\n2026-01-01,1.0\n2026-01-02,1.1\n",
        encoding="utf-8",
    )


def test_run_index_auto_runs_all_profiles_with_profiled_kwargs(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(runs_root=runs_root)
    calls: list[dict[str, object]] = []

    def fake_run_monthly_update(**kwargs):
        calls.append(dict(kwargs))
        run_id = str(kwargs["run_id"])
        _write_run_artifacts(runs_root / run_id)
        return {"BTC": 0.6, "ETH": 0.4}, {"sharpe": 1.2}

    async def fake_persist_index_run_record(*args, **kwargs):
        return None

    monkeypatch.setattr(api_app, "run_monthly_update", fake_run_monthly_update)
    monkeypatch.setattr(api_app, "_persist_index_run_record", fake_persist_index_run_record)

    strategy_runs = asyncio.run(
        api_app._run_index_auto(settings, force=True, target_month=date(2026, 2, 1))
    )

    assert len(strategy_runs) == 3
    assert len(calls) == 3

    expected_profiles_by_prefix = {
        profile.run_prefix: profile for profile in api_app._index_auto_profiles()
    }
    assert {profile.strategy_key for profile in expected_profiles_by_prefix.values()} == {
        "classic",
        "conservative",
        "aggressive",
    }

    for call in calls:
        run_id = str(call["run_id"])
        profile = next(
            profile
            for profile in expected_profiles_by_prefix.values()
            if run_id.startswith(f"{profile.run_prefix}-")
        )
        assert call["fresh_data"] is True
        assert call["info_messages"] is False
        assert call["visualization"] is False
        for key, value in profile.run_kwargs.items():
            assert call[key] == value

    for record in strategy_runs:
        run_dir = runs_root / str(record["run_id"])
        meta_payload = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta_payload["source"] == "auto"
        assert meta_payload["strategy"] == record["strategy_key"]
        assert meta_payload["tag"] == record["run_prefix"]
        assert isinstance(meta_payload.get("run_profile"), dict)
        assert (run_dir / "weights.csv").exists()
        assert (run_dir / "perf.json").exists()
        assert (run_dir / "equity_curve.csv").exists()


def test_run_index_auto_skips_strategy_already_ran_this_month_when_not_forced(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(runs_root=runs_root)

    classic_profile = next(profile for profile in api_app._index_auto_profiles() if profile.strategy_key == "classic")
    existing_run = runs_root / f"{classic_profile.run_prefix}-existing"
    _write_run_artifacts(existing_run)
    (existing_run / "meta.json").write_text(
        json.dumps(
            {
                "source": "auto",
                "strategy": "classic",
                "tag": classic_profile.run_prefix,
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ),
        encoding="utf-8",
    )
    now_ts = datetime.now(timezone.utc).timestamp()
    os.utime(existing_run, (now_ts, now_ts))
    os.utime(existing_run / "weights.csv", (now_ts, now_ts))

    calls: list[dict[str, object]] = []

    def fake_run_monthly_update(**kwargs):
        calls.append(dict(kwargs))
        run_id = str(kwargs["run_id"])
        _write_run_artifacts(runs_root / run_id)
        return {"BTC": 0.6}, {"sharpe": 1.0}

    async def fake_persist_index_run_record(*args, **kwargs):
        return None

    monkeypatch.setattr(api_app, "run_monthly_update", fake_run_monthly_update)
    monkeypatch.setattr(api_app, "_persist_index_run_record", fake_persist_index_run_record)

    strategy_runs = asyncio.run(
        api_app._run_index_auto(settings, force=False, target_month=date.today())
    )

    assert len(strategy_runs) == 2
    assert len(calls) == 2
    assert all(not str(call["run_id"]).startswith(classic_profile.run_prefix) for call in calls)


def test_run_index_auto_idempotent_within_same_month(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(runs_root=runs_root)
    calls: list[str] = []

    def fake_run_monthly_update(**kwargs):
        run_id = str(kwargs["run_id"])
        calls.append(run_id)
        _write_run_artifacts(runs_root / run_id)
        return {"BTC": 0.5, "ETH": 0.5}, {"sharpe": 1.1}

    async def fake_persist_index_run_record(*args, **kwargs):
        return None

    monkeypatch.setattr(api_app, "run_monthly_update", fake_run_monthly_update)
    monkeypatch.setattr(api_app, "_persist_index_run_record", fake_persist_index_run_record)

    first = asyncio.run(api_app._run_index_auto(settings, force=False, target_month=date.today()))
    second = asyncio.run(api_app._run_index_auto(settings, force=False, target_month=date.today()))

    assert len(first) == 3
    assert second == []
    assert len(calls) == 3


def test_maybe_run_index_auto_skips_when_distributed_lock_held(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(runs_root=runs_root)
    config = api_app.AutoRunConfig(enabled=True, next_run_date=date.today())
    called = {"count": 0}
    monkeypatch.setenv("AICI_ENABLE_PIPELINE", "1")

    monkeypatch.setattr(api_app, "_latest_index_auto_run_date_across_profiles", lambda _settings: None)
    monkeypatch.setattr(api_app, "_load_index_auto_config", lambda _settings, latest_run_date=None: config)

    async def fake_run_index_auto(*args, **kwargs):
        called["count"] += 1
        return []

    monkeypatch.setattr(api_app, "_run_index_auto", fake_run_index_auto)

    with hold_monthly_job_lock(
        runs_root,
        contour=api_app._INDEX_AUTO_LOCK_CONTOUR,
        target_month=date.today(),
        stale_after_seconds=60,
    ):
        resolved = asyncio.run(api_app._maybe_run_index_auto(settings))

    assert resolved is config
    assert called["count"] == 0


def test_trigger_performance_refresh_conflicts_when_distributed_lock_held(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(runs_root=runs_root)
    config = api_app.AutoRunConfig(enabled=True, next_run_date=date.today())

    with hold_monthly_job_lock(
        runs_root,
        contour=api_app._PERFORMANCE_AUTO_LOCK_CONTOUR,
        target_month=date.today(),
        stale_after_seconds=60,
    ):
        try:
            asyncio.run(
                api_app._trigger_performance_refresh(
                    settings,
                    reason="auto",
                    config=config,
                    snapshots=[],
                    benchmark_snapshots=[],
                )
            )
        except HTTPException as exc:
            assert exc.status_code == 409
            assert exc.detail == "performance_refresh_running"
        else:
            raise AssertionError("Expected HTTPException for locked performance monthly job")


def test_maybe_run_index_auto_triggers_post_refresh_when_strategy_runs_exist(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(runs_root=runs_root)
    config = api_app.AutoRunConfig(enabled=True, next_run_date=date.today())
    post_refresh_calls: list[dict[str, object]] = []
    monkeypatch.setenv("AICI_ENABLE_PIPELINE", "1")

    monkeypatch.setattr(api_app, "_latest_index_auto_run_date_across_profiles", lambda _settings: None)
    monkeypatch.setattr(api_app, "_load_index_auto_config", lambda _settings, latest_run_date=None: config)

    async def fake_run_index_auto(*args, **kwargs):
        return [{"strategy_key": "classic", "run_id": "auto-classic-2026-03-01T00-00-00Z"}]

    async def fake_post_refresh(_settings, *, strategy_runs, reason):
        post_refresh_calls.append({"strategy_runs": strategy_runs, "reason": reason})

    monkeypatch.setattr(api_app, "_run_index_auto", fake_run_index_auto)
    monkeypatch.setattr(api_app, "_update_index_auto_after_success", lambda *args, **kwargs: config)
    monkeypatch.setattr(api_app, "_trigger_performance_refresh_after_index_auto", fake_post_refresh)

    resolved = asyncio.run(api_app._maybe_run_index_auto(settings))

    assert resolved is config
    assert len(post_refresh_calls) == 1
    assert post_refresh_calls[0]["reason"] == "index_auto"
    assert len(post_refresh_calls[0]["strategy_runs"]) == 1


def test_post_index_auto_refresh_does_not_raise_when_trigger_fails(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(runs_root=runs_root)
    config = api_app.AutoRunConfig(enabled=True, next_run_date=date.today())

    monkeypatch.setattr(api_app, "_load_auto_config_with_latest", lambda _settings: (config, [], []))

    async def fake_trigger_performance_refresh(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(api_app, "_trigger_performance_refresh", fake_trigger_performance_refresh)

    asyncio.run(
        api_app._trigger_performance_refresh_after_index_auto(
            settings,
            strategy_runs=[{"strategy_key": "classic"}],
            reason="index_auto",
        )
    )
