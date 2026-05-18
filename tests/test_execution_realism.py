from datetime import date

import numpy as np
import pandas as pd

from ai_crypto_index.utils.execution_realism import apply_execution_realism_filters


def test_apply_execution_realism_filters_enforces_region_listing_and_delisting() -> None:
    idx = pd.date_range("2026-02-10", periods=5, freq="D")
    df = pd.DataFrame(
        {
            "BTC": [1.0, 1.1, 1.2, 1.3, 1.4],
            "PAXG": [10.0, 10.1, 10.2, 10.3, 10.4],
            "UNL": [5.0, 5.1, 5.2, 5.3, 5.4],
            "OLD": [2.0, 2.1, np.nan, np.nan, np.nan],
        },
        index=idx,
    )
    cfg = {
        "enabled": True,
        "target_venue": "binance_spot",
        "target_region": "EEA",
        "require_tradable_on_target_venue": True,
        "require_region_allowed": True,
        "require_not_delisted_at_t": True,
        "tail_grace_days": 1,
        "listings": {"binance_spot": {"unlisted_symbols": ["UNL"]}},
        "region_restrictions": {
            "binance_spot": {
                "EEA": {
                    "blocked_symbols": ["PAXG"],
                }
            }
        },
        "delisted_at": {"binance_spot": {"OLD": "2026-02-12"}},
    }

    filtered, excluded, context = apply_execution_realism_filters(
        df,
        execution_cfg=cfg,
        as_of_date=date(2026, 2, 14),
    )

    assert list(filtered.columns) == ["BTC"]
    excluded_by_asset = {item["asset"]: item["reasons"] for item in excluded}
    assert "region_restricted" in excluded_by_asset["PAXG"]
    assert "not_listed_on_target_venue" in excluded_by_asset["UNL"]
    assert "delisted_on_target_venue" in excluded_by_asset["OLD"]
    assert context["target_region"] == "EEA"


def test_apply_execution_realism_filters_can_be_disabled() -> None:
    idx = pd.date_range("2026-02-10", periods=3, freq="D")
    df = pd.DataFrame({"BTC": [1.0, 1.1, 1.2], "ETH": [2.0, 2.1, 2.2]}, index=idx)

    filtered, excluded, context = apply_execution_realism_filters(
        df,
        execution_cfg={"enabled": False},
        as_of_date=date(2026, 2, 12),
    )

    assert filtered.equals(df)
    assert excluded == []
    assert context["enabled"] is False
