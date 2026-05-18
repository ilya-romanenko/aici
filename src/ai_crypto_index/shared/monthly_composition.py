from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from math import isfinite
from pathlib import Path
import re

from ai_crypto_index.shared import run_store
from ai_crypto_index.shared.performance_series_store import (
    resolve_performance_series_read_path,
)
from ai_crypto_index.shared.settings import ServiceSettings

MONTHLY_COMPOSITION_DIR = "_index_composition"
MONTHLY_COMPOSITION_FILENAME = "monthly_snapshots.json"
DEFAULT_LIVE_RUN_PREFIX = "auto-classic"
BACKTEST_RUNS_ROOT_DIR = "_performance"
BACKTEST_RUNS_SUBDIR = "runs"
BACKTEST_WEIGHTS_CANDIDATES = ("checkpoint_weights.csv", "monthly_weights.csv")
RUN_PREFIX_TO_BACKTEST_DIR = {
    "auto-classic": "classic",
    "auto-conservative": "conservative",
    "auto-aggressive": "risky",
    "auto-risky": "risky",
}
BACKTEST_MONTHLY_SERIES_FILENAMES = {
    "classic": "AICI_classic.csv",
    "conservative": "AICI_conservative.csv",
    "risky": "AICI_risky.csv",
}


@dataclass(frozen=True)
class MonthlyCompositionSnapshot:
    month: str
    asset: str
    weight: float
    source: str
    run_id: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "MonthlyCompositionSnapshot":
        return cls(
            month=str(payload.get("month") or ""),
            asset=str(payload.get("asset") or ""),
            weight=float(payload.get("weight") or 0.0),
            source=str(payload.get("source") or ""),
            run_id=str(payload.get("run_id") or ""),
        )


@dataclass(frozen=True)
class MonthlyCompositionStore:
    updated_at: str
    current_month: str | None
    snapshots: list[MonthlyCompositionSnapshot]
    live_snapshots: list[MonthlyCompositionSnapshot]
    backtest_snapshots: list[MonthlyCompositionSnapshot]

    def to_dict(self) -> dict[str, object]:
        return {
            "updated_at": self.updated_at,
            "current_month": self.current_month,
            "snapshots": [item.to_dict() for item in self.snapshots],
            "live_snapshots": [item.to_dict() for item in self.live_snapshots],
            "backtest_snapshots": [item.to_dict() for item in self.backtest_snapshots],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "MonthlyCompositionStore":
        snapshots = [
            MonthlyCompositionSnapshot.from_dict(item)
            for item in payload.get("snapshots", [])
            if isinstance(item, dict)
        ]
        live_snapshots = [
            MonthlyCompositionSnapshot.from_dict(item)
            for item in payload.get("live_snapshots", [])
            if isinstance(item, dict)
        ]
        backtest_snapshots = [
            MonthlyCompositionSnapshot.from_dict(item)
            for item in payload.get("backtest_snapshots", [])
            if isinstance(item, dict)
        ]
        return cls(
            updated_at=str(payload.get("updated_at") or ""),
            current_month=(str(payload.get("current_month")) if payload.get("current_month") else None),
            snapshots=snapshots,
            live_snapshots=live_snapshots,
            backtest_snapshots=backtest_snapshots,
        )


def _store_path(settings: ServiceSettings) -> Path:
    root = settings.runs_root / MONTHLY_COMPOSITION_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root / MONTHLY_COMPOSITION_FILENAME


def _source_from_meta(run_dir: Path, *, run_prefix: str | None) -> str:
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                source = raw.get("source")
                if isinstance(source, str) and source.strip():
                    return source.strip()
        except Exception:
            pass
    if run_prefix and run_dir.name.startswith(run_prefix):
        return "auto"
    return "run"


def _resolve_month_utc(run_dir: Path) -> str:
    month_from_equity = _resolve_month_from_equity_curve(run_dir)
    if month_from_equity:
        return month_from_equity

    name_match = re.search(r"(\d{4}-\d{2}-\d{2})T\d{2}-\d{2}-\d{2}Z", run_dir.name)
    if name_match:
        try:
            parsed = date.fromisoformat(name_match.group(1))
            return f"{parsed.year:04d}-{parsed.month:02d}"
        except ValueError:
            pass

    ts = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
    return f"{ts.year:04d}-{ts.month:02d}"


def _resolve_run_timestamp(run_dir: Path) -> float:
    match = re.search(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})Z",
        run_dir.name,
    )
    if match:
        try:
            parsed = datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6)),
                tzinfo=timezone.utc,
            )
            return parsed.timestamp()
        except ValueError:
            pass
    return run_dir.stat().st_mtime


def _select_first_run_per_month(run_dirs: list[Path]) -> list[tuple[str, Path]]:
    selected: dict[str, Path] = {}
    ordered = sorted(run_dirs, key=_resolve_run_timestamp)
    for run_dir in ordered:
        month_key = _resolve_month_utc(run_dir)
        if month_key not in selected:
            selected[month_key] = run_dir
    return sorted(selected.items(), key=lambda item: item[0])


def _build_monthly_snapshots(
    settings: ServiceSettings,
    *,
    run_prefix: str | None,
) -> list[MonthlyCompositionSnapshot]:
    runs = list(run_store.iter_completed_runs(settings, prefix=run_prefix))
    if not runs:
        return []

    month_runs = _select_first_run_per_month(runs)
    snapshots: list[MonthlyCompositionSnapshot] = []
    for month_key, run_dir in month_runs:
        try:
            rows = run_store.load_weights(run_dir)
        except Exception:
            continue
        sorted_rows = sorted(rows, key=lambda row: float(row.get("weight", 0.0)), reverse=True)
        source = _source_from_meta(run_dir, run_prefix=run_prefix)
        for row in sorted_rows:
            asset = str(row.get("asset", "")).strip()
            if not asset:
                continue
            snapshots.append(
                MonthlyCompositionSnapshot(
                    month=month_key,
                    asset=asset,
                    weight=float(row.get("weight", 0.0)),
                    source=source,
                    run_id=run_dir.name,
                )
            )
    return snapshots


def _month_from_text(raw_value: object) -> str | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d{4})-(\d{2})", text)
    if not match:
        return None
    month = int(match.group(2))
    if month < 1 or month > 12:
        return None
    year = int(match.group(1))
    return f"{year:04d}-{month:02d}"


def _resolve_month_from_equity_curve(run_dir: Path) -> str | None:
    equity_path = run_dir / run_store.EQUITY_CURVE_ARTIFACT
    if not equity_path.exists() or equity_path.stat().st_size <= 0:
        return None

    try:
        with equity_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames:
                return None
            date_field = "date" if "date" in fieldnames else fieldnames[0]
            resolved_month: str | None = None
            for row in reader:
                month = _month_from_text(row.get(date_field))
                if month:
                    resolved_month = month
            return resolved_month
    except Exception:
        return None


def _resolve_backtest_strategy_dir(run_prefix: str) -> str:
    normalized = (run_prefix or "").strip().lower()
    if normalized in RUN_PREFIX_TO_BACKTEST_DIR:
        return RUN_PREFIX_TO_BACKTEST_DIR[normalized]
    if "conservative" in normalized:
        return "conservative"
    if "risky" in normalized or "aggressive" in normalized:
        return "risky"
    return "classic"


def _build_backtest_snapshots_from_csv(
    csv_path: Path,
    *,
    strategy_dir: str,
) -> list[MonthlyCompositionSnapshot]:
    if not csv_path.exists() or csv_path.stat().st_size <= 0:
        return []

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if len(fieldnames) < 2:
            return []
        date_field = fieldnames[0]
        asset_fields = [field for field in fieldnames[1:] if str(field).strip()]
        if not asset_fields:
            return []

        month_to_weights: dict[str, list[tuple[str, float]]] = {}
        for row in reader:
            month_key = _month_from_text(row.get(date_field))
            if not month_key or month_key in month_to_weights:
                continue
            weights_for_month: list[tuple[str, float]] = []
            for asset in asset_fields:
                asset_name = str(asset).strip()
                if not asset_name:
                    continue
                raw_weight = row.get(asset)
                try:
                    weight = float(raw_weight) if raw_weight not in (None, "") else float("nan")
                except (TypeError, ValueError):
                    continue
                if weight <= 0 or not isfinite(weight):
                    continue
                weights_for_month.append((asset_name, weight))
            if not weights_for_month:
                continue
            month_to_weights[month_key] = sorted(
                weights_for_month,
                key=lambda item: item[1],
                reverse=True,
            )

    snapshots: list[MonthlyCompositionSnapshot] = []
    for month_key in sorted(month_to_weights):
        run_id = f"backtest-{strategy_dir}-{month_key}"
        for asset, weight in month_to_weights[month_key]:
            snapshots.append(
                MonthlyCompositionSnapshot(
                    month=month_key,
                    asset=asset,
                    weight=weight,
                    source="backtest",
                    run_id=run_id,
                )
            )
    return snapshots


def _resolve_results_backup_dir(settings: ServiceSettings) -> Path:
    return settings.runs_root.parent / "Results_Backup"


def _infer_backup_strategy_tag(path: Path) -> str:
    label = path.parent.name.strip().lower()
    if "conservative" in label:
        return "conservative"
    if "risky" in label or "aggressive" in label:
        return "risky"
    if "classic" in label:
        return "classic"
    return "generic"


def _backup_priority_for_strategy(*, strategy_dir: str, strategy_tag: str) -> int:
    if strategy_dir == "classic":
        if strategy_tag in {"classic", "generic"}:
            return 2
        return 0
    if strategy_dir == "conservative":
        if strategy_tag == "conservative":
            return 2
        if strategy_tag == "generic":
            return 1
        return 0
    if strategy_dir == "risky":
        if strategy_tag == "risky":
            return 2
        if strategy_tag == "generic":
            return 1
        return 0
    return 0


def _iter_backtest_weight_candidates(
    settings: ServiceSettings,
    *,
    strategy_dir: str,
) -> list[tuple[Path, int]]:
    base_dir = settings.runs_root / BACKTEST_RUNS_ROOT_DIR / BACKTEST_RUNS_SUBDIR / strategy_dir
    candidates: list[tuple[Path, int]] = []

    for index, filename in enumerate(BACKTEST_WEIGHTS_CANDIDATES):
        # Keep the freshest _performance artifacts at the highest priority.
        candidates.append((base_dir / filename, 1000 - index))

    backup_root = _resolve_results_backup_dir(settings)
    if backup_root.exists():
        for path in backup_root.glob("*/checkpoint_weights.csv"):
            strategy_tag = _infer_backup_strategy_tag(path)
            priority = _backup_priority_for_strategy(
                strategy_dir=strategy_dir,
                strategy_tag=strategy_tag,
            )
            if priority <= 0:
                continue
            candidates.append((path, 500 + priority))

    return candidates


def _resolve_backtest_series_start_month(
    settings: ServiceSettings,
    strategy_dir: str,
) -> str | None:
    filename = BACKTEST_MONTHLY_SERIES_FILENAMES.get(strategy_dir)
    if not filename:
        return None

    target_path = resolve_performance_series_read_path(
        filename=filename,
        runs_root=settings.runs_root,
    )
    if target_path is None:
        return None

    with target_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            return None
        date_field = "date" if "date" in fieldnames else fieldnames[0]
        for row in reader:
            month_key = _month_from_text(row.get(date_field))
            if month_key:
                return month_key
    return None


def _group_snapshots_by_month(
    snapshots: list[MonthlyCompositionSnapshot],
) -> dict[str, list[MonthlyCompositionSnapshot]]:
    grouped: dict[str, list[MonthlyCompositionSnapshot]] = {}
    for item in snapshots:
        grouped.setdefault(item.month, []).append(item)
    for month_key, rows in grouped.items():
        grouped[month_key] = sorted(rows, key=lambda item: (-item.weight, item.asset))
    return grouped


def _build_backtest_monthly_snapshots(
    settings: ServiceSettings,
    *,
    run_prefix: str,
) -> list[MonthlyCompositionSnapshot]:
    strategy_dir = _resolve_backtest_strategy_dir(run_prefix)
    parsed_candidates: list[
        tuple[int, int, str, str, dict[str, list[MonthlyCompositionSnapshot]]]
    ] = []

    for candidate_path, priority in _iter_backtest_weight_candidates(
        settings,
        strategy_dir=strategy_dir,
    ):
        candidate_snapshots = _build_backtest_snapshots_from_csv(
            candidate_path,
            strategy_dir=strategy_dir,
        )
        if not candidate_snapshots:
            continue
        grouped = _group_snapshots_by_month(candidate_snapshots)
        month_keys = sorted(grouped)
        latest_month = month_keys[-1] if month_keys else ""
        parsed_candidates.append(
            (
                priority,
                len(month_keys),
                latest_month,
                str(candidate_path),
                grouped,
            )
        )

    parsed_candidates.sort(
        key=lambda item: (item[0], item[1], item[2], item[3]),
        reverse=True,
    )

    merged_by_month: dict[str, list[MonthlyCompositionSnapshot]] = {}
    for _, _, _, _, grouped in parsed_candidates:
        for month_key in sorted(grouped):
            if month_key in merged_by_month:
                continue
            merged_by_month[month_key] = grouped[month_key]

    backtest_start_month = _resolve_backtest_series_start_month(settings, strategy_dir)
    if backtest_start_month:
        merged_by_month = {
            month_key: rows
            for month_key, rows in merged_by_month.items()
            if month_key >= backtest_start_month
        }

    merged: list[MonthlyCompositionSnapshot] = []
    for month_key in sorted(merged_by_month):
        merged.extend(merged_by_month[month_key])
    return merged


def _split_by_live_start(
    snapshots: list[MonthlyCompositionSnapshot],
    *,
    live_start_date: str | None,
) -> tuple[list[MonthlyCompositionSnapshot], list[MonthlyCompositionSnapshot]]:
    if not live_start_date:
        return [], list(snapshots)
    try:
        parsed = date.fromisoformat(live_start_date)
        live_start_month = f"{parsed.year:04d}-{parsed.month:02d}"
    except ValueError:
        return [], list(snapshots)

    live: list[MonthlyCompositionSnapshot] = []
    backtest: list[MonthlyCompositionSnapshot] = []
    for item in snapshots:
        if item.month >= live_start_month:
            live.append(item)
        else:
            backtest.append(item)
    return live, backtest


def refresh_monthly_snapshots_store(
    settings: ServiceSettings,
    *,
    live_start_date: str | None,
    run_prefix: str = DEFAULT_LIVE_RUN_PREFIX,
    persist: bool = True,
) -> MonthlyCompositionStore:
    live_run_snapshots = _build_monthly_snapshots(settings, run_prefix=run_prefix)
    live_months = {item.month for item in live_run_snapshots}

    backtest_snapshots_seed = _build_backtest_monthly_snapshots(
        settings,
        run_prefix=run_prefix,
    )
    backtest_snapshots_seed = [
        item for item in backtest_snapshots_seed if item.month not in live_months
    ]

    snapshots = sorted(
        [*backtest_snapshots_seed, *live_run_snapshots],
        key=lambda item: (item.month, -item.weight, item.asset),
    )

    live_snapshots, backtest_snapshots = _split_by_live_start(
        snapshots,
        live_start_date=live_start_date,
    )
    current_month = max((item.month for item in snapshots), default=None)
    store = MonthlyCompositionStore(
        updated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        current_month=current_month,
        snapshots=snapshots,
        live_snapshots=live_snapshots,
        backtest_snapshots=backtest_snapshots,
    )
    if persist:
        _store_path(settings).write_text(
            json.dumps(store.to_dict(), indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
    return store


def load_monthly_snapshots_store(settings: ServiceSettings) -> MonthlyCompositionStore:
    path = _store_path(settings)
    if not path.exists():
        return MonthlyCompositionStore(
            updated_at="",
            current_month=None,
            snapshots=[],
            live_snapshots=[],
            backtest_snapshots=[],
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return MonthlyCompositionStore(
            updated_at="",
            current_month=None,
            snapshots=[],
            live_snapshots=[],
            backtest_snapshots=[],
        )
    if not isinstance(raw, dict):
        return MonthlyCompositionStore(
            updated_at="",
            current_month=None,
            snapshots=[],
            live_snapshots=[],
            backtest_snapshots=[],
        )
    return MonthlyCompositionStore.from_dict(raw)


__all__ = [
    "DEFAULT_LIVE_RUN_PREFIX",
    "MONTHLY_COMPOSITION_DIR",
    "MONTHLY_COMPOSITION_FILENAME",
    "MonthlyCompositionSnapshot",
    "MonthlyCompositionStore",
    "load_monthly_snapshots_store",
    "refresh_monthly_snapshots_store",
]
