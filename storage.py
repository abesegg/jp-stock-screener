from pathlib import Path

import pandas as pd


def append_dedup(new_df, path, key_cols, keep="last"):
    # keep="last": 新しいデータで既存行を上書き / keep="first": 既存行を残し、新しい行だけ追加
    if Path(path).exists():
        existing = pd.read_csv(path)
        combined = pd.concat([existing, new_df]).drop_duplicates(subset=key_cols, keep=keep)
    else:
        combined = new_df
    combined = combined.sort_values(key_cols).reset_index(drop=True)
    combined.to_csv(path, index=False)
    return combined
