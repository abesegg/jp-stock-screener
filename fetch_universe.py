import pandas as pd
import requests

JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
TARGET_MARKETS = ["プライム（内国株式）", "スタンダード（内国株式）", "グロース（内国株式）"]

response = requests.get(JPX_URL, headers={"User-Agent": "Mozilla/5.0"})
response.raise_for_status()

with open("data_j.xlsx", "wb") as f:
    f.write(response.content)

df = pd.read_excel("data_j.xlsx")
df = df[df["市場・商品区分"].isin(TARGET_MARKETS)]
df = df[["コード", "銘柄名", "市場・商品区分", "33業種区分"]]
df["ticker"] = df["コード"].astype(str) + ".T"

df.to_csv("universe.csv", index=False)
print(f"{len(df)}銘柄を universe.csv に保存しました")
