from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent.parent
DIST_RESULTS_DIR = REPO_ROOT / "dist" / "static" / "results_performance"
SOURCE_RESULTS_DIR = PACKAGE_ROOT / "frontend" / "static" / "results_performance"

PERFORMANCE_STATE_DIR = "_performance"
PERFORMANCE_SERIES_SUBDIR = "series"


def resolve_performance_series_root(runs_root: Path) -> Path:
    return Path(runs_root) / PERFORMANCE_STATE_DIR / PERFORMANCE_SERIES_SUBDIR


def resolve_performance_series_write_path(*, runs_root: Path, filename: str) -> Path:
    root = resolve_performance_series_root(runs_root)
    root.mkdir(parents=True, exist_ok=True)
    return root / filename


def iter_performance_series_read_candidates(
    *,
    filename: str,
    runs_root: Path | None = None,
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if runs_root is not None:
        candidates.append(resolve_performance_series_root(runs_root) / filename)
    candidates.append(DIST_RESULTS_DIR / filename)
    candidates.append(SOURCE_RESULTS_DIR / filename)

    deduped: list[Path] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return tuple(deduped)


def resolve_performance_series_read_path(
    *,
    filename: str,
    runs_root: Path | None = None,
) -> Path | None:
    for candidate in iter_performance_series_read_candidates(
        filename=filename,
        runs_root=runs_root,
    ):
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


__all__ = [
    "DIST_RESULTS_DIR",
    "SOURCE_RESULTS_DIR",
    "PERFORMANCE_SERIES_SUBDIR",
    "PERFORMANCE_STATE_DIR",
    "iter_performance_series_read_candidates",
    "resolve_performance_series_read_path",
    "resolve_performance_series_root",
    "resolve_performance_series_write_path",
]
