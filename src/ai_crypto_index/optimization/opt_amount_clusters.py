import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score

from ai_crypto_index.optimization.clustering import corr_matrix, corr_to_dist, detone_correlation


def find_optimal_clusters(df_log, max_k=10, linkage='average'):
    corr = corr_matrix(df_log, method='spearman', shrink=True)
    corr = detone_correlation(corr, n_components=1)

    if corr.shape[0] < 2:
        return 2

    Dfull = corr_to_dist(corr).values
    np.fill_diagonal(Dfull, 0.0)

    good = np.isfinite(Dfull).all(axis=0)
    if not good.all():
        Dfull = Dfull[np.ix_(good, good)]
        if Dfull.shape[0] < 2:
            return 2

    max_k = max(2, min(max_k, Dfull.shape[0]-1))

    best_k, best_score = 2, -1
    for k in range(2, max_k+1):
        model = AgglomerativeClustering(
            n_clusters=k, metric='precomputed', linkage=linkage)
        labels = model.fit_predict(Dfull)
        score = silhouette_score(Dfull, labels, metric='precomputed')
        if score > best_score:
            best_k, best_score = k, score
    return best_k