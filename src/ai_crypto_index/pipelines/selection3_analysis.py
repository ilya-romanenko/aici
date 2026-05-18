#!/usr/bin/env python3
"""section3_analysis.py

End‑to‑end quantitative toolkit for **Section 3** of the bachelor thesis
"FORMING AN OPTIMAL PORTFOLIO USING AN AI-POWERED CRYPTO INDEX".

The script consumes already‑simulated artefacts produced in earlier sections
(`equity_curve.csv`, `monthly_weights.csv`) plus individual price files (e.g.
`BTC_price.csv`, `ETH_price.csv`, `SOL_price.csv`). All files can live either
next to this script in `data/` or directly in `/mnt/data/` — both paths are
searched automatically.

Key capabilities
================
0.  **Data loaders** – locate and read index & asset price series.
1.  **Risk/return helpers** – CAGR, Volatility, Sharpe, Sortino, CVaR95,
    Max‑Drawdown.
2.  **Markowitz engine** – light‑weight random search of the efficient frontier.
3.  **Back‑test module** – constant‑weight & periodic‑rebalance portfolios.
4.  **Plotting** – cumulative equity curves and efficient frontier chart.
5.  **demo()** – one‑click pipeline that reproduces artefacts required for
    subsections 3.2–3.4 (tables + figures printed / popped‑up).

Dependencies: `pandas`, `numpy`, `matplotlib`, `scipy`.
"""
from __future__ import annotations

import math
import pathlib
import sys
from collections.abc import Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from numpy.random import default_rng

# ──────────────────────────────────────────────────────────────────────────────
# 0 ── DATA LOCATORS & LOADERS
# ──────────────────────────────────────────────────────────────────────────────

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIRS = (ROOT / "analysis_data", pathlib.Path("/mnt/data"))


def _find_file(name: str) -> pathlib.Path:
    """Return first matching path among DATA_DIRS; raise if not found."""
    for folder in DATA_DIRS:
        candidate = folder / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Cannot locate required data file: {name}")


def read_index_log_returns() -> pd.Series:
    """Read AI‑Index *logarithmic* daily returns from equity_curve.csv."""
    path = _find_file("equity_curve.csv")
    df = pd.read_csv(path, index_col=0, parse_dates=True)

    log_ret = df.iloc[:, 0].astype(float)
    log_ret.name = "AI_Index_log_ret"
    return log_ret


def read_price_series(symbol: str) -> pd.Series:
    """Read daily close for *symbol* from either `${symbol}_price.csv` or
    `equity_curve.csv` in case of AI_Index."""
    if symbol.upper() in {"AI", "AI_INDEX", "AI-INDEX"}:
        # build price series from already‑read log returns
        log_ret = read_index_log_returns()
        price = logret_to_price(log_ret, start_price=1.0)
        price.name = "AI_Index"
        return price

    fname_options = [f"{symbol}_price.csv", f"{symbol.upper()}_price.csv"]
    for fname in fname_options:
        try:
            path = _find_file(fname)
        except FileNotFoundError:
            continue
        df = pd.read_csv(path, index_col=0, parse_dates=True)

        # === AUTO-DETECT 'Close' column regardless of case
        close_col = next((c for c in df.columns if c.lower() == 'close'), None)
        if close_col is None:
            columns_list = df.columns.tolist()
            raise ValueError(
                f"'Close' column not found in file {fname}. Columns: {columns_list}"
            )

        price = df[close_col]
        price.name = symbol
        return price

    raise FileNotFoundError(f"Price file for symbol '{symbol}' not found.")



# ──────────────────────────────────────────────────────────────────────────────
# 1 ── PERFORMANCE / RISK METRICS
# ──────────────────────────────────────────────────────────────────────────────

def logret_to_price(log_ret: pd.Series, start_price: float = 1.0) -> pd.Series:
    """Convert log‑return series to price series (capital curve)."""
    price = start_price * np.exp(log_ret.cumsum())
    price.name = log_ret.name.replace("log_ret", "price")
    return price


def performance_summary_log(log_ret: pd.Series,
                            annual_factor: int = 365) -> pd.Series:
    """
    Evaluation based on log-returns (identical to the test script).
    Returns a Series: CAGR, Volatility, Sharpe, Sortino, MaxDrawdown, CVaR95.
    """
    log_ret = log_ret.dropna()
    mean_daily = log_ret.mean()
    std_daily  = log_ret.std()

    annual_return   = math.exp(mean_daily * annual_factor) - 1
    annual_vol      = std_daily * math.sqrt(annual_factor)
    sharpe          = (annual_return) / annual_vol  if annual_vol != 0 else 0

    downside_std = log_ret[log_ret < 0].std() * math.sqrt(annual_factor)
    sortino      = annual_return / downside_std if downside_std else np.nan

    capital = np.exp(log_ret.cumsum())
    max_dd  = ((capital.cummax() - capital) / capital.cummax()).max()

    cvar95 = log_ret[log_ret < log_ret.quantile(0.05)].mean()

    return pd.Series({
        "CAGR":          annual_return,
        "Volatility":    annual_vol,
        "Sharpe":        sharpe,
        "Sortino":       sortino,
        "MaxDrawdown":   max_dd,
        "CVaR95":        cvar95,
    })

def portfolio_log_returns(prices: pd.DataFrame,
                          weights: Mapping[str, float]) -> pd.Series:
    """From asset prices → daily portfolio log-returns Σ w_i·r_i."""
    log_ret = np.log(prices).diff().dropna()
    w_vec = np.array([weights[c] for c in prices.columns])
    return (log_ret * w_vec).sum(axis=1)

# ──────────────────────────────────────────────────────────────────────────────
# 2 ── MARKOWITZ FRONTIER (RANDOM SEARCH)
# ──────────────────────────────────────────────────────────────────────────────

def _rand_weights(n: int, rng: np.random.Generator) -> np.ndarray:
    w = rng.random(n)
    return w / w.sum()


def markowitz_efficient_frontier(prices: pd.DataFrame, n_portf: int = 5000,
                                  rng: np.random.Generator | None = None,
                                  allow_short: bool = False) -> pd.DataFrame:
    """Generate a random‑search efficient frontier DataFrame for the given price
    DataFrame (daily close). Columns: Return, Volatility, Sharpe, *w_i*.
    """
    if rng is None:
        rng = default_rng(42)
    daily_ret = prices.pct_change().dropna()
    exp_ret = daily_ret.mean() * 252
    cov = daily_ret.cov() * 252

    records: list[dict[str, float]] = []
    syms = list(prices.columns)
    for _ in range(n_portf):
        if allow_short:
            # allow weights in [‑1,1] but sum==1
            w = rng.uniform(-1, 1, len(syms))
            if abs(w.sum()) < 1e-3:
                continue
            w /= w.sum()
        else:
            w = _rand_weights(len(syms), rng)
        port_ret = float(np.dot(w, exp_ret))
        port_vol = float(np.sqrt(np.dot(w, cov @ w)))
        sharpe = port_ret / port_vol if port_vol != 0 else np.nan
        rec = {"Return": port_ret, "Volatility": port_vol, "Sharpe": sharpe}
        rec.update({f"w_{s}": w_i for s, w_i in zip(syms, w)})
        records.append(rec)

    df = pd.DataFrame.from_records(records)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 3 ── PORTFOLIO BACKTESTER
# ──────────────────────────────────────────────────────────────────────────────

def backtest_portfolio(prices: pd.DataFrame, weights: Mapping[str, float],
                       rebalance: str | None = None) -> pd.Series:
    """Return capital curve of a constant‑weight portfolio.

    *prices* must contain columns for every key in *weights* (will be aligned
    internally). *rebalance* accepts pandas offset aliases ('M', 'Q', etc.) or
    None for buy‑and‑hold.
    """
    # Align & filter
    prices = prices[list(weights)].dropna()
    returns = prices.pct_change().dropna()

    if rebalance is None:
        w_vec = np.array([weights[c] for c in prices.columns])
        port_ret = returns.values @ w_vec
        port_price = (1 + pd.Series(port_ret, index=returns.index)).cumprod()
        return port_price

    # periodic rebalancing: iterate over periods
    periods = returns.groupby(pd.Grouper(freq=rebalance))
    capital = 1.0
    capital_curve = []
    for _, df in periods:
        if df.empty:
            continue
        w_vec = np.array([weights[c] for c in df.columns])
        period_ret = (df.values @ w_vec)  # daily returns within period
        period_curve = capital * (1 + pd.Series(period_ret, index=df.index)).cumprod()
        capital_curve.append(period_curve)
        capital = period_curve.iloc[-1]  # update capital for next period
    return pd.concat(capital_curve)


# ──────────────────────────────────────────────────────────────────────────────
# 4 ── PLOTTING UTILITIES
# ──────────────────────────────────────────────────────────────────────────────

def plot_equity_curves(curves: dict[str, pd.Series]) -> None:
    plt.figure(figsize=(10, 5))
    for label, ser in curves.items():
        plt.plot(ser.index, ser, label=label, drawstyle="steps-post")
    plt.title("Portfolio Cumulative Returns")
    plt.legend()
    plt.xlabel("Date")
    plt.ylabel("Capital")
    plt.tight_layout()
    plt.show()


def plot_efficient_frontier(df_front: pd.DataFrame,
                            best_point: Sequence[float] | None = None) -> None:
    plt.figure(figsize=(7, 5))
    plt.scatter(df_front["Volatility"], df_front["Return"], s=5, alpha=0.5)
    if best_point is not None:
        plt.scatter(best_point[0], best_point[1], marker="*", s=200,
                    label="Max‑Sharpe", zorder=5)
        plt.legend()
    plt.xlabel("Annualised Volatility")
    plt.ylabel("Annualised Return")
    plt.title("Markowitz Efficient Frontier (random search)")
    plt.grid(True)
    plt.tight_layout()
    plt.show()


# ──────────────────────────────────────────────────────────────────────────────
# 5 ── MAIN DEMONSTRATION PIPELINE
# ──────────────────────────────────────────────────────────────────────────────

def demo() -> None:
    """Quick demo that rebuilds Section 3 key artefacts."""
    # ------------------------------------------------------------------
    # 5.1  AI‑Index performance
    # ------------------------------------------------------------------
    ai_log_ret = read_index_log_returns()
    ai_price = logret_to_price(ai_log_ret, start_price=1.0)
    metrics_ai = performance_summary_log(ai_log_ret)
    print("\nAI‑Index summary\n", metrics_ai.to_string(float_format="{:.3%}".format), "\n")

    # ------------------------------------------------------------------
    # 5.2  Load BTC & ETH prices for correlation / benchmark
    # ------------------------------------------------------------------
    btc = read_price_series("BTC")
    eth = read_price_series("ETH")

    # Align index & combine
    df_prices = pd.concat([ai_price, btc, eth], axis=1, join="inner")
    df_prices.columns = ["AI_Index", "BTC", "ETH"]

    corr = df_prices.pct_change().dropna().corr()
    print("Correlation matrix\n", corr, "\n")

    # ------------------------------------------------------------------
    # 5.3  Markowitz frontier using BTC, ETH, AI‑Index
    # ------------------------------------------------------------------
    frontier = markowitz_efficient_frontier(df_prices)
    idx_max_sharpe = frontier["Sharpe"].idxmax()
    best_pt = frontier.loc[idx_max_sharpe, ["Volatility", "Return"]].values
    print("Max‑Sharpe portfolio weights:")
    print(frontier.loc[idx_max_sharpe, [c for c in frontier.columns if c.startswith("w_")]])

    # Plot efficient frontier
    plot_efficient_frontier(frontier, best_pt)

    # ------------------------------------------------------------------
    # 5.4  Construct thesis portfolios
    # ------------------------------------------------------------------
    allocs = {
        "Only_Index": {"AI_Index": 1.0},
        "Index+Stable": {"AI_Index": 0.7, "USDC": 0.3},
        "Index+Alts": {"AI_Index": 0.5, "SOL": 0.25, "LINK": 0.25},
        "BTC+ETH_50_50": {"BTC": 0.5, "ETH": 0.5},
    }

    # Ensure all symbols present; load extra price series if needed
    extra_symbols = {
        sym for allocation in allocs.values() for sym in allocation} - set(df_prices.columns)
    for sym in extra_symbols:
        try:
            ser = read_price_series(sym)
        except FileNotFoundError as e:
            print(f"[WARN] {e}. Skipping symbol in portfolios.")
            # remove symbol from any allocation if file absent
            for k in list(allocs):
                allocs[k].pop(sym, None)
            continue
        df_prices[sym] = ser

    # Drop rows with NaNs after adding new assets
    df_prices_raw = df_prices

    # Back‑test each portfolio (buy‑and‑hold in this demo)
    curves: dict[str, pd.Series] = {}
    metrics_records: list[dict[str, float]] = []
    for name, w in allocs.items():
        if not set(w).issubset(df_prices_raw.columns):
            print(f"[SKIP] {name} – missing price series.")
            continue
        curve_prices = df_prices_raw[w.keys()].dropna()   # trim only for the required assets
        curve = backtest_portfolio(curve_prices, w, rebalance=None)
        curves[name] = curve

        port_log_ret = portfolio_log_returns(curve_prices, w)
        summary = performance_summary_log(port_log_ret)
        summary["Portfolio"] = name
        metrics_records.append(summary)

    # ------------------------------------------------------------------
    # 5.5  Output comparison
    # ------------------------------------------------------------------
    if curves:
        plot_equity_curves(curves)
    if metrics_records:
        metrics_df = pd.DataFrame(metrics_records).set_index("Portfolio")
        print("Performance comparison (buy‑and‑hold)\n",
              metrics_df.to_string(float_format="{:.3%}".format))


# ──────────────────────────────────────────────────────────────────────────────
# 6 ── SCRIPT ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        demo()
    except FileNotFoundError as exc:
        print("✖ Data file missing:", exc, file=sys.stderr)
        sys.exit(1)
