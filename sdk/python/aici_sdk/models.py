"""Lightweight dataclasses mirroring the public API payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class WeightEntry:
    asset: str
    weight: float

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WeightEntry":
        return cls(
            asset=str(payload.get("asset", "")).upper(),
            weight=_coerce_float(payload.get("weight")),
        )


@dataclass(slots=True)
class WeightsSnapshot:
    run_id: str
    items: list[WeightEntry] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WeightsSnapshot":
        run_id = str(payload.get("run_id") or "")
        rows = payload.get("items") or []
        items = [WeightEntry.from_payload(row) for row in rows]
        return cls(run_id=run_id, items=items)

    def as_dict(self) -> dict[str, float]:
        """Return weights indexed by asset ticker."""
        return {entry.asset: entry.weight for entry in self.items}

    def top_assets(self, limit: int = 10) -> list[WeightEntry]:
        """Return the heaviest assets sorted in descending order."""
        return sorted(self.items, key=lambda entry: entry.weight, reverse=True)[:limit]


@dataclass(slots=True)
class RunPerformance:
    run_id: str
    metrics: dict[str, float]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RunPerformance":
        run_id = str(payload.get("run_id") or "")
        metrics_payload = payload.get("metrics") or {}
        metrics = {key: _coerce_float(value) for key, value in metrics_payload.items()}
        return cls(run_id=run_id, metrics=metrics)

    def format_metric(self, key: str, *, precision: int = 4) -> str:
        """Format a metric if it exists, fallback to '-'."""
        value = self.metrics.get(key)
        if value is None:
            return "-"
        return f"{value:.{precision}f}"


@dataclass(slots=True)
class IndexComponent:
    rank: int
    asset: str
    weight_pct: float
    weight: float
    cumulative_pct: float
    relative_to_mean: float

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "IndexComponent":
        return cls(
            rank=int(payload.get("rank") or 0),
            asset=str(payload.get("asset") or ""),
            weight_pct=_coerce_float(payload.get("weight_pct")),
            weight=_coerce_float(payload.get("weight")),
            cumulative_pct=_coerce_float(payload.get("cumulative_pct")),
            relative_to_mean=_coerce_float(payload.get("relative_to_mean")),
        )


@dataclass(slots=True)
class IndexCompositionSummary:
    count: int
    top3_pct: float
    herfindahl: float
    effective_assets: float | None
    max_weight_pct: float
    min_weight_pct: float
    mean_weight_pct: float
    total_weight_pct: float

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "IndexCompositionSummary":
        return cls(
            count=int(payload.get("count") or 0),
            top3_pct=_coerce_float(payload.get("top3_pct")),
            herfindahl=_coerce_float(payload.get("herfindahl")),
            effective_assets=_coerce_float(payload.get("effective_assets"), default=None)
            if payload.get("effective_assets") is not None
            else None,
            max_weight_pct=_coerce_float(payload.get("max_weight_pct")),
            min_weight_pct=_coerce_float(payload.get("min_weight_pct")),
            mean_weight_pct=_coerce_float(payload.get("mean_weight_pct")),
            total_weight_pct=_coerce_float(payload.get("total_weight_pct")),
        )


@dataclass(slots=True)
class IndexComposition:
    run_id: str
    updated_display: str
    updated_iso: str
    assets: list[IndexComponent]
    summary: IndexCompositionSummary

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "IndexComposition":
        run_id = str(payload.get("run_id") or "")
        updated_display = str(payload.get("updated_display") or "")
        updated_iso = str(payload.get("updated_iso") or "")
        assets_payload = payload.get("assets") or []
        summary_payload = payload.get("summary") or {}
        assets = [IndexComponent.from_payload(row) for row in assets_payload]
        summary = IndexCompositionSummary.from_payload(summary_payload)
        return cls(
            run_id=run_id,
            updated_display=updated_display,
            updated_iso=updated_iso,
            assets=assets,
            summary=summary,
        )

    def top_symbols(self, limit: int = 5) -> list[str]:
        return [component.asset for component in sorted(self.assets, key=lambda x: x.weight_pct, reverse=True)[:limit]]


@dataclass(slots=True)
class PerformanceSnapshot:
    key: str
    label: str
    chart_caption: str
    metrics: dict[str, str]
    raw: Mapping[str, Any]

    @classmethod
    def from_payload(cls, key: str, payload: Mapping[str, Any]) -> "PerformanceSnapshot":
        cards = payload.get("metric_cards") or []
        metrics = {str(card.get("label") or f"metric_{index}") : str(card.get("value_text") or "")
                   for index, card in enumerate(cards)}
        return cls(
            key=key,
            label=str(payload.get("strategy_label") or key),
            chart_caption=str(payload.get("chart_caption") or ""),
            metrics=metrics,
            raw=payload,
        )


def parse_performance_snapshots(payload: Mapping[str, Any]) -> tuple[str, dict[str, PerformanceSnapshot]]:
    """Convert /performance payloads into friendly dataclasses."""
    default_key = str(payload.get("default_key") or "")
    snapshots_payload = payload.get("snapshots") or {}
    snapshots = {
        key: PerformanceSnapshot.from_payload(key, snapshot)
        for key, snapshot in snapshots_payload.items()
    }
    return default_key, snapshots
