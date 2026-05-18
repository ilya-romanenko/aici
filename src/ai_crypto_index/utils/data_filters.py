
import pandas as pd


def filter_assets_by_history(df_log: pd.DataFrame, assets: list[str], min_len: int) -> list[str]:
    """Returns assets that have >= min_len non-NaN observations."""
    ok: list[str] = []
    for a in assets:
        s = df_log[a].dropna()
        if len(s) >= min_len:
            ok.append(a)
    return ok
