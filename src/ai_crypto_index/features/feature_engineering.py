import numpy as np
import pandas as pd


def compute_log_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Log returns with tolerance for missing values:
    - replace ±inf with NaN,
    - drop only COMPLETELY empty rows,
    - optionally filter columns with too few observations (done downstream in the pipeline).
    """
    log_df = np.log(df)
    log_ret = log_df.diff()
    log_ret = log_ret.replace([np.inf, -np.inf], np.nan)
    log_ret = log_ret.dropna(how="all")
    return log_ret