import matplotlib.pyplot as plt
import pandas as pd


def analyze_data(df: pd.DataFrame):
    """
    Displays basic statistics and builds a correlation matrix for a DataFrame
    where columns represent prices of different coins.
    """
    print("===== BASIC STATISTICS =====")
    print(df.describe())

    # Correlation matrix (based on raw prices — not always correct; usually done on returns)
    corr_matrix_prices = df.corr()

    print("\n===== PRICE CORRELATIONS =====")
    print(corr_matrix_prices)

    # Build a correlation heatmap (for visualization purposes only)
    plt.figure(figsize=(8,6))
    plt.imshow(corr_matrix_prices, cmap='viridis', interpolation='none')
    plt.colorbar()
    plt.xticks(range(len(df.columns)), df.columns, rotation=90)
    plt.yticks(range(len(df.columns)), df.columns)
    plt.title("Correlation Matrix (Prices)")
    plt.show()
