import matplotlib.pyplot as plt
import pandas as pd
from scr.feature_engineering import compute_log_returns

from .general_analysis import analyze_data


def main_analysis_workflow(merged_csv_path="data/merged_prices.csv"):
    # 1) Load the file
    df_prices = pd.read_csv(merged_csv_path, parse_dates=['Date'], index_col='Date')

    # 2) Run basic analysis on prices
    analyze_data(df_prices)

    # 3) Compute log returns (recommended for financial applications)
    df_log_returns = compute_log_returns(df_prices)

    # 4) Inspect return statistics
    print("\n===== LOG RETURNS STATISTICS =====")
    print(df_log_returns.describe())

    # 5) Return correlations
    corr_matrix_returns = df_log_returns.corr()
    print("\n===== RETURN CORRELATIONS =====")
    print(corr_matrix_returns)

    # Visualize return correlations
    plt.figure(figsize=(8,6))
    plt.imshow(corr_matrix_returns, cmap='viridis', interpolation='none')
    plt.colorbar()
    plt.xticks(range(len(df_log_returns.columns)), df_log_returns.columns, rotation=90)
    plt.yticks(range(len(df_log_returns.columns)), df_log_returns.columns)
    plt.title("Correlation Matrix (Log Returns)")
    plt.show()
    
    return df_prices, df_log_returns


if __name__ == "__main__":
    main_analysis_workflow()