import numpy as np
import pytest

from ai_crypto_index.optimization.optimization import project_to_bounded_simplex


def test_project_to_bounded_simplex_enforces_cap_and_budget():
    raw_weights = np.array([0.42, 0.31, 0.19, 0.08], dtype=float)
    projected = project_to_bounded_simplex(raw_weights, upper_bound=0.30)

    assert projected.sum() == pytest.approx(1.0)
    assert np.all(projected >= -1e-12)
    assert np.all(projected <= 0.30 + 1e-12)


def test_project_to_bounded_simplex_keeps_feasible_point():
    feasible = np.array([0.28, 0.27, 0.25, 0.20], dtype=float)
    projected = project_to_bounded_simplex(feasible, upper_bound=0.30)

    assert np.allclose(projected, feasible)


def test_project_to_bounded_simplex_raises_for_infeasible_cap():
    with pytest.raises(ValueError, match="Infeasible bounded-simplex constraints"):
        project_to_bounded_simplex(np.array([0.5, 0.3, 0.2], dtype=float), upper_bound=0.30)
