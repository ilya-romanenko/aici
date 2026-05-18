import numpy as np


def evaluate_portfolio_performance(df_log_returns, weights, annual_factor=365):
    """
    Evaluate portfolio performance based on daily log returns:
    - Mean annual return
    - Mean annual volatility
    - Sharpe Ratio
    - Maximum drawdown
    """

    # df_log_returns - DataFrame with asset columns
    # weights - np.array of weights with the same length as df_log_returns columns
    # annual_factor - number of days in a year (365 or ~252 for trading days)

    # 1. Check dimensions
    if len(weights) != df_log_returns.shape[1]:
        raise ValueError("The weights vector must match the number of assets in df_log_returns.")

    # 2. Daily portfolio log returns = sum(w_i * r_i)
    portfolio_log_ret = (df_log_returns * weights).sum(axis=1)

    # 3. Mean daily return and volatility
    mean_daily_ret = portfolio_log_ret.mean()
    std_daily_ret = portfolio_log_ret.std()

    # 4. Annual return = exp(mean_daily_ret * annual_factor) - 1
    #    (because we work with log returns)
    annual_return = np.exp(mean_daily_ret * annual_factor) - 1

    # 5. Annual volatility ~ std_daily_ret * sqrt(annual_factor)
    annual_volatility = std_daily_ret * np.sqrt(annual_factor)

    # 6. Sharpe = (r - rf) / vol, assuming rf=0
    sharpe_ratio = (annual_return) / annual_volatility if annual_volatility != 0 else 0

    # 7. Max drawdown
    # convert log returns to capital curve
    # if r_t is a log return, then capital_t = exp(cumsum(r_t))
    capital = np.exp(portfolio_log_ret.cumsum())
    running_max = capital.cummax()
    drawdown = (capital - running_max) / running_max
    max_drawdown = drawdown.min()  # the minimum value is the max drawdown (negative)

    result = {
        "AnnualReturn(%)": annual_return * 100,
        "AnnualVolatility(%)": annual_volatility * 100,
        "SharpeRatio": sharpe_ratio,
        "MaxDrawdown(%)": max_drawdown * 100  # negative number
    }
    return result