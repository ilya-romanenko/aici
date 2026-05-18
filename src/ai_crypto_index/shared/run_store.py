from __future__ import annotations

import csv
import io
import json
import secrets
import zipfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from ai_crypto_index.shared.settings import ServiceSettings

CSV_ARTIFACT = "weights.csv"
PERF_ARTIFACT = "perf.json"
EQUITY_CURVE_ARTIFACT = "equity_curve.csv"
EQUITY_CURVE_PLOT_ARTIFACT = "equity_curve_plot.png"


def make_run_id() -> str:
    """Generate a collision-resistant run identifier."""

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    suffix = secrets.token_hex(2)
    return f"{timestamp}-{suffix}"


def resolve_run_dir(settings: ServiceSettings, run_id: str) -> Path:
    run_dir = settings.runs_root / run_id
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"Run '{run_id}' not found under {settings.runs_root}")
    return run_dir


def iter_completed_runs(settings: ServiceSettings, *, prefix: str | None = None) -> Iterable[Path]:
    for child in sorted(settings.runs_root.iterdir()):
        if not child.is_dir():
            continue
        if prefix and not child.name.startswith(prefix):
            continue
        csv_path = child / CSV_ARTIFACT
        if csv_path.exists() and csv_path.stat().st_size > 0:
            yield child


def find_latest_run(
    settings: ServiceSettings,
    *,
    before_timestamp: float | None = None,
    prefix: str | None = None,
) -> Path | None:
    runs = [
        path
        for path in iter_completed_runs(settings, prefix=prefix)
        if before_timestamp is None or path.stat().st_mtime <= before_timestamp
    ]
    if not runs:
        return None
    return max(runs, key=lambda path: path.stat().st_mtime)


def load_weights(run_dir: Path) -> list[dict[str, float]]:
    csv_path = run_dir / CSV_ARTIFACT
    if not csv_path.exists():
        raise FileNotFoundError(f"weights.csv not found for run '{run_dir.name}'")

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, float]] = []
        for row in reader:
            asset = row.get("asset")
            weight_raw = row.get("weight", "0")
            if not asset:
                continue
            try:
                weight = float(weight_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid weight value '{weight_raw}' in {csv_path}") from exc
            rows.append({"asset": asset, "weight": weight})
    return rows


def load_perf(run_dir: Path) -> dict[str, float]:
    perf_path = run_dir / PERF_ARTIFACT
    if not perf_path.exists():
        raise FileNotFoundError(f"perf.json not found for run '{run_dir.name}'")

    with perf_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected perf payload in {perf_path}")
    return {str(k): float(v) for k, v in data.items()}


def _load_equity_curve_dataframe(run_dir: Path) -> pd.DataFrame:
    csv_path = run_dir / EQUITY_CURVE_ARTIFACT
    if not csv_path.exists():
        raise FileNotFoundError(f"equity_curve.csv not found for run '{run_dir.name}'")

    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError(f"equity_curve.csv is empty for run '{run_dir.name}'")
    if "equity_curve" not in df.columns:
        raise ValueError(f"equity_curve.csv missing 'equity_curve' column for run '{run_dir.name}'")

    df["equity_curve"] = pd.to_numeric(df["equity_curve"], errors="coerce")
    df = df.dropna(subset=["equity_curve"])
    if df.empty:
        raise ValueError(f"equity_curve.csv has no numeric equity values for run '{run_dir.name}'")

    if "date" in df.columns:
        display_dates = df["date"].astype(str)
        parsed_dates = pd.to_datetime(df["date"], errors="coerce")
        if parsed_dates.notna().any():
            parsed_dates = parsed_dates.ffill().bfill()
            df["_plot_x"] = parsed_dates
        else:
            df["_plot_x"] = pd.RangeIndex(len(df))
        df["_display_date"] = display_dates
    else:
        df["_plot_x"] = pd.RangeIndex(len(df))
        df["_display_date"] = df.index.astype(str)

    df = df.sort_values("_plot_x").reset_index(drop=True)
    return df


def load_equity_curve_summary(run_dir: Path) -> dict[str, float | int | str | None]:
    df = _load_equity_curve_dataframe(run_dir)

    start_value = float(df["equity_curve"].iloc[0])
    end_value = float(df["equity_curve"].iloc[-1])
    min_value = float(df["equity_curve"].min())
    max_value = float(df["equity_curve"].max())

    total_return_pct: float | None
    if start_value == 0:
        total_return_pct = None
    else:
        total_return_pct = (end_value / start_value - 1.0) * 100.0

    return {
        "points": int(len(df)),
        "start_date": str(df["_display_date"].iloc[0]),
        "end_date": str(df["_display_date"].iloc[-1]),
        "start_value": start_value,
        "end_value": end_value,
        "min_value": min_value,
        "max_value": max_value,
        "total_return_pct": total_return_pct,
    }


def render_equity_curve_png(run_dir: Path) -> bytes:
    df = _load_equity_curve_dataframe(run_dir)
    x = df["_plot_x"]
    y = df["equity_curve"]

    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    ax.plot(x, y, color="#2563eb", linewidth=2.2)
    ax.fill_between(x, y, y.min(), color="#3b82f6", alpha=0.08)

    if pd.api.types.is_datetime64_any_dtype(x):
        locator = mdates.AutoDateLocator()
        formatter = mdates.ConciseDateFormatter(locator)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        fig.autofmt_xdate()

    ax.set_title("Equity Curve", fontsize=12, fontweight="bold")
    ax.set_ylabel("Growth")
    ax.grid(alpha=0.2, linestyle="--", linewidth=0.6)
    ax.margins(x=0.01, y=0.05)

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=144, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    return buffer.read()


def ensure_equity_curve_plot(run_dir: Path, *, refresh: bool = False) -> Path:
    png_path = run_dir / EQUITY_CURVE_PLOT_ARTIFACT
    if refresh or not png_path.exists() or png_path.stat().st_size == 0:
        png_bytes = render_equity_curve_png(run_dir)
        png_path.write_bytes(png_bytes)
    return png_path


def export_artifacts(run_dir: Path, fmt: str) -> tuple[io.BytesIO | Path, str, str]:
    fmt_lower = fmt.lower()
    if fmt_lower == "csv":
        csv_path = run_dir / CSV_ARTIFACT
        if not csv_path.exists():
            raise FileNotFoundError(f"weights.csv not found for run '{run_dir.name}'")
        return csv_path, "text/csv", f"{run_dir.name}_weights.csv"

    if fmt_lower != "zip":
        raise ValueError(f"Unsupported export format '{fmt}'")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for child in sorted(run_dir.iterdir()):
            if child.is_file():
                archive.write(child, arcname=child.name)
    buffer.seek(0)
    return buffer, "application/zip", f"{run_dir.name}.zip"
