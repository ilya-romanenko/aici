from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import numpy as np

_FLOAT_TYPES = (np.floating,)
_INT_TYPES = (np.integer,)


def to_builtin(value: Any) -> Any:
    """Recursively convert dataclasses and numpy scalars to JSON-safe types."""

    if is_dataclass(value):
        return {key: to_builtin(item) for key, item in asdict(value).items()}

    if isinstance(value, Mapping):
        return {str(key): to_builtin(item) for key, item in value.items()}

    if isinstance(value, (Sequence, set)) and not isinstance(value, (str, bytes, bytearray)):
        return [to_builtin(item) for item in value]

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, np.ndarray):
        return [to_builtin(item) for item in value.tolist()]

    if isinstance(value, _FLOAT_TYPES):
        return float(value)

    if isinstance(value, _INT_TYPES):
        return int(value)

    return value


__all__ = ["to_builtin"]
