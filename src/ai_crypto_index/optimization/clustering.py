import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
from scipy.spatial.distance import squareform


def corr_to_dist(corr: pd.DataFrame) -> pd.DataFrame:
    # clip to be safe
    C = np.clip(corr.values, -1 + 1e-6, 1 - 1e-6)
    dist = np.sqrt(0.5 * (1.0 - C))  # [0..1]
    # symmetry + diagonal
    dist = 0.5 * (dist + dist.T)
    np.fill_diagonal(dist, 0.0)
    return pd.DataFrame(dist, index=corr.index, columns=corr.columns)


def detone_correlation(
    corr: pd.DataFrame,
    n_components: int = 1,
    eps: float = 1e-8,
) -> pd.DataFrame:
    """
    Removes n_components principal factors with numerical stabilization.
    Returns a valid correlation matrix (diag=1, symmetric, PSD approx).
    """
    # symmetrize just in case
    C = 0.5 * (corr.values + corr.values.T)

    # spectral decomposition
    vals, vecs = np.linalg.eigh(C)  # for symmetric matrices
    idx = np.argsort(vals)[::-1]
    vals, vecs = vals[idx], vecs[:, idx]

    # subtract top components
    for i in range(min(n_components, len(vals))):
        v = vecs[:, i:i+1]
        C = C - vals[i] * (v @ v.T)

    # numerical floor (negative diagonal/eigenvalues can appear after subtraction)
    vals2, vecs2 = np.linalg.eigh(0.5 * (C + C.T))
    vals2 = np.clip(vals2, eps, None)  # enforce PSD
    C = (vecs2 @ np.diag(vals2) @ vecs2.T)

    # rescale diagonal back to 1
    d = np.sqrt(np.clip(np.diag(C), eps, None))
    C = C / np.outer(d, d)

    # clip boundary values and symmetrize
    C = np.clip(C, -1 + 1e-6, 1 - 1e-6)
    C = 0.5 * (C + C.T)
    np.fill_diagonal(C, 1.0)

    return pd.DataFrame(C, index=corr.index, columns=corr.columns)



def corr_matrix(
    df_returns: pd.DataFrame,
    method: str = "spearman",
    shrink: bool = True,
) -> pd.DataFrame:
    """
    Robust estimation of the correlation matrix.
    method: 'pearson' | 'spearman'
    shrink: if True — apply Ledoit-Wolf shrinkage
    """
    df = df_returns.replace([np.inf, -np.inf], np.nan).copy()

    if method == 'spearman':
        corr = df.corr(method='spearman', min_periods=max(10, int(0.5*len(df))))
    elif shrink:
        if df.isna().any().any():
            corr = df.corr()
        elif shrink:
            from sklearn.covariance import LedoitWolf
            lw = LedoitWolf().fit(df.values)
            cov = lw.covariance_
            d = np.sqrt(np.diag(cov))
            corr = pd.DataFrame(cov / np.outer(d, d), index=df.columns, columns=df.columns)
        else:
            corr = df.corr()

    corr = corr.fillna(0.0)
    np.fill_diagonal(corr.values, 1.0)
    return corr


def hierarchical_clustering_by_corr(
    df_returns: pd.DataFrame,
    method: str = 'average',
    max_clusters: int = None,
    dist_threshold: float = None,
    show_dendrogram: bool = False
):
    """
    Hierarchical clustering of assets based on correlations.

    :param df_returns: log returns DataFrame with asset columns
    :param method: linkage method
    :param max_clusters: number of clusters (if specified)
    :param dist_threshold: distance for cutting the dendrogram (alternative to max_clusters)
    :param show_dendrogram: whether to display the dendrogram
    :return: (cluster_dict, actual_n_clusters)
    """
    corr = corr_matrix(df_returns, method='spearman', shrink=True)
    corr = detone_correlation(corr, n_components=1)

    if corr.shape[0] < 2:
        return {}

    dist = corr_to_dist(corr)
    dist_condensed = squareform(dist, checks=False)
    Z = linkage(dist_condensed, method=method)

    if max_clusters is not None:
        labels = fcluster(Z, max_clusters, criterion='maxclust')
        cut_height = Z[-(max_clusters-1), 2] - 1e-12 if max_clusters > 1 else np.inf
    elif dist_threshold is not None:
        labels = fcluster(Z, dist_threshold, criterion='distance')
        cut_height = dist_threshold
    else:
        raise ValueError("Either max_clusters or dist_threshold must be provided.")

    assets = df_returns.columns
    cluster_dict = {asset: clust_id for asset, clust_id in zip(assets, labels)}

    if show_dendrogram:
        clean_labels = [a.replace('-USD_Close', '') for a in assets]
        plt.figure(figsize=(12, 5))
        plt.title("Hierarchical Clustering Dendrogram")
        plt.xlabel("Assets")
        plt.ylabel("Distance")

        dn = dendrogram(
            Z,
            labels=clean_labels,
            leaf_rotation=45,
            color_threshold=cut_height  # highlight the cut level
        )

        # colored cluster strip below the leaves
        # dn['leaves'] — indices of original observations in dendrogram order
        order = dn['leaves']
        labels_ordered = labels[order]

        # draw a thin strip with cluster colors
        ax = plt.gca()
        ymin, ymax = ax.get_ylim()
        # small margin below the X axis
        plt.subplots_adjust(bottom=0.22)

        ax2 = ax.inset_axes([0.0, -0.18, 1.0, 0.08])
        ax2.imshow(labels_ordered[np.newaxis, :], aspect='auto')
        ax2.set_yticks([])
        ax2.set_xticks(range(len(order)))
        ax2.set_xticklabels([clean_labels[i] for i in order], rotation=45, ha='right', fontsize=8)
        ax2.set_title("Cluster labels along dendrogram leaves", fontsize=9)
        plt.tight_layout()
        plt.show()
        plt.close('all')

    return cluster_dict
