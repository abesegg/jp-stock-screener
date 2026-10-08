import pandas as pd

from jpx import download_jpx_list

TARGET_MARKETS = ["プライム（内国株式）", "スタンダード（内国株式）", "グロース（内国株式）"]

with open("data_j.xlsx", "wb") as f:
    f.write(download_jpx_list())

df = pd.read_excel("data_j.xlsx")
df = df[df["市場・商品区分"].isin(TARGET_MARKETS)]
df = df[["コード", "銘柄名", "市場・商品区分", "33業種区分"]]
df["ticker"] = df["コード"].astype(str) + ".T"

df.to_csv("universe.csv", index=False)
print(f"{len(df)}銘柄を universe.csv に保存しました")
