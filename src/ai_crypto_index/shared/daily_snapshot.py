from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from ai_crypto_index.fetch_data.data_collection import download_multiple_cryptos
from ai_crypto_index.fetch_data.data_preprocessing.load_and_preprocess import (
    load_and_preprocess_data_fixed,
)
from ai_crypto_index.fetch_data.data_preprocessing.load_top_n_auto import get_top_n_cryptos_cmc
from ai_crypto_index.shared.settings import ServiceSettings

logger = logging.getLogger("ai_crypto_index.daily_snapshot")

DEFAULT_BASE_URI = "s3://ai-ci/daily"
DEFAULT_N_TOP = 100
DEFAULT_SNAPSHOT_HOUR_UTC = 6
DEFAULT_RETRY_DELAYS = (600, 1200)
DEFAULT_DAILY_RETENTION_DAYS = 3
STATE_FILENAME = "state.json"
LATEST_NAME_TEMPLATE = "latest_n{n}.parquet"
CUSTOM_CACHE_DIR = "custom"
CUSTOM_SNAPSHOT_FILENAME = "snapshot.parquet"
CUSTOM_META_FILENAME = "meta.json"
CUSTOM_CLEANUP_STATE_FILENAME = "custom_cleanup_state.json"
DEFAULT_CUSTOM_CACHE_LIMIT = 10
DEFAULT_CUSTOM_CACHE_MIN_LIMIT = 5
DEFAULT_CUSTOM_CACHE_MAX_LIMIT = 10
DEFAULT_CUSTOM_TTL_DAYS = 7
DEFAULT_INCREMENTAL_OVERLAP_DAYS = 2
DEFAULT_FULL_REBUILD_WEEKDAY_UTC = 6  # Sunday


def _env_int(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


_CUSTOM_CACHE_LIMIT_RAW = _env_int("AICI_CUSTOM_SNAPSHOT_LIMIT", DEFAULT_CUSTOM_CACHE_LIMIT)
CUSTOM_CACHE_LIMIT = _clamp_int(
    _CUSTOM_CACHE_LIMIT_RAW,
    DEFAULT_CUSTOM_CACHE_MIN_LIMIT,
    DEFAULT_CUSTOM_CACHE_MAX_LIMIT,
)
CUSTOM_CACHE_TTL_DAYS = max(0, _env_int("AICI_CUSTOM_SNAPSHOT_TTL_DAYS", DEFAULT_CUSTOM_TTL_DAYS))


class DailySnapshotError(Exception):
    """Raised when daily snapshot preparation fails."""


@dataclass
class DailySnapshotMeta:
    snapshot_date: date
    source_date: date
    n_top_coins: int
    local_path: str
    storage_uri: str
    stale: bool
    created_at: datetime
    uploaded: bool
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["snapshot_date"] = self.snapshot_date.isoformat()
        payload["source_date"] = self.source_date.isoformat()
        payload["created_at"] = self.created_at.isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "DailySnapshotMeta":
        def _parse_date(value: object | None) -> date:
            if not value:
                return datetime.now(timezone.utc).date()
            try:
                return date.fromisoformat(str(value))
            except (TypeError, ValueError):
                return datetime.now(timezone.utc).date()

        def _parse_dt(value: object | None) -> datetime:
            if not value:
                return datetime.now(timezone.utc)
            try:
                parsed = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                return datetime.now(timezone.utc)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        return cls(
            snapshot_date=_parse_date(payload.get("snapshot_date")),
            source_date=_parse_date(payload.get("source_date")),
            n_top_coins=int(payload.get("n_top_coins", DEFAULT_N_TOP)),
            local_path=str(payload.get("local_path", "")),
            storage_uri=str(payload.get("storage_uri", "")),
            stale=bool(payload.get("stale", False)),
            created_at=_parse_dt(payload.get("created_at")),
            uploaded=bool(payload.get("uploaded", False)),
            error=str(payload.get("error") or "") or None,
        )


@dataclass
class CustomSnapshotMeta:
    snapshot_date: date
    n_top_coins: int
    local_path: str
    storage_uri: str
    created_at: datetime
    last_access: datetime
    uploaded: bool
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["snapshot_date"] = self.snapshot_date.isoformat()
        payload["created_at"] = self.created_at.isoformat()
        payload["last_access"] = self.last_access.isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CustomSnapshotMeta":
        def _parse_date(value: object | None) -> date:
            if not value:
                return datetime.now(timezone.utc).date()
            try:
                return date.fromisoformat(str(value))
            except (TypeError, ValueError):
                return datetime.now(timezone.utc).date()

        def _parse_dt(value: object | None) -> datetime:
            if not value:
                return datetime.now(timezone.utc)
            try:
                parsed = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                return datetime.now(timezone.utc)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        return cls(
            snapshot_date=_parse_date(payload.get("snapshot_date")),
            n_top_coins=int(payload.get("n_top_coins", DEFAULT_N_TOP)),
            local_path=str(payload.get("local_path", "")),
            storage_uri=str(payload.get("storage_uri", "")),
            created_at=_parse_dt(payload.get("created_at")),
            last_access=_parse_dt(payload.get("last_access")),
            uploaded=bool(payload.get("uploaded", False)),
            error=str(payload.get("error") or "") or None,
        )


@dataclass
class SnapshotSelection:
    source: str
    meta: DailySnapshotMeta | CustomSnapshotMeta
    dataframe: pd.DataFrame


def _base_dir_from_config(config_path: Path) -> Path:
    cfg_dir = config_path.resolve().parent
    if cfg_dir.name.lower() == "config":
        return cfg_dir.parent
    return cfg_dir


def _snapshot_root(settings: ServiceSettings, override: str | None = None) -> Path:
    if override:
        root = Path(override)
    else:
        root = settings.runs_root / "_daily_snapshot"
    return root.resolve()


def resolve_snapshot_root(settings: ServiceSettings, override: str | None = None) -> Path:
    return _snapshot_root(settings, override)


def resolve_snapshot_root_from_runs_root(runs_root: Path, override: str | None = None) -> Path:
    if override:
        root = Path(override)
    else:
        root = Path(runs_root) / "_daily_snapshot"
    return root.resolve()


def _state_path(root: Path) -> Path:
    return root / STATE_FILENAME


def _latest_marker_path(root: Path, n_top_coins: int) -> Path:
    return root / LATEST_NAME_TEMPLATE.format(n=n_top_coins)


def _custom_cache_root(root: Path) -> Path:
    return root / CUSTOM_CACHE_DIR


def _custom_dir(root: Path, n_top_coins: int) -> Path:
    return _custom_cache_root(root) / f"n{n_top_coins}"


def _custom_snapshot_path(root: Path, n_top_coins: int) -> Path:
    return _custom_dir(root, n_top_coins) / CUSTOM_SNAPSHOT_FILENAME


def _custom_meta_path(root: Path, n_top_coins: int) -> Path:
    return _custom_dir(root, n_top_coins) / CUSTOM_META_FILENAME


def _custom_cleanup_state_path(root: Path) -> Path:
    return _custom_cache_root(root) / CUSTOM_CLEANUP_STATE_FILENAME


def _build_custom_storage_uri(base_uri: str, n_top_coins: int) -> str:
    base = base_uri.rstrip("/")
    return f"{base}/custom/{n_top_coins}/snapshot.parquet"


def _load_state(root: Path) -> DailySnapshotMeta | None:
    path = _state_path(root)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return DailySnapshotMeta.from_dict(raw)
    except Exception:
        logger.warning("Failed to parse daily snapshot state at %s", path)
        return None


def _persist_state(root: Path, meta: DailySnapshotMeta) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = meta.to_dict()
    try:
        _state_path(root).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to persist daily snapshot state: %s", exc)


def _build_custom_meta_from_path(
    snapshot_path: Path,
    *,
    n_top_coins: int,
    base_uri: str,
) -> CustomSnapshotMeta:
    created_at = datetime.fromtimestamp(snapshot_path.stat().st_mtime, tz=timezone.utc)
    return CustomSnapshotMeta(
        snapshot_date=created_at.date(),
        n_top_coins=n_top_coins,
        local_path=str(snapshot_path),
        storage_uri=_build_custom_storage_uri(base_uri, n_top_coins),
        created_at=created_at,
        last_access=created_at,
        uploaded=False,
        error=None,
    )


def _load_custom_meta(root: Path, n_top_coins: int, *, base_uri: str) -> CustomSnapshotMeta | None:
    snapshot_path = _custom_snapshot_path(root, n_top_coins)
    if not snapshot_path.exists():
        return None
    meta_path = _custom_meta_path(root, n_top_coins)
    if meta_path.exists():
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                meta = CustomSnapshotMeta.from_dict(raw)
            else:
                meta = _build_custom_meta_from_path(
                    snapshot_path,
                    n_top_coins=n_top_coins,
                    base_uri=base_uri,
                )
        except Exception:
            logger.warning("Failed to parse custom snapshot meta at %s", meta_path)
            meta = _build_custom_meta_from_path(
                snapshot_path,
                n_top_coins=n_top_coins,
                base_uri=base_uri,
            )
    else:
        meta = _build_custom_meta_from_path(
            snapshot_path,
            n_top_coins=n_top_coins,
            base_uri=base_uri,
        )
    if not meta.local_path:
        meta.local_path = str(snapshot_path)
    if not meta.storage_uri:
        meta.storage_uri = _build_custom_storage_uri(base_uri, n_top_coins)
    return meta


def _persist_custom_meta(root: Path, meta: CustomSnapshotMeta) -> None:
    meta_path = _custom_meta_path(root, meta.n_top_coins)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    payload = meta.to_dict()
    try:
        meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to persist custom snapshot meta: %s", exc)


def _load_custom_cleanup_state(root: Path) -> str | None:
    path = _custom_cleanup_state_path(root)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to parse custom snapshot cleanup state at %s", path)
        return None
    if isinstance(raw, dict):
        value = raw.get("last_cleanup_month")
        if isinstance(value, str) and value:
            return value
    return None


def _persist_custom_cleanup_state(root: Path, *, month_key: str, now: datetime) -> None:
    path = _custom_cleanup_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_cleanup_month": month_key,
        "updated_at": now.isoformat(),
    }
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to persist custom snapshot cleanup state: %s", exc)


def _custom_meta_expired(meta: CustomSnapshotMeta, *, now: datetime, ttl_days: int) -> bool:
    if ttl_days <= 0:
        return False
    return now - meta.last_access > timedelta(days=ttl_days)


def _purge_custom_snapshot(root: Path, n_top_coins: int, *, reason: str) -> None:
    target = _custom_dir(root, n_top_coins)
    if not target.exists():
        return
    try:
        shutil.rmtree(target, ignore_errors=True)
        logger.info("Custom snapshot evicted for n_top_coins=%s (reason=%s)", n_top_coins, reason)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to evict custom snapshot for n_top_coins=%s: %s", n_top_coins, exc)


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path, index=True)
    except (ImportError, ModuleNotFoundError) as exc:
        raise DailySnapshotError(
            "pyarrow or fastparquet is required to write parquet snapshots"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise DailySnapshotError(f"failed to persist parquet snapshot: {exc}") from exc


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    normalized = uri.replace("s3://", "", 1)
    bucket, _, key = normalized.partition("/")
    if not bucket or not key:
        raise DailySnapshotError(f"invalid s3 uri: {uri}")
    return bucket, key


def _upload_if_s3(local_path: Path, storage_uri: str) -> bool:
    if not storage_uri.lower().startswith("s3://"):
        return False
    try:
        import boto3  # type: ignore
    except ImportError:
        logger.warning("boto3 not installed; skipping upload to %s", storage_uri)
        return False

    bucket, key = _parse_s3_uri(storage_uri)
    try:
        boto3.client("s3").upload_file(str(local_path), bucket, key)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to upload snapshot to %s: %s", storage_uri, exc)
        return False


def _build_storage_uri(base_uri: str, snapshot_date: date, n_top_coins: int) -> str:
    base = base_uri.rstrip("/")
    return f"{base}/{snapshot_date.isoformat()}/n{n_top_coins}.parquet"


def _iter_custom_snapshot_meta(root: Path, *, base_uri: str) -> list[CustomSnapshotMeta]:
    custom_root = _custom_cache_root(root)
    if not custom_root.exists():
        return []
    items: list[CustomSnapshotMeta] = []
    for child in custom_root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if not name.startswith("n"):
            continue
        try:
            n_top = int(name[1:])
        except ValueError:
            continue
        meta = _load_custom_meta(root, n_top, base_uri=base_uri)
        if meta:
            items.append(meta)
    return items


def prune_custom_snapshots(
    root: Path,
    *,
    base_uri: str,
    ttl_days: int = CUSTOM_CACHE_TTL_DAYS,
    limit: int = CUSTOM_CACHE_LIMIT,
    now: datetime | None = None,
    keep_n: set[int] | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    keep = keep_n or set()
    metas = _iter_custom_snapshot_meta(root, base_uri=base_uri)
    active: list[CustomSnapshotMeta] = []
    for meta in metas:
        if meta.n_top_coins in keep:
            active.append(meta)
            continue
        if _custom_meta_expired(meta, now=now, ttl_days=ttl_days):
            _purge_custom_snapshot(root, meta.n_top_coins, reason="ttl_expired")
        else:
            active.append(meta)

    if limit <= 0 or len(active) <= limit:
        return

    keep_count = len([meta for meta in active if meta.n_top_coins in keep])
    if keep_count > limit:
        logger.info(
            "Custom snapshot limit (%s) is lower than keep set size (%s); skipping eviction.",
            limit,
            keep_count,
        )
        return

    candidates = [meta for meta in active if meta.n_top_coins not in keep]
    candidates.sort(key=lambda meta: meta.last_access)
    to_remove = max(0, len(active) - limit)
    for meta in candidates[:to_remove]:
        _purge_custom_snapshot(root, meta.n_top_coins, reason="limit_exceeded")


def maybe_prune_custom_snapshots_monthly(
    root: Path,
    *,
    base_uri: str,
    ttl_days: int = CUSTOM_CACHE_TTL_DAYS,
    limit: int = CUSTOM_CACHE_LIMIT,
    now: datetime | None = None,
    keep_n: set[int] | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    month_key = f"{now.year:04d}-{now.month:02d}"
    last_cleanup = _load_custom_cleanup_state(root)
    if last_cleanup == month_key:
        return False
    prune_custom_snapshots(
        root,
        base_uri=base_uri,
        ttl_days=ttl_days,
        limit=limit,
        now=now,
        keep_n=keep_n,
    )
    _persist_custom_cleanup_state(root, month_key=month_key, now=now)
    logger.info("Custom snapshot monthly cleanup completed (month=%s)", month_key)
    return True


def load_custom_snapshot_meta(
    root: Path,
    *,
    n_top_coins: int,
    base_uri: str,
    ttl_days: int = CUSTOM_CACHE_TTL_DAYS,
    now: datetime | None = None,
) -> CustomSnapshotMeta | None:
    now = now or datetime.now(timezone.utc)
    meta = _load_custom_meta(root, n_top_coins, base_uri=base_uri)
    if not meta:
        return None
    if _custom_meta_expired(meta, now=now, ttl_days=ttl_days):
        _purge_custom_snapshot(root, n_top_coins, reason="ttl_expired")
        return None
    meta.last_access = now
    _persist_custom_meta(root, meta)
    return meta


def refresh_custom_snapshot(
    *,
    config_path: Path,
    snapshot_root: Path,
    n_top_coins: int,
    base_uri: str,
    now: datetime | None = None,
) -> CustomSnapshotMeta:
    now_dt = now or datetime.now(timezone.utc)
    target_date = now_dt.date()
    root = snapshot_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    config = _load_pipeline_config(config_path)
    data_cfg = config.get("data", {})
    start_date = _resolve_start_date(config, target_date)
    end_date = target_date.isoformat()

    tmp_dir = Path(tempfile.mkdtemp(prefix="custom_snapshot_", dir=root))
    try:
        symbols = _normalize_symbols(get_top_n_cryptos_cmc(n=n_top_coins))
        merged = _download_and_merge(
            symbols,
            start_date=start_date,
            end_date=end_date,
            tmp_dir=tmp_dir,
            data_cfg=data_cfg,
        )
        target_dir = _custom_dir(root, n_top_coins)
        target_dir.mkdir(parents=True, exist_ok=True)
        local_path = target_dir / CUSTOM_SNAPSHOT_FILENAME
        _write_parquet(merged, local_path)
        storage_uri = _build_custom_storage_uri(base_uri, n_top_coins)
        uploaded = _upload_if_s3(local_path, storage_uri)
        meta = CustomSnapshotMeta(
            snapshot_date=target_date,
            n_top_coins=n_top_coins,
            local_path=str(local_path),
            storage_uri=storage_uri,
            created_at=now_dt,
            last_access=now_dt,
            uploaded=uploaded,
            error=None,
        )
        _persist_custom_meta(root, meta)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    prune_custom_snapshots(
        root,
        base_uri=base_uri,
        ttl_days=CUSTOM_CACHE_TTL_DAYS,
        limit=CUSTOM_CACHE_LIMIT,
        now=now_dt,
        keep_n={n_top_coins},
    )
    return meta


def _load_pipeline_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_base_uri(settings: ServiceSettings, override: str | None = None) -> str:
    config = _load_pipeline_config(settings.config_path)
    return (
        override
        or os.getenv("AICI_DAILY_SNAPSHOT_BASE_URI")
        or config.get("daily_snapshot", {}).get("base_uri")
        or DEFAULT_BASE_URI
    )


def resolve_base_uri_from_config(config_path: Path, override: str | None = None) -> str:
    config = _load_pipeline_config(config_path)
    return (
        override
        or os.getenv("AICI_DAILY_SNAPSHOT_BASE_URI")
        or config.get("daily_snapshot", {}).get("base_uri")
        or DEFAULT_BASE_URI
    )


def _resolve_start_date(config: dict, target_date: date) -> str:
    market_cfg = config.get("market_data", {})
    raw = market_cfg.get("start_date")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    year_ago = target_date - timedelta(days=365)
    return year_ago.isoformat()


def _should_run_scheduled_full_rebuild(target_date: date) -> bool:
    return target_date.weekday() == DEFAULT_FULL_REBUILD_WEEKDAY_UTC


def _normalize_symbol(symbol: object) -> str:
    normalized = str(symbol or "").strip()
    if not normalized:
        return ""
    if normalized.upper().endswith("-USD"):
        normalized = normalized[:-4]
    return normalized.upper()


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _normalize_snapshot_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    normalized = df.copy()
    idx = pd.to_datetime(normalized.index, errors="coerce")
    if not isinstance(idx, pd.DatetimeIndex):
        raise DailySnapshotError("snapshot index is not datetime-like")
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    valid = ~idx.isna()
    normalized = normalized.loc[valid].copy()
    normalized.index = idx[valid]
    normalized.sort_index(inplace=True)
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    normalized.columns = [str(col) for col in normalized.columns]
    return normalized


def _read_downloaded_symbol_series(csv_path: Path, symbol: str) -> pd.Series:
    if not csv_path.exists():
        raise DailySnapshotError(f"missing downloaded CSV for {symbol}: {csv_path}")
    try:
        raw = pd.read_csv(csv_path)
    except Exception as exc:  # noqa: BLE001
        raise DailySnapshotError(f"failed to read downloaded CSV for {symbol}: {exc}") from exc
    if raw.empty or len(raw.columns) < 2:
        raise DailySnapshotError(f"downloaded CSV for {symbol} has unexpected format")
    date_col = raw.columns[0]
    value_col = symbol if symbol in raw.columns else raw.columns[1]
    idx = pd.DatetimeIndex(pd.to_datetime(raw[date_col], errors="coerce"))
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    valid = ~idx.isna()
    series = pd.Series(
        pd.to_numeric(raw.loc[valid, value_col], errors="coerce").values,
        index=idx[valid],
        name=symbol,
    )
    series = series.sort_index()
    series = series[~series.index.duplicated(keep="last")]
    return series


def _write_frame_as_symbol_csvs(df: pd.DataFrame, data_folder: Path) -> None:
    data_folder.mkdir(parents=True, exist_ok=True)
    for symbol in df.columns:
        series = pd.to_numeric(df[symbol], errors="coerce")
        symbol_df = series.to_frame(name=symbol)
        symbol_df.index.name = "Date"
        symbol_df.to_csv(data_folder / f"{symbol}.csv")


def _apply_snapshot_filters(
    df: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
    tmp_dir: Path,
    data_cfg: dict,
) -> pd.DataFrame:
    staging_dir = tmp_dir / "assembled"
    _write_frame_as_symbol_csvs(df, staging_dir)
    merged = load_and_preprocess_data_fixed(
        data_folder=str(staging_dir),
        dropna_all=bool(data_cfg.get("dropna_all", True)),
        min_history_days=int(data_cfg.get("min_history_days", 365)),
        start_date=start_date,
        end_date=end_date,
        include_delisted=bool(data_cfg.get("include_delisted", False)),
        allow_internal_gaps=bool(data_cfg.get("allow_internal_gaps", False)),
        tail_grace_days=int(data_cfg.get("tail_grace_days", 3)),
    )
    if merged.empty:
        raise DailySnapshotError("merged snapshot dataframe is empty")
    return merged


def _build_symbol_stats(base_df: pd.DataFrame | None, symbols: Sequence[str]) -> dict[str, int]:
    top_symbols = _normalize_symbols(symbols)
    base_columns = [] if base_df is None else _normalize_symbols([str(col) for col in base_df.columns])
    base_set = set(base_columns)
    top_set = set(top_symbols)
    return {
        "before": len(base_columns),
        "top": len(top_symbols),
        "added": len([symbol for symbol in top_symbols if symbol not in base_set]),
        "removed": len([symbol for symbol in base_columns if symbol not in top_set]),
        "updated_incremental": len([symbol for symbol in top_symbols if symbol in base_set]),
    }


def _load_latest_snapshot_dataframe(
    root: Path,
    *,
    n_top_coins: int,
    base_uri: str,
    target_date: date,
) -> pd.DataFrame | None:
    latest_meta = _latest_local_snapshot(
        root,
        n_top_coins,
        base_uri=base_uri,
        before=target_date + timedelta(days=1),
    )
    if latest_meta is None:
        return None
    try:
        raw = load_snapshot_dataframe(latest_meta)
        return _normalize_snapshot_dataframe(raw)
    except Exception as exc:  # noqa: BLE001
        raise DailySnapshotError(f"failed to load base snapshot {latest_meta.local_path}: {exc}") from exc


def _build_incremental_snapshot(
    *,
    symbols: Sequence[str],
    start_date: str,
    end_date: str,
    tmp_dir: Path,
    data_cfg: dict,
    base_df: pd.DataFrame | None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    if base_df is None or base_df.empty:
        raise DailySnapshotError("incremental refresh requires a non-empty base snapshot")
    top_symbols = _normalize_symbols(symbols)
    stats = _build_symbol_stats(base_df, top_symbols)
    if not top_symbols:
        raise DailySnapshotError("empty symbol list for incremental refresh")

    start_bound = date.fromisoformat(start_date)
    end_bound = date.fromisoformat(end_date)
    base_column_map = {
        normalized: str(column)
        for column in base_df.columns
        if (normalized := _normalize_symbol(column))
    }
    base_set = set(base_column_map)
    existing_symbols = [symbol for symbol in top_symbols if symbol in base_set]
    added_symbols = [symbol for symbol in top_symbols if symbol not in base_set]
    if not existing_symbols and not added_symbols:
        raise DailySnapshotError("incremental refresh has no target symbols")

    last_date = base_df.index.max().date()
    incremental_start_date = max(start_bound, last_date - timedelta(days=DEFAULT_INCREMENTAL_OVERLAP_DAYS))
    incremental_start = incremental_start_date.isoformat()
    if incremental_start_date > end_bound:
        incremental_start_date = end_bound
        incremental_start = end_date

    download_dir = tmp_dir / "incremental_download"
    download_dir.mkdir(parents=True, exist_ok=True)
    if existing_symbols:
        download_multiple_cryptos(
            existing_symbols,
            incremental_start,
            end_date,
            data_folder=str(download_dir),
        )
    if added_symbols:
        download_multiple_cryptos(
            added_symbols,
            start_date,
            end_date,
            data_folder=str(download_dir),
        )

    merged_by_symbol: dict[str, pd.Series] = {}
    overlap_ts = pd.Timestamp(incremental_start_date)
    for symbol in existing_symbols:
        base_series = pd.to_numeric(base_df[base_column_map[symbol]], errors="coerce")
        csv_path = download_dir / f"{symbol}.csv"
        if not csv_path.exists():
            logger.warning(
                "Incremental tail missing for %s, preserving base history",
                symbol,
            )
            merged_by_symbol[symbol] = base_series
            continue
        try:
            tail_series = _read_downloaded_symbol_series(csv_path, symbol)
        except DailySnapshotError as exc:
            logger.warning(
                "Incremental tail unavailable for %s (%s), preserving base history",
                symbol,
                exc,
            )
            merged_by_symbol[symbol] = base_series
            continue
        head_series = base_series
        head_series = head_series[head_series.index < overlap_ts]
        combined = pd.concat([head_series, tail_series]).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
        merged_by_symbol[symbol] = combined
    for symbol in added_symbols:
        csv_path = download_dir / f"{symbol}.csv"
        if not csv_path.exists():
            logger.warning(
                "Incremental history missing for new ticker %s, skipping ticker",
                symbol,
            )
            continue
        try:
            merged_by_symbol[symbol] = _read_downloaded_symbol_series(csv_path, symbol)
        except DailySnapshotError as exc:
            logger.warning(
                "Incremental history unavailable for new ticker %s (%s), skipping ticker",
                symbol,
                exc,
            )
            continue

    if not merged_by_symbol:
        raise DailySnapshotError("incremental refresh produced no symbols after download")

    merged = pd.concat(merged_by_symbol, axis=1)
    merged = _normalize_snapshot_dataframe(merged)
    merged = merged.loc[pd.Timestamp(start_date):pd.Timestamp(end_date)]
    filtered = _apply_snapshot_filters(
        merged,
        start_date=start_date,
        end_date=end_date,
        tmp_dir=tmp_dir,
        data_cfg=data_cfg,
    )
    ordered_columns = [symbol for symbol in top_symbols if symbol in filtered.columns]
    if ordered_columns:
        filtered = filtered[ordered_columns]
    return filtered, stats


def _download_and_merge(
    symbols: Iterable[str],
    *,
    start_date: str,
    end_date: str,
    tmp_dir: Path,
    data_cfg: dict,
) -> pd.DataFrame:
    symbols_list = _normalize_symbols(symbols)
    if not symbols_list:
        raise DailySnapshotError("empty symbol list for snapshot download")

    download_multiple_cryptos(
        symbols_list,
        start_date,
        end_date,
        data_folder=str(tmp_dir),
    )
    merged = load_and_preprocess_data_fixed(
        data_folder=str(tmp_dir),
        dropna_all=bool(data_cfg.get("dropna_all", True)),
        min_history_days=int(data_cfg.get("min_history_days", 365)),
        start_date=start_date,
        end_date=end_date,
        include_delisted=bool(data_cfg.get("include_delisted", False)),
        allow_internal_gaps=bool(data_cfg.get("allow_internal_gaps", False)),
        tail_grace_days=int(data_cfg.get("tail_grace_days", 3)),
    )
    if merged.empty:
        raise DailySnapshotError("merged snapshot dataframe is empty")
    return merged


def _latest_local_snapshot(
    root: Path,
    n_top_coins: int,
    *,
    base_uri: str,
    before: date | None = None,
) -> DailySnapshotMeta | None:
    if not root.exists():
        return None
    candidates: list[DailySnapshotMeta] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            child_date = date.fromisoformat(child.name)
        except ValueError:
            continue
        if before and child_date >= before:
            continue
        parquet_path = child / f"n{n_top_coins}.parquet"
        csv_path = child / f"n{n_top_coins}.csv"
        target_path = parquet_path if parquet_path.exists() else csv_path
        if not target_path.exists():
            continue
        candidates.append(
            DailySnapshotMeta(
                snapshot_date=child_date,
                source_date=child_date,
                n_top_coins=n_top_coins,
                local_path=str(target_path),
                storage_uri=_build_storage_uri(base_uri, child_date, n_top_coins),
                stale=False,
                created_at=datetime.fromtimestamp(target_path.stat().st_mtime, tz=timezone.utc),
                uploaded=False,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda meta: meta.source_date)
    return candidates[-1]


def _copy_latest_marker(root: Path, meta: DailySnapshotMeta) -> None:
    marker_path = _latest_marker_path(root, meta.n_top_coins)
    try:
        shutil.copy2(meta.local_path, marker_path)
    except Exception:  # noqa: BLE001
        logger.debug("Could not refresh latest marker at %s", marker_path)


def _prune_old_daily_snapshot_dirs(
    root: Path,
    *,
    target_date: date,
    retention_days: int = DEFAULT_DAILY_RETENTION_DAYS,
) -> None:
    keep_window = max(1, retention_days)
    cutoff = target_date - timedelta(days=keep_window - 1)
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            child_date = date.fromisoformat(child.name)
        except ValueError:
            continue
        if child_date >= cutoff:
            continue
        try:
            shutil.rmtree(child, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to prune old daily snapshot directory %s: %s", child, exc)


def refresh_daily_snapshot(
    settings: ServiceSettings,
    *,
    n_top_coins: int = DEFAULT_N_TOP,
    base_uri: str | None = None,
    retry_delays: Sequence[int] = DEFAULT_RETRY_DELAYS,
    snapshot_root: str | None = None,
    now: datetime | None = None,
) -> DailySnapshotMeta:
    """
    Build and persist the daily snapshot for n_top_coins=100.

    - Target time: run once per calendar day (UTC) at ~06:00.
    - Retries: two attempts after 10 and 20 minutes by default.
    - On repeated failure, falls back to the latest available snapshot and marks it stale.
    """
    target_date = (now or datetime.now(timezone.utc)).date()
    root = _snapshot_root(settings, snapshot_root)
    root.mkdir(parents=True, exist_ok=True)
    config = _load_pipeline_config(settings.config_path)
    data_cfg = config.get("data", {})
    base_uri = resolve_base_uri(settings, base_uri)

    existing = _load_state(root)
    if existing and existing.n_top_coins == n_top_coins:
        existing_path = Path(existing.local_path)
        if existing_path.exists():
            existing.stale = existing.source_date < target_date
            if not existing.storage_uri:
                existing.storage_uri = _build_storage_uri(base_uri, existing.source_date, n_top_coins)
                _persist_state(root, existing)
            if existing.snapshot_date == target_date and not existing.stale:
                return existing

    start_date = _resolve_start_date(config, target_date)
    end_date = target_date.isoformat()
    scheduled_full_rebuild = n_top_coins == DEFAULT_N_TOP and _should_run_scheduled_full_rebuild(
        target_date
    )

    delays = [0, *(int(d) for d in retry_delays)]
    last_error: str | None = None

    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            symbols = _normalize_symbols(get_top_n_cryptos_cmc(n=n_top_coins))
            tmp_dir = Path(tempfile.mkdtemp(prefix="daily_snapshot_", dir=root))
            try:
                base_df: pd.DataFrame | None = None
                refresh_stats: dict[str, int] | None = None
                if n_top_coins == DEFAULT_N_TOP:
                    try:
                        base_df = _load_latest_snapshot_dataframe(
                            root,
                            n_top_coins=n_top_coins,
                            base_uri=base_uri,
                            target_date=target_date,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Unable to read base snapshot for incremental refresh: %s", exc)
                        base_df = None
                    refresh_stats = _build_symbol_stats(base_df, symbols)

                refresh_mode = "full_rebuild"
                if n_top_coins == DEFAULT_N_TOP and not scheduled_full_rebuild:
                    try:
                        merged, refresh_stats = _build_incremental_snapshot(
                            symbols=symbols,
                            start_date=start_date,
                            end_date=end_date,
                            tmp_dir=tmp_dir,
                            data_cfg=data_cfg,
                            base_df=base_df,
                        )
                        refresh_mode = "incremental"
                    except Exception as incremental_exc:  # noqa: BLE001
                        logger.warning(
                            "Incremental daily snapshot failed, switching to full rebuild: %s",
                            incremental_exc,
                        )
                        merged = _download_and_merge(
                            symbols,
                            start_date=start_date,
                            end_date=end_date,
                            tmp_dir=tmp_dir,
                            data_cfg=data_cfg,
                        )
                        refresh_mode = "fallback_full"
                else:
                    merged = _download_and_merge(
                        symbols,
                        start_date=start_date,
                        end_date=end_date,
                        tmp_dir=tmp_dir,
                        data_cfg=data_cfg,
                    )

                final_stats = refresh_stats or _build_symbol_stats(None, symbols)
                final_stats["after"] = int(len(merged.columns))
                logger.info(
                    "Daily snapshot refresh mode=%s n_top_coins=%s tickers_before=%s "
                    "tickers_after=%s added=%s removed=%s updated_incremental=%s",
                    refresh_mode,
                    n_top_coins,
                    final_stats.get("before", 0),
                    final_stats.get("after", 0),
                    final_stats.get("added", 0),
                    final_stats.get("removed", 0),
                    final_stats.get("updated_incremental", 0) if refresh_mode == "incremental" else 0,
                )

                target_dir = root / target_date.isoformat()
                target_dir.mkdir(parents=True, exist_ok=True)
                local_path = target_dir / f"n{n_top_coins}.parquet"
                _write_parquet(merged, local_path)
                storage_uri = _build_storage_uri(base_uri, target_date, n_top_coins)
                uploaded = _upload_if_s3(local_path, storage_uri)
                meta = DailySnapshotMeta(
                    snapshot_date=target_date,
                    source_date=target_date,
                    n_top_coins=n_top_coins,
                    local_path=str(local_path),
                    storage_uri=storage_uri,
                    stale=False,
                    created_at=datetime.now(timezone.utc),
                    uploaded=uploaded,
                    error=None,
                )
                _persist_state(root, meta)
                _copy_latest_marker(root, meta)
                if n_top_coins == DEFAULT_N_TOP:
                    _prune_old_daily_snapshot_dirs(root, target_date=target_date)
                return meta
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning("Daily snapshot attempt failed (delay=%ss): %s", delay, exc)
            continue

    fallback = _latest_local_snapshot(
        root,
        n_top_coins,
        base_uri=base_uri,
        before=target_date + timedelta(days=1),
    )
    if fallback:
        fallback.snapshot_date = target_date
        fallback.stale = True
        fallback.error = last_error
        _persist_state(root, fallback)
        _copy_latest_marker(root, fallback)
        return fallback

    raise DailySnapshotError(f"failed to build daily snapshot for {target_date}: {last_error}")


def load_latest_snapshot_meta(
    snapshot_root: Path,
    *,
    target_date: date | None = None,
    n_top_coins: int = DEFAULT_N_TOP,
    base_uri: str | None = None,
) -> DailySnapshotMeta | None:
    """Return the freshest available snapshot metadata (today if possible)."""
    target = target_date or datetime.now(timezone.utc).date()
    base_uri = base_uri or DEFAULT_BASE_URI
    meta = _load_state(snapshot_root)
    if meta and meta.n_top_coins == n_top_coins:
        path = Path(meta.local_path)
        if path.exists():
            meta.stale = meta.source_date < target
            if not meta.storage_uri:
                meta.storage_uri = _build_storage_uri(base_uri, meta.source_date, n_top_coins)
            return meta

    fallback = _latest_local_snapshot(
        snapshot_root,
        n_top_coins,
        base_uri=base_uri,
        before=target + timedelta(days=1),
    )
    if fallback:
        if fallback.source_date < target:
            fallback.snapshot_date = target
            fallback.stale = True
        else:
            fallback.stale = False
        return fallback
    return None


def select_snapshot_dataframe(
    *,
    config_path: Path,
    runs_root: Path,
    n_top_coins: int,
    snapshot_root: str | None = None,
    base_uri: str | None = None,
    now: datetime | None = None,
) -> SnapshotSelection:
    root = resolve_snapshot_root_from_runs_root(runs_root, snapshot_root)
    base_uri = resolve_base_uri_from_config(config_path, base_uri)
    now = now or datetime.now(timezone.utc)

    if n_top_coins == DEFAULT_N_TOP:
        meta = load_latest_snapshot_meta(root, n_top_coins=n_top_coins, base_uri=base_uri)
        if meta is None:
            raise DailySnapshotError("daily snapshot not found for n_top_coins=100")
        df = load_snapshot_dataframe(meta)
        source = "daily_default" if not meta.stale else "daily_stale"
        logger.info("Snapshot source resolved: %s (n_top_coins=%s)", source, n_top_coins)
        return SnapshotSelection(source=source, meta=meta, dataframe=df)

    prune_custom_snapshots(
        root,
        base_uri=base_uri,
        ttl_days=CUSTOM_CACHE_TTL_DAYS,
        limit=CUSTOM_CACHE_LIMIT,
        now=now,
        keep_n={n_top_coins},
    )
    meta = load_custom_snapshot_meta(
        root,
        n_top_coins=n_top_coins,
        base_uri=base_uri,
        ttl_days=CUSTOM_CACHE_TTL_DAYS,
        now=now,
    )
    if meta is not None:
        df = load_snapshot_dataframe(meta)
        logger.info("Snapshot source resolved: custom_cache (n_top_coins=%s)", n_top_coins)
        return SnapshotSelection(source="custom_cache", meta=meta, dataframe=df)

    logger.info("Custom snapshot cache miss; downloading fresh data (n_top_coins=%s)", n_top_coins)
    meta = refresh_custom_snapshot(
        config_path=config_path,
        snapshot_root=root,
        n_top_coins=n_top_coins,
        base_uri=base_uri,
        now=now,
    )
    df = load_snapshot_dataframe(meta)
    logger.info("Snapshot source resolved: custom_fresh (n_top_coins=%s)", n_top_coins)
    return SnapshotSelection(source="custom_fresh", meta=meta, dataframe=df)


def load_snapshot_dataframe(meta: DailySnapshotMeta | CustomSnapshotMeta) -> pd.DataFrame:
    path = Path(meta.local_path)
    if not path.exists():
        raise DailySnapshotError(f"snapshot file missing at {path}")
    suffix = path.suffix.lower()
    try:
        if suffix == ".parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path, index_col=0, parse_dates=True)
    except (ImportError, ModuleNotFoundError) as exc:
        raise DailySnapshotError("pyarrow or fastparquet required to read snapshot parquet") from exc
    except Exception as exc:  # noqa: BLE001
        raise DailySnapshotError(f"failed to load snapshot data: {exc}") from exc
