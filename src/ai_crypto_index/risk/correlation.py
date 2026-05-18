import numpy as np
import pandas as pd


def ewma_cov(returns: pd.DataFrame, lam: float = 0.97) -> pd.DataFrame:
    R = returns.to_numpy()
    n = R.shape[1]
    S = np.eye(n) * 1e-8
    mu = np.zeros(n)
    for r in R:
        x = r - mu
        S = lam * S + (1 - lam) * np.outer(x, x)
    return pd.DataFrame(S, index=returns.columns, columns=returns.columns)

def corr_from_cov(S: pd.DataFrame) -> pd.DataFrame:
    d = np.sqrt(np.diag(S))
    R = S.values / np.outer(d, d)
    R = np.nan_to_num(R)
    R = np.clip(R, -0.99, 0.99)
    return pd.DataFrame(R, index=S.index, columns=S.columns)

def shrink_to_diag(S: pd.DataFrame, gamma: float = 0.05) -> pd.DataFrame:
    """Simple shrinkage toward the diagonal: gamma∈[0,1]. 0.05–0.10 are the working values from your tests."""
    D = np.diag(np.diag(S.values))
    S_shr = (1 - gamma) * S.values + gamma * D
    return pd.DataFrame(S_shr, index=S.index, columns=S.columns)
