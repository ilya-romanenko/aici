
def select_assets_balanced(
    cluster_dict,
    df_log,
    total_assets=10,
    metric="sharpe",
    corr_threshold=0.9,
):
    """Select assets across clusters while capping intra-cluster correlation."""
    from collections import defaultdict

    df_corr = df_log.corr()
    clusters = defaultdict(list)
    for asset, cid in cluster_dict.items():
        clusters[cid].append(asset)

    # (1) Determine how many assets to select from each cluster
    cluster_sizes = {cid: len(assets) for cid, assets in clusters.items()}
    sorted_clusters = sorted(cluster_sizes.items(), key=lambda x: -x[1])  # largest clusters first

    selected_assets = []
    remaining_assets = total_assets

    for cid, size in sorted_clusters:
        assets = clusters[cid]
        if remaining_assets <= 0:
            break

        if size <= 2:
            n_select = 1
        else:
            n_select = min(size, remaining_assets // 2 if cid == sorted_clusters[0][0] else 1)

        # Select the best assets from the cluster taking correlation into account
        asset_scores = []
        for asset in assets:
            series = df_log[asset].dropna()
            if len(series) < 2:
                continue
            mean_ = series.mean()
            std_ = series.std()
            score = mean_ / std_ if std_ > 1e-8 else float('-inf')
            asset_scores.append((asset, score))

        asset_scores.sort(key=lambda x: x[1], reverse=True)

        filtered = []
        for candidate, score in asset_scores:
            if len(filtered) >= n_select:
                break
            if all(
                abs(df_corr.loc[candidate, chosen]) < corr_threshold
                for chosen in filtered
            ):
                filtered.append(candidate)

        selected_assets.extend(filtered)
        remaining_assets -= len(filtered)

    # If not enough assets were selected — fill up with the best from all remaining
    if remaining_assets > 0:
        remaining_candidates = [
            asset for asset in df_log.columns if asset not in selected_assets
        ]
        asset_scores = []
        for asset in remaining_candidates:
            series = df_log[asset].dropna()
            if len(series) < 2:
                continue
            mean_ = series.mean()
            std_ = series.std()
            score = mean_ / std_ if std_ > 1e-8 else float('-inf')
            asset_scores.append((asset, score))
        asset_scores.sort(key=lambda x: x[1], reverse=True)

        for candidate, score in asset_scores:
            if len(selected_assets) >= total_assets:
                break
            if all(
                abs(df_corr.loc[candidate, chosen]) < corr_threshold
                for chosen in selected_assets
            ):
                selected_assets.append(candidate)

    return selected_assets
