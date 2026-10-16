import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from filters import (
    BENCHMARK_TICKER, GOLDEN_CROSS_SHORT, RS_PERIOD,
    above_ema_wide, liquidity_passed_tickers, load_close_wide,
    relative_strength_series, relative_strength_wide, sector_breadth,
)

st.set_page_config(page_title="日本株 スクリーニング", page_icon="📈", layout="wide")

RS_RANKING_FILE = "rs_ranking.csv"
GOLDEN_CROSS_FILE = "golden_cross.csv"
OHLCV_FILE = "daily_ohlcv.csv"
UNIVERSE_FILE = "universe.csv"

BREADTH_METRICS = {
    f"RSがプラス（対TOPIX {RS_PERIOD}営業日）": "rs",
    "25EMAより上": 25,
    "75EMAより上": 75,
}
SMALL_SECTOR = 10  # 銘柄数がこれ未満の業種は割合が振れやすいため※を付けて表示


@st.cache_data(ttl=600)
def load_ranking():
    return pd.read_csv(RS_RANKING_FILE)


@st.cache_data(ttl=600)
def load_golden_cross():
    return pd.read_csv(GOLDEN_CROSS_FILE)


@st.cache_data(ttl=600)
def load_ohlcv():
    return pd.read_csv(OHLCV_FILE, parse_dates=["date"])


@st.cache_data(ttl=600)
def load_close_wide_cached():
    return load_close_wide(OHLCV_FILE)


@st.cache_data(ttl=600)
def load_sector_breadth(metric):
    # 流動性フィルタ通過銘柄（RSランキングと同じ母集団）で業種別の割合を計算する
    close_wide = load_close_wide_cached()
    tickers = [t for t in liquidity_passed_tickers() if t in close_wide.columns]
    if metric == "rs":
        rs = relative_strength_wide(close_wide)[tickers]
        flags = (rs > 0).astype(float).where(rs.notna())
    else:
        flags = above_ema_wide(close_wide[tickers], metric)
    sector_of = pd.read_csv(UNIVERSE_FILE).set_index("ticker")["33業種区分"]
    return sector_breadth(flags, sector_of)


def data_as_of(ohlcv):
    # スクリーニングの基準日（RS計算と揃えるためベンチマークの最新日を採用）
    return ohlcv.loc[ohlcv["ticker"] == BENCHMARK_TICKER, "date"].max()


def render_chart(ticker, ohlcv, close_wide, key_prefix, display_days=None):
    full_data = ohlcv[ohlcv["ticker"] == ticker].sort_values("date")

    if full_data.empty:
        st.warning("価格データがありません")
        return

    # 指標は全期間データで計算してから表示範囲を絞り込む（EMA等のウォームアップを保つため）
    ema25 = full_data["close"].ewm(span=25, adjust=False).mean()
    ema_short = full_data["close"].ewm(span=GOLDEN_CROSS_SHORT, adjust=False).mean()
    ema75 = full_data["close"].ewm(span=75, adjust=False).mean()
    rolling_std = full_data["close"].rolling(25).std()
    rs_series = relative_strength_series(ticker, close_wide, BENCHMARK_TICKER, RS_PERIOD)

    if display_days is not None:
        cutoff = full_data["date"].max() - pd.Timedelta(days=display_days)
        mask = full_data["date"] >= cutoff
        ticker_data = full_data[mask]
        ema25 = ema25[mask]
        ema_short = ema_short[mask]
        ema75 = ema75[mask]
        rolling_std = rolling_std[mask]
        rs_series = rs_series[rs_series.index >= cutoff]
    else:
        ticker_data = full_data

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.8, 0.2], vertical_spacing=0.03,
        subplot_titles=("", f"対TOPIX相対強度（{RS_PERIOD}営業日騰落率差, %）"),
    )
    fig.add_trace(go.Candlestick(
        x=ticker_data["date"],
        open=ticker_data["open"],
        high=ticker_data["high"],
        low=ticker_data["low"],
        close=ticker_data["close"],
        name="株価",
        increasing=dict(line=dict(color="#66BB6A", width=1.2), fillcolor="#66BB6A"),
        decreasing=dict(line=dict(color="#EF5350", width=1.2), fillcolor="#EF5350"),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=ticker_data["date"], y=ema25, mode="lines", name="25EMA",
        line=dict(color="#2196F3", width=1.5),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=ticker_data["date"], y=ema_short, mode="lines", name=f"{GOLDEN_CROSS_SHORT}EMA",
        line=dict(color="#EB6101", width=1.5),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=ticker_data["date"], y=ema75, mode="lines", name="75EMA",
        line=dict(color="#C0CA33", width=1.5),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=ticker_data["date"], y=ema25 + 2 * rolling_std, mode="lines", name="+2σ",
        line=dict(color="#64B5F6", width=0.5), showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=ticker_data["date"], y=ema25 - 2 * rolling_std, mode="lines", name="-2σ",
        line=dict(color="#64B5F6", width=0.5),
        fill="tonexty", fillcolor="rgba(100, 181, 246, 0.15)",
        showlegend=False,
    ), row=1, col=1)
    for sign, label in [(1, "+1σ"), (-1, "-1σ")]:
        fig.add_trace(go.Scatter(
            x=ticker_data["date"],
            y=ema25 + sign * rolling_std,
            mode="lines",
            name=label,
            line=dict(color="#64B5F6", width=0.5),
            showlegend=False,
        ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=rs_series.index,
        y=rs_series.values * 100,
        mode="lines",
        name="RS(%)",
        line=dict(color="#1f77b4"),
    ), row=2, col=1)
    fig.add_hline(y=0, line_dash="dot", line_color="gray", row=2, col=1)

    # 休場日（土日・祝日）や売買停止日を詰めて表示するため、データのない日をx軸から除外する
    all_days = pd.date_range(ticker_data["date"].min(), ticker_data["date"].max())
    missing_days = all_days.difference(ticker_data["date"])
    fig.update_xaxes(rangebreaks=[dict(values=missing_days.strftime("%Y-%m-%d").tolist())])

    fig.update_layout(
        height=600,
        boxgap=0.4,  # ローソク足の実体幅（日付間隔に対する隙間の割合。既定0.3）
        xaxis_rangeslider_visible=False,
        margin=dict(l=20, r=20, t=30, b=20),
        showlegend=False,
    )
    st.plotly_chart(fig, width="stretch", key=f"chart_{key_prefix}")


def to_tradingview_watchlist(tickers):
    # TradingViewのウォッチリストにインポートできる形式（"TSE:7203,TSE:6758,..."）
    return ",".join(f"TSE:{t.removesuffix('.T')}" for t in tickers)


def render_screening_tab(display_df, key_prefix, ohlcv, close_wide, display_days, download_name):
    if display_df.empty:
        st.info("該当する銘柄はありません")
        return

    col_list, col_chart = st.columns([1, 2])

    with col_list:
        st.subheader("リスト")
        event = st.dataframe(
            display_df,
            width="stretch",
            height=600,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=f"table_{key_prefix}",
        )
        selected_rows = event.selection.rows if event and event.selection else []
        selected_idx = selected_rows[0] if selected_rows else 0
        selected_ticker = display_df.iloc[selected_idx]["ticker"]

        st.download_button(
            "TradingView用リストをダウンロード",
            data=to_tradingview_watchlist(display_df["ticker"]),
            file_name=download_name,
            mime="text/plain",
            on_click="ignore",  # ダウンロード時にアプリを再実行しない（選択中の銘柄などを保つ）
            key=f"download_{key_prefix}",
        )

    with col_chart:
        st.subheader("日足チャート")
        render_chart(selected_ticker, ohlcv, close_wide, key_prefix, display_days)


def render_breadth_tab(display_days):
    metric_label = st.radio("指標", list(BREADTH_METRICS), horizontal=True)
    ratio, count = load_sector_breadth(BREADTH_METRICS[metric_label])
    latest = ratio.index[-1]
    overall = ratio.loc[latest, "全体"] * 100

    latest_df = pd.DataFrame({"割合": ratio.loc[latest] * 100, "銘柄数": count.loc[latest]})
    latest_df = latest_df.drop("全体").sort_values("割合")

    # 割合が50%を超えた業種の数（判定できる業種がない日はNaN）
    sector_ratio = ratio.drop(columns="全体")
    n_sectors = sector_ratio.notna().sum(axis=1)
    above_half = (sector_ratio > 0.5).sum(axis=1)
    above_share = (above_half / n_sectors.where(n_sectors > 0)) * 100

    st.metric(
        "割合50%超の業種",
        f"{above_half.iloc[-1]} / {n_sectors.iloc[-1]}業種（{above_share.iloc[-1]:.0f}%）",
        delta=f"{above_half.iloc[-1] - above_half.iloc[-2]:+d}業種（前日比）",
    )

    col_bar, col_line = st.columns([1, 1])

    with col_bar:
        st.subheader(f"業種別（{latest:%Y-%m-%d}）")
        fig = go.Figure(go.Bar(
            x=latest_df["割合"], y=latest_df.index, orientation="h",
            marker_color="#2196F3",
            text=[
                f"{r:.0f}%（<span style='color:#EF6C00'><b>{n}※</b></span>）" if n < SMALL_SECTOR else f"{r:.0f}%（{n}）"
                for r, n in zip(latest_df["割合"], latest_df["銘柄数"])
            ],
            textposition="outside",
            cliponaxis=False,  # 100%付近のラベルが軸の範囲外にはみ出しても切り取らない
            hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
        ))
        fig.add_vline(x=overall, line_dash="dot", line_color="gray",
                      annotation_text=f"全体 {overall:.1f}%", annotation_position="top")
        fig.update_layout(
            height=800, xaxis=dict(range=[0, 110], ticksuffix="%"),
            margin=dict(l=20, r=70, t=30, b=20),  # 右側にラベル用の余白を確保
        )
        st.plotly_chart(fig, width="stretch", key="breadth_bar")
        st.caption(f"（）内は銘柄数。※は銘柄数{SMALL_SECTOR}未満の業種（割合が振れやすい）")

    with col_line:
        st.subheader("推移")
        # 指標を切り替えると選択肢の並び順が変わり選択がリセットされるため、選択中の業種を保持して引き継ぐ
        # keyを指定するとウィジェットの同一性がkeyだけで決まり、並び順が変わっても選択が保持される
        # （key無しでindexを毎回渡すと、indexの変化でIDが変わり、直後の選択が1回おきに無視されていた）
        options = ["全体"] + latest_df.index[::-1].tolist()
        if st.session_state.get("breadth_sector") not in options:
            st.session_state["breadth_sector"] = "全体"
        sector = st.selectbox("業種", options, key="breadth_sector")
        series = ratio[sector] * 100
        overall_series = ratio["全体"] * 100
        if display_days is not None:
            cutoff = series.index.max() - pd.Timedelta(days=display_days)
            series = series[series.index >= cutoff]
            overall_series = overall_series[overall_series.index >= cutoff]
            above_share = above_share[above_share.index >= cutoff]

        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.7, 0.3], vertical_spacing=0.08,
            subplot_titles=("", "割合50%超の業種（全業種に対する割合, %）"),
        )
        if sector != "全体":
            fig.add_trace(go.Scatter(
                x=overall_series.index, y=overall_series, mode="lines", name="全体",
                line=dict(color="gray", width=1, dash="dot"),
            ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=series.index, y=series, mode="lines", name=sector,
            line=dict(color="#2196F3", width=1.5),
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=above_share.index, y=above_share, mode="lines", name="50%超の業種",
            line=dict(color="#1f77b4", width=1.5), showlegend=False,
        ), row=2, col=1)
        for row in (1, 2):
            fig.add_hline(y=50, line_dash="dash", line_color="#BDBDBD", line_width=1, row=row, col=1)
            fig.update_yaxes(range=[0, 100], ticksuffix="%", row=row, col=1)
        fig.update_layout(
            height=550,
            margin=dict(l=20, r=20, t=30, b=20),
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig, width="stretch", key="breadth_line")
        st.caption("75EMAはデータの先頭数か月が助走期間のため、「全期間」表示の序盤は参考値")


st.title("📈 日本株 スクリーニング")

with st.sidebar:
    st.subheader("表示設定")
    period_map = {"1ヶ月": 30, "3ヶ月": 90, "6ヶ月": 180, "1年": 365, "全期間": None}
    period_label = st.selectbox("チャート表示期間", list(period_map.keys()), index=2)
    display_days = period_map[period_label]

ohlcv = load_ohlcv()
close_wide = load_close_wide_cached()

as_of = data_as_of(ohlcv)
st.caption(f"データ基準日: {as_of:%Y-%m-%d}（{'月火水木金土日'[as_of.weekday()]}）の終値")

tab_rs, tab_golden, tab_breadth = st.tabs(["RS上位", "ゴールデンクロス", "業種別"])

with tab_rs:
    ranking = load_ranking()
    st.caption(f"流動性フィルタ通過後、TOPIX(1306.T)に対する相対強度(21営業日)の上位{len(ranking)}銘柄")
    display_df = ranking.copy()
    display_df["RS(%)"] = (display_df["relative_strength"] * 100).round(2)
    display_df = display_df[["33業種区分", "銘柄名", "ticker", "RS(%)"]].rename(columns={"33業種区分": "業種"})
    render_screening_tab(display_df, "rs", ohlcv, close_wide, display_days, f"rs_top_{as_of:%Y%m%d}.txt")

with tab_golden:
    golden = load_golden_cross()
    st.caption(f"流動性フィルタ通過銘柄のうち、{GOLDEN_CROSS_SHORT}EMAが25EMAを直近3営業日以内に下から上に抜けた{len(golden)}銘柄")
    if golden.empty:
        display_df = golden
    else:
        display_df = golden[["33業種区分", "銘柄名", "ticker", "cross_date"]].rename(
            columns={"33業種区分": "業種", "cross_date": "クロス日"}
        )
    render_screening_tab(display_df, "golden", ohlcv, close_wide, display_days, f"golden_cross_{as_of:%Y%m%d}.txt")

with tab_breadth:
    st.caption("流動性フィルタ通過銘柄のうち、各条件を満たす銘柄の割合（業種別）")
    render_breadth_tab(display_days)

st.divider()
st.caption(
    "⚠️ 本ダッシュボードは情報提供目的のみです。投資判断はご自身の責任で行ってください。"
)
