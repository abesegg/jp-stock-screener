import pandas as pd

BENCHMARK_TICKER = "1306.T"
RS_PERIOD = 21
GOLDEN_CROSS_SHORT = 5
GOLDEN_CROSS_LONG = 25
GOLDEN_CROSS_LOOKBACK = 3


def liquidity_passed_tickers(screening_result_path="screening_result.csv"):
    df = pd.read_csv(screening_result_path)
    return df[df["除外"] == False]["ticker"].tolist()


def load_close_wide(daily_ohlcv_path="daily_ohlcv.csv"):
    df = pd.read_csv(daily_ohlcv_path, parse_dates=["date"])
    return df.pivot(index="date", columns="ticker", values="close").sort_index()


def relative_strength(tickers, close_wide, benchmark_ticker=BENCHMARK_TICKER, period=RS_PERIOD):
    if len(close_wide) <= period:
        raise ValueError(
            f"データが{period}営業日分蓄積されていません（現在{len(close_wide)}日分）。"
            "backfill_history.pyの実行を確認してください。"
        )

    available = [t for t in tickers if t in close_wide.columns]
    stock_return = close_wide[available].iloc[-1] / close_wide[available].iloc[-1 - period] - 1
    benchmark_return = close_wide[benchmark_ticker].iloc[-1] / close_wide[benchmark_ticker].iloc[-1 - period] - 1

    rs = (stock_return - benchmark_return).dropna()
    return rs.sort_values(ascending=False)


def relative_strength_series(ticker, close_wide, benchmark_ticker=BENCHMARK_TICKER, period=RS_PERIOD):
    if ticker not in close_wide.columns or benchmark_ticker not in close_wide.columns:
        return pd.Series(dtype=float)

    stock_ret = close_wide[ticker] / close_wide[ticker].shift(period) - 1
    bench_ret = close_wide[benchmark_ticker] / close_wide[benchmark_ticker].shift(period) - 1
    return (stock_ret - bench_ret).dropna()


def golden_cross_tickers(
    tickers,
    close_wide,
    short_period=GOLDEN_CROSS_SHORT,
    long_period=GOLDEN_CROSS_LONG,
    lookback=GOLDEN_CROSS_LOOKBACK,
):
    if len(close_wide) < long_period + lookback:
        raise ValueError(
            f"データが{long_period + lookback}営業日分蓄積されていません（現在{len(close_wide)}日分）。"
            "backfill_history.pyの実行を確認してください。"
        )

    available = [t for t in tickers if t in close_wide.columns]
    close = close_wide[available]

    short_ema = close.ewm(span=short_period, adjust=False).mean()
    long_ema = close.ewm(span=long_period, adjust=False).mean()
    diff = short_ema - long_ema

    crossed = (diff.shift(1) <= 0) & (diff > 0)
    recent_cross = crossed.tail(lookback).any()

    cross_dates = {}
    for ticker in recent_cross[recent_cross].index:
        cross_series = crossed[ticker]
        cross_dates[ticker] = cross_series[cross_series].index[-1]

    return cross_dates


def relative_strength_wide(close_wide, benchmark_ticker=BENCHMARK_TICKER, period=RS_PERIOD):
    # 全銘柄・全日付の相対強度（index=date, columns=ticker）
    ret = close_wide / close_wide.shift(period) - 1
    return ret.sub(ret[benchmark_ticker], axis=0)


def above_ema_wide(close_wide, span):
    # 終値がEMAより上なら1、下なら0（終値が欠損の日はNaN）
    ema = close_wide.ewm(span=span, adjust=False).mean()
    return (close_wide > ema).astype(float).where(close_wide.notna())


def sector_breadth(flags_wide, sector_of):
    # 条件を満たす銘柄の割合を業種・日付ごとに集計する（NaN=判定不可の銘柄は母数から除く）
    # flags_wide: index=date, columns=ticker の1/0/NaN、sector_of: ticker→業種のSeries
    sectors = sector_of.reindex(flags_wide.columns)
    hit = flags_wide.T.groupby(sectors).sum().T
    count = flags_wide.notna().T.groupby(sectors).sum().T
    hit["全体"] = hit.sum(axis=1)
    count["全体"] = count.sum(axis=1)
    return hit / count, count
