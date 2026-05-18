import numpy as np
from scipy.optimize import minimize


def _risk_contributions(w, cov):
    """Return asset-level risk contributions and portfolio variance."""
    port_var = w @ cov @ w
    marginal = cov @ w
    rc = w * marginal
    return rc, port_var


def _effective_max_weight(n_assets, min_weight, max_weight, tol=1e-12):
    """Ensure bounds admit a feasible allocation and return adjusted upper bound."""
    if n_assets * min_weight - 1.0 > tol:
        raise ValueError(
            f"min_weight={min_weight} is too high for {n_assets} assets; "
            "the lower bounds sum to more than 1."
        )

    required_max = 1.0 - (n_assets - 1) * min_weight
    max_weight_eff = max(max_weight, required_max)
    max_weight_eff = min(1.0, max_weight_eff)

    if n_assets * max_weight_eff < 1.0 - tol:
        raise ValueError(
            f"max_weight={max_weight} is too low for {n_assets} assets; "
            "the upper bounds cannot sum to 1."
        )

    if max_weight_eff < min_weight - tol:
        raise ValueError(
            "Adjusted max_weight falls below min_weight; please revise bounds."
        )

    return max_weight_eff


def project_to_bounded_simplex(
    weights,
    *,
    upper_bound,
    lower_bound=0.0,
    target_sum=1.0,
    tol=1e-12,
    max_iter=200,
):
    """Project weights to {w | sum(w)=target_sum, lower_bound<=w<=upper_bound}."""
    w = np.asarray(weights, dtype=float)
    if w.ndim != 1 or w.size == 0:
        raise ValueError("weights must be a non-empty 1D array.")
    if not np.isfinite(w).all():
        raise ValueError("weights must contain only finite values.")
    if not np.isfinite(lower_bound) or not np.isfinite(upper_bound):
        raise ValueError("Bounds must be finite numbers.")
    if upper_bound < lower_bound:
        raise ValueError("upper_bound must be greater or equal to lower_bound.")

    n_assets = w.size
    feasible_min = n_assets * lower_bound
    feasible_max = n_assets * upper_bound
    if target_sum < feasible_min - tol or target_sum > feasible_max + tol:
        raise ValueError(
            "Infeasible bounded-simplex constraints: adjust weight_cap or asset count."
        )

    low = float(np.min(w) - upper_bound)
    high = float(np.max(w) - lower_bound)
    projected = np.clip(w, lower_bound, upper_bound)

    for _ in range(max_iter):
        shift = 0.5 * (low + high)
        projected = np.clip(w - shift, lower_bound, upper_bound)
        total = float(projected.sum())
        if abs(total - target_sum) <= tol:
            break
        if total > target_sum:
            low = shift
        else:
            high = shift

    projected = np.clip(w - 0.5 * (low + high), lower_bound, upper_bound)
    if abs(float(projected.sum()) - target_sum) > 1e-9:
        raise RuntimeError("Bounded-simplex projection did not converge.")
    return projected


def risk_parity_weights(
    cov_matrix,
    min_weight=0.03,
    max_weight=0.25,
    target_risk=None,
    initial_weights=None,
    max_iter=5_000,
    ftol=1e-12,
):
    """Compute risk-parity weights under box constraints and a sum-to-one budget."""
    cov = cov_matrix.values if hasattr(cov_matrix, "values") else np.asarray(cov_matrix)
    n_assets = cov.shape[0]
    if cov.shape[0] != cov.shape[1]:
        raise ValueError("cov_matrix must be square")

    max_weight_eff = _effective_max_weight(n_assets, min_weight, max_weight)

    w0 = (
        np.full(n_assets, 1.0 / n_assets)
        if initial_weights is None
        else np.asarray(initial_weights)
    )
    w0 = np.clip(w0, min_weight, max_weight_eff)
    w0 = w0 / w0.sum()

    bounds = [(min_weight, max_weight_eff)] * n_assets
    eq_cons = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}

    def objective(w, cov):
        rc, port_var = _risk_contributions(w, cov)
        target_rc = port_var / n_assets
        return np.sum((rc - target_rc) ** 2)

    res = minimize(
        objective,
        w0,
        args=(cov,),
        method="SLSQP",
        bounds=bounds,
        constraints=[eq_cons],
        options={"maxiter": max_iter, "ftol": ftol, "disp": False},
    )

    if not res.success:
        raise RuntimeError(f"Risk-parity optimization failed: {res.message}")

    w = res.x

    if target_risk is not None:
        port_std = np.sqrt(w @ cov @ w)
        if port_std > 1e-12:
            w *= target_risk / port_std
            overflow = w.sum() - 1.0
            if (
                abs(overflow) > 1e-10
                or (w < min_weight - 1e-12).any()
                or (w > max_weight_eff + 1e-12).any()
            ):
                w = np.clip(w, min_weight, max_weight_eff)
                free = (w > min_weight + 1e-12) & (w < max_weight_eff - 1e-12)
                if free.any():
                    w[free] -= (w.sum() - 1.0) / free.sum()
                w = np.clip(w, min_weight, max_weight_eff)
                w /= w.sum()

    return w
