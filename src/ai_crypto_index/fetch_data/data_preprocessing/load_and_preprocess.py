import os

import pandas as pd


def load_and_preprocess_data_fixed(
    data_folder: str = "data",
    dropna_all: bool = True,
    min_history_days: int = 365,
    start_date: str | None = None,
    end_date: str | None = None,
    include_delisted: bool = True,
    allow_internal_gaps: bool = True,
    tail_grace_days: int = 3,
):
    frames = []
    names = []

    for fname in os.listdir(data_folder):
        if not fname.endswith(".csv"):
            continue
        if fname.startswith("merged_") or fname.lower() in {"merged.csv", "merged_prices.csv"}:
            continue

        symbol = os.path.splitext(fname)[0]
        path   = os.path.join(data_folder, fname)

        # Try to be tolerant with delimiter/encoding
        try:
            df = pd.read_csv(path)  # default comma
            if df.shape[1] == 1:    # maybe semicolon-separated?
                df = pd.read_csv(path, sep=";")
        except Exception as e:
            print(f"[WARN] {fname}: read_csv failed ({e}), skip")
            continue

        # Try to coerce to [Date, Close]-like
        cols = [c.lower().strip() for c in df.columns]
        if len(cols) >= 2:
            # Common cases:
            # 1) Two columns already (Date, Close)
            # 2) OHLCV present -> pick Close if available
            date_candidates = [i for i, c in enumerate(cols) if c in {"date", "time"}]
            close_candidates = [
                i
                for i, c in enumerate(cols)
                if c in {"close", "adj close", "adj_close"}
            ]

            if len(df.columns) == 2:
                # assume first is Date, second is price
                df = df.iloc[:, :2]
                df.columns = ["Date", symbol]
            elif date_candidates and close_candidates:
                df = df.iloc[:, [date_candidates[0], close_candidates[0]]]
                df.columns = ["Date", symbol]
            else:
                print(f"[WARN] {fname}: can't find Date/Close columns, skip")
                continue
        else:
            print(f"[WARN] {fname}: unexpected column count ({len(cols)}), skip")
            continue

        # Parse date + index
        try:
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=False)
        except Exception as e:
            print(f"[WARN] {fname}: date parse failed ({e}), skip")
            continue

        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        frames.append(df)
        names.append(symbol)

    if not frames:
        raise ValueError("No usable CSV files parsed.")

    # Build full index
    if start_date and end_date:
        full_idx = pd.date_range(start=start_date, end=end_date, freq="D")
    else:
        gmin = min(f.index.min() for f in frames)
        gmax = max(f.index.max() for f in frames)
        full_idx = pd.date_range(start=gmin, end=gmax, freq="D")

    tail_grace = pd.Timedelta(days=max(0, int(tail_grace_days)))
    aligned, kept = [], []
    for symbol, f in zip(names, frames):
        fr = f.reindex(full_idx)
        s = fr[symbol]

        non_nan_days = s.notna().sum()
        if non_nan_days < min_history_days:
            print(f"[INFO] Drop {symbol}: only {non_nan_days} days (< {min_history_days}).")
            continue

        first_valid = s.first_valid_index()
        last_valid  = s.last_valid_index()
        if first_valid is None or last_valid is None:
            print(f"[INFO] Drop {symbol}: no valid data points.")
            continue

        tail_is_all_nan = False
        if last_valid < full_idx.max():
            tail_segment = s.loc[last_valid:]
            if len(tail_segment) > 1:
                all_nan_tail = tail_segment.iloc[1:].isna().all()
                tail_duration = full_idx.max() - last_valid
                tail_is_all_nan = all_nan_tail and tail_duration > tail_grace

        if (not include_delisted) and tail_is_all_nan:
            print(f"[INFO] Drop {symbol}: looks delisted (NaN tail after {last_valid.date()}).")
            continue

        # 3) internal gaps: NaN within [first_valid, last_valid]
        middle_has_nan = s.loc[first_valid:last_valid].isna().any()
        if (not allow_internal_gaps) and middle_has_nan:
            print(
                f"[INFO] Drop {symbol}: internal gaps between "
                f"{first_valid.date()} and {last_valid.date()}."
            )
            continue

        aligned.append(fr)
        kept.append(symbol)

    if not aligned:
        raise ValueError("All assets dropped by min_history_days filter.")

    merged = pd.concat(aligned, axis=1)

    if dropna_all:
        merged.dropna(how="all", inplace=True)

    return merged
