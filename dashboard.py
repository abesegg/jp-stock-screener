import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from filters import BENCHMARK_TICKER, GOLDEN_CROSS_SHORT, RS_PERIOD, load_close_wide, relative_strength_series

st.set_page_config(page_title="日本株 スクリーニング", page_icon="📈", layout="wide")

RS_RANKING_FILE = "rs_ranking.csv"
GOLDEN_CROSS_FILE = "golden_cross.csv"
OHLCV_FILE = "daily_ohlcv.csv"


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


def render_chart(ticker, ohlcv, close_wide, key_prefix):
    ticker_data = ohlcv[ohlcv["ticker"] == ticker].sort_values("date")

    if ticker_data.empty:
        st.warning("価格データがありません")
        return

    rs_series = relative_strength_series(ticker, close_wide, BENCHMARK_TICKER, RS_PERIOD)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.03,
        subplot_titles=("", f"対TOPIX相対強度（{RS_PERIOD}営業日騰落率差, %）"),
    )
    fig.add_trace(go.Candlestick(
        x=ticker_data["date"],
        open=ticker_data["open"],
        high=ticker_data["high"],
        low=ticker_data["low"],
        close=ticker_data["close"],
        name="株価",
        increasing=dict(line_color="#3D9970", fillcolor="#3D9970"),
        decreasing=dict(line_color="#FF4136", fillcolor="#FF4136"),
    ), row=1, col=1)

    ema25 = ticker_data["close"].ewm(span=25, adjust=False).mean()
    fig.add_trace(go.Scatter(
        x=ticker_data["date"], y=ema25, mode="lines", name="25EMA",
        line=dict(color="#FF9800", width=1.5),
    ), row=1, col=1)

    ema_short = ticker_data["close"].ewm(span=GOLDEN_CROSS_SHORT, adjust=False).mean()
    fig.add_trace(go.Scatter(
        x=ticker_data["date"], y=ema_short, mode="lines", name=f"{GOLDEN_CROSS_SHORT}EMA",
        line=dict(color="#FFD700", width=1.5),
    ), row=1, col=1)

    rolling_std = ticker_data["close"].rolling(25).std()
    for multiplier in [1, 2]:
        for sign, label in [(1, f"+{multiplier}σ"), (-1, f"-{multiplier}σ")]:
            fig.add_trace(go.Scatter(
                x=ticker_data["date"],
                y=ema25 + sign * multiplier * rolling_std,
                mode="lines",
                name=label,
                line=dict(color="#FF9800", width=1),
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

    fig.update_layout(
        height=750,
        xaxis_rangeslider_visible=False,
        margin=dict(l=20, r=20, t=30, b=20),
        showlegend=False,
    )
    st.plotly_chart(fig, width="stretch", key=f"chart_{key_prefix}")


def render_screening_tab(display_df, key_prefix, ohlcv, close_wide):
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

    with col_chart:
        st.subheader("日足チャート")
        render_chart(selected_ticker, ohlcv, close_wide, key_prefix)


st.title("📈 日本株 スクリーニング")

ohlcv = load_ohlcv()
close_wide = load_close_wide_cached()

tab_rs, tab_golden = st.tabs(["RS上位", "ゴールデンクロス"])

with tab_rs:
    ranking = load_ranking()
    st.caption(f"流動性フィルタ通過後、TOPIX(1306.T)に対する相対強度(21営業日)の上位{len(ranking)}銘柄")
    display_df = ranking.copy()
    display_df["RS(%)"] = (display_df["relative_strength"] * 100).round(2)
    display_df = display_df[["ticker", "銘柄名", "33業種区分", "RS(%)"]].rename(columns={"33業種区分": "業種"})
    render_screening_tab(display_df, "rs", ohlcv, close_wide)

with tab_golden:
    golden = load_golden_cross()
    st.caption(f"流動性フィルタ通過銘柄のうち、{GOLDEN_CROSS_SHORT}EMAが25EMAを直近3営業日以内に下から上に抜けた{len(golden)}銘柄")
    if golden.empty:
        display_df = golden
    else:
        display_df = golden[["ticker", "銘柄名", "33業種区分", "cross_date"]].rename(
            columns={"33業種区分": "業種", "cross_date": "クロス日"}
        )
    render_screening_tab(display_df, "golden", ohlcv, close_wide)

st.divider()
st.caption(
    "⚠️ 本ダッシュボードは情報提供目的のみです。投資判断はご自身の責任で行ってください。"
)
