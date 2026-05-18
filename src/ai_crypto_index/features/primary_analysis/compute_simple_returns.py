import pandas as pd


def compute_simple_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes the simple daily return for each coin in the DataFrame.
    df - prices (columns represent individual coins).
    Returns a DataFrame with return columns.
    """
    returns_df = df.pct_change().dropna()
    return returns_df