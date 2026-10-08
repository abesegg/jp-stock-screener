"""JPXの上場銘柄一覧（data_j.xlsx）に関する共通処理"""
import io

import pandas as pd
import requests

JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"


def download_jpx_list():
    response = requests.get(JPX_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    return response.content


def list_date(xlsx):
    """一覧の基準日（「日付」列）を返す。xlsxはファイルパスまたはバイト列"""
    source = io.BytesIO(xlsx) if isinstance(xlsx, bytes) else xlsx
    df = pd.read_excel(source, usecols=["日付"], nrows=1)
    return pd.to_datetime(str(df["日付"].iloc[0])).date()
