from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

DEFAULT_TARGET_VENUE = "binance_spot"
DEFAULT_TARGET_REGION = "GLOBAL"
DEFAULT_TAIL_GRACE_DAYS = 3


def _normalize_symbol(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return ""
    if normalized.endswith("-USD"):
        normalized = normalized[:-4]
    return normalized


def _coerce_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _find_ci(mapping: Mapping[str, Any], key: str) -> Any:
    target = str(key or "").strip().lower()
    for raw_key, raw_value in mapping.items():
        if str(raw_key or "").strip().lower() == target:
            return raw_value
    return None


def _normalize_symbol_set(values: object) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        normalized = _normalize_symbol(values)
        return {normalized} if normalized else set()
    if not isinstance(values, Sequence):
        return set()
    result: set[str] = set()
    for value in values:
        normalized = _normalize_symbol(value)
        if normalized:
            result.add(normalized)
    return result


def _parse_date_value(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if "T" in candidate:
            candidate = candidate.split("T", 1)[0]
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            return None
    return None


def _extract_delisted_map(
    cfg: Mapping[str, Any],
    *,
    target_venue: str,
) -> dict[str, date]:
    by_venue = _coerce_mapping(cfg.get("delisted_at"))
    raw_venue_map = _find_ci(by_venue, target_venue)
    venue_map = _coerce_mapping(raw_venue_map)
    parsed: dict[str, date] = {}
    for raw_symbol, raw_value in venue_map.items():
        symbol = _normalize_symbol(raw_symbol)
        if not symbol:
            continue
        if isinstance(raw_value, Mapping):
            raw_date = raw_value.get("date") or raw_value.get("delisted_at")
        else:
            raw_date = raw_value
        delisted_on = _parse_date_value(raw_date)
        if delisted_on is not None:
            parsed[symbol] = delisted_on
    return parsed


def apply_execution_realism_filters(
    df: pd.DataFrame,
    *,
    execution_cfg: Mapping[str, Any] | None,
    as_of_date: date,
    default_tail_grace_days: int = DEFAULT_TAIL_GRACE_DAYS,
) -> tuple[pd.DataFrame, list[dict[str, str]], dict[str, object]]:
    cfg = _coerce_mapping(execution_cfg)
    enabled = bool(cfg.get("enabled", True))
    target_venue = str(cfg.get("target_venue", DEFAULT_TARGET_VENUE) or DEFAULT_TARGET_VENUE).strip().lower()
    target_region = str(cfg.get("target_region", DEFAULT_TARGET_REGION) or DEFAULT_TARGET_REGION).strip().upper()
    require_tradable = bool(cfg.get("require_tradable_on_target_venue", True))
    require_region_allowed = bool(cfg.get("require_region_allowed", True))
    require_not_delisted = bool(cfg.get("require_not_delisted_at_t", True))
    try:
        tail_grace_days = int(cfg.get("tail_grace_days", default_tail_grace_days))
    except (TypeError, ValueError):
        tail_grace_days = default_tail_grace_days
    tail_grace_days = max(0, int(tail_grace_days))

    context: dict[str, object] = {
        "enabled": enabled,
        "target_venue": target_venue,
        "target_region": target_region,
        "as_of_date": as_of_date.isoformat(),
        "tail_grace_days": tail_grace_days,
    }
    if not enabled or df.empty:
        return df, [], context

    listings_cfg = _coerce_mapping(_find_ci(_coerce_mapping(cfg.get("listings")), target_venue))
    listed_symbols = _normalize_symbol_set(listings_cfg.get("listed_symbols"))
    unlisted_symbols = _normalize_symbol_set(listings_cfg.get("unlisted_symbols"))

    region_root = _coerce_mapping(cfg.get("region_restrictions"))
    region_venue_cfg = _coerce_mapping(_find_ci(region_root, target_venue))
    region_policy = _coerce_mapping(_find_ci(region_venue_cfg, target_region))
    blocked_region_symbols = _normalize_symbol_set(region_policy.get("blocked_symbols"))
    allowed_region_symbols = _normalize_symbol_set(region_policy.get("allowed_symbols"))

    delisted_map = _extract_delisted_map(cfg, target_venue=target_venue)

    tail_grace = pd.Timedelta(days=tail_grace_days)
    as_of_ts = pd.Timestamp(as_of_date)
    keep_columns: list[str] = []
    exclusions: list[dict[str, str]] = []

    for column in df.columns:
        symbol = _normalize_symbol(column)
        if not symbol:
            continue
        reasons: list[str] = []
        details: list[str] = []

        series = pd.to_numeric(df[column], errors="coerce")
        up_to_t = series.loc[series.index <= as_of_ts]
        latest_value = up_to_t.iloc[-1] if not up_to_t.empty else float("nan")
        last_valid = up_to_t.last_valid_index() if not up_to_t.empty else None

        if require_tradable:
            if listed_symbols and symbol not in listed_symbols:
                reasons.append("not_listed_on_target_venue")
                details.append(f"{symbol} absent from listed_symbols for {target_venue}")
            if symbol in unlisted_symbols:
                reasons.append("not_listed_on_target_venue")
                details.append(f"{symbol} marked unlisted for {target_venue}")
            if pd.isna(latest_value) or float(latest_value) <= 0.0:
                reasons.append("not_tradable_on_target_venue_at_t")
                details.append(f"missing/invalid last quote at {as_of_date.isoformat()}")

        if require_region_allowed:
            if allowed_region_symbols and symbol not in allowed_region_symbols:
                reasons.append("region_not_allowlisted")
                details.append(f"{symbol} is not allowlisted for {target_region} on {target_venue}")
            if symbol in blocked_region_symbols:
                reasons.append("region_restricted")
                details.append(f"{symbol} blocked for {target_region} on {target_venue}")

        if require_not_delisted:
            explicit_delisted_on = delisted_map.get(symbol)
            if explicit_delisted_on and explicit_delisted_on <= as_of_date:
                reasons.append("delisted_on_target_venue")
                details.append(f"explicit delisted_at={explicit_delisted_on.isoformat()}")
            elif last_valid is None:
                reasons.append("not_delisted_at_t_failed")
                details.append("no valid prices before as_of_date")
            else:
                last_valid_ts = pd.Timestamp(last_valid)
                if (as_of_ts - last_valid_ts) > tail_grace:
                    reasons.append("not_delisted_at_t_failed")
                    details.append(
                        "last valid quote is stale: "
                        f"last_valid={last_valid_ts.date().isoformat()} grace_days={tail_grace_days}"
                    )

        if reasons:
            reason_codes = ",".join(dict.fromkeys(reasons))
            exclusions.append(
                {
                    "asset": symbol,
                    "reasons": reason_codes,
                    "details": "; ".join(dict.fromkeys(details)),
                }
            )
            continue

        keep_columns.append(str(column))

    filtered = df.loc[:, keep_columns]
    context["excluded_count"] = len(exclusions)
    context["kept_count"] = len(keep_columns)
    return filtered, exclusions, context
