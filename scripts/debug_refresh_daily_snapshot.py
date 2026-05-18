# scripts/debug_refresh_daily_snapshot.py
from datetime import datetime, timezone
from ai_crypto_index.shared.settings import get_settings
from ai_crypto_index.shared import daily_snapshot

settings = get_settings()

meta = daily_snapshot.refresh_daily_snapshot(
    settings,
    n_top_coins=100,
    snapshot_root="runs/_daily_snapshot_debug",
    now=datetime.now(timezone.utc),
)

df = daily_snapshot.load_snapshot_dataframe(meta)
print(meta.local_path, df.shape)
