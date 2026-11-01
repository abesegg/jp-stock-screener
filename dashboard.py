import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from filters import (
    BENCHMARK_TICKER, EMA_WARMUP_DAYS, GOLDEN_CROSS_SHORT, PO_FRESH_DAYS, PO_LOOKBACK, RS_PERIOD, RS_TOP_PCT, WATCH_DAYS,
    above_ema_wide, fresh_perfect_order_tickers, fresh_perfect_order_wide, liquidity_passed_tickers, load_close_wide,
    relative_strength, relative_strength_series, relative_strength_wide, sector_breadth, watch_list,
)
from notify import WATCH_MIN_ELAPSED, WATCH_MIN_SHARE

st.set_page_config(page_title="日本株 スクリーニング", page_icon="📈", layout="wide")

RS_RANKING_FILE = "rs_ranking.csv"
GOLDEN_CROSS_FILE = "golden_cross.csv"
OHLCV_FILE = "daily_ohlcv.csv"
UNIVERSE_FILE = "universe.csv"
SCREENING_RESULT_FILE = "screening_result.csv"
JPX_LIST_FILE = "data_j.xlsx"
SIZE_RANK = {"TOPIX Core30": 1, "TOPIX Large70": 2, "TOPIX Mid400": 3, "TOPIX Small 1": 4, "TOPIX Small 2": 5}

BREADTH_METRICS = {
    f"RSがプラス（対TOPIX {RS_PERIOD}営業日）": "rs",
    "25EMAより上": 25,
    "75EMAより上": 75,
}
SMALL_SECTOR = 10  # 銘柄数がこれ未満の業種は割合が振れやすいため※を付けて表示
CANDIDATE_COUNT = 9  # 業種内候補タブに並べる銘柄数（3列×3行）
HIGHLIGHT_STYLE = "color: #1565C0; font-weight: bold"  # リストで強調する行の文字色
RS_THRESHOLD_COLOR = "#9E9E9E"  # RSチャートのRS上位の境目の線
PO_MARK_COLOR = "#FF9800"  # チャートのPO成立日の線と監視期間の塗り
ENTRY_MARK_COLOR = "#7E57C2"  # チャートのRS上位入り日の線
GC_MARK_COLOR = "#00E676"  # チャートのゴールデンクロスの印（ローソク足に埋もれないよう明るめ）
DC_MARK_COLOR = "#FF4081"  # チャートのデッドクロスの印（陰線の赤と区別するためピンク寄り）


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


@st.cache_data(ttl=600)
def load_sector_leaders():
    # 業種ごとの代表銘柄: 流動性フィルタ通過銘柄のうち、JPXの規模区分が最上位の銘柄の中で10日中央値売買代金が最大の銘柄
    # （業種別指数は時価総額加重のため、大型株を選ぶと業種全体の値動きに近くなる。規模区分のない新規上場・新コードは最下位扱い）
    df = pd.read_csv(SCREENING_RESULT_FILE)
    df = df[df["除外"] == False]
    jpx = pd.read_excel(JPX_LIST_FILE, dtype={"コード": str})
    size = jpx.assign(ticker=jpx["コード"] + ".T").set_index("ticker")["規模区分"]
    df["規模区分"] = df["ticker"].map(size)
    df["規模順"] = df["規模区分"].map(SIZE_RANK).fillna(len(SIZE_RANK) + 1)
    df = df.sort_values(["規模順", "10日中央値売買代金"], ascending=[True, False])
    return df.groupby("33業種区分").head(1).set_index("33業種区分")[["ticker", "銘柄名", "規模区分"]]


@st.cache_data(ttl=600)
def load_latest_rs():
    # 流動性フィルタ通過銘柄の最新RS（RSの高い順。データ不足で計算できない銘柄は含まない）
    return relative_strength(liquidity_passed_tickers(), load_close_wide_cached())


@st.cache_data(ttl=600)
def load_rs_top():
    # 流動性フィルタ通過銘柄の日ごとのRS上位RS_TOP_PCTの判定（watch_list()・analysis/と同じ定義。RSが計算できない日は上位に数えない）と、
    # その日の境目（上位に入った銘柄のRSの最小値。RSが境目以上なら上位）
    close_wide = load_close_wide_cached()
    tickers = [t for t in liquidity_passed_tickers() if t in close_wide.columns]
    rs = relative_strength_wide(close_wide)[tickers]
    top = rs.rank(axis=1, pct=True) > 1 - RS_TOP_PCT
    return top, rs.where(top).min(axis=1)


@st.cache_data(ttl=600)
def load_rs_entry():
    # 最新日にRS上位にいる銘柄の、上位に初めて入った日（直前PO_FRESH_DAYS営業日に上位にいなかった日。analysis/rs_entry_study.pyと同じ）と、
    # その日からの経過営業日数（入った日が1日目。検証の「n日目」はn+1日目に当たる。途中で上位を出てもリセットしない）・上位にいた日数
    top, _ = load_rs_top()
    recent = top.shift(1, fill_value=False).rolling(PO_FRESH_DAYS, min_periods=1).max().astype(bool)
    first = top & ~recent
    rows = []
    for t in top.columns[top.iloc[-1]]:
        entry = first.index[first[t]][-1]
        after = top[t][top.index >= entry]
        rows.append({"ticker": t, "entry_date": entry.strftime("%Y-%m-%d"),
                     "entry_elapsed": len(after), "entry_top_days": int(after.sum())})
    return pd.DataFrame(rows, columns=["ticker", "entry_date", "entry_elapsed", "entry_top_days"])


@st.cache_data(ttl=600)
def load_last_fresh_po():
    # 流動性フィルタ通過銘柄の最後のPO新規の日（振り返り用のためRSの条件は付けない。助走期間中などで新規成立がない銘柄は含まない）
    close_wide = load_close_wide_cached()
    tickers = [t for t in liquidity_passed_tickers() if t in close_wide.columns]
    fresh = fresh_perfect_order_wide(close_wide[tickers])
    return fresh.apply(lambda s: s.index[s][-1] if s.any() else pd.NaT).dropna().dt.strftime("%Y-%m-%d")


@st.cache_data(ttl=600)
def load_perfect_order():
    # 直近PO_LOOKBACK営業日以内にパーフェクトオーダーが新規成立し、成立日のRSがプラスの銘柄（成立日の新しい順、同日はRSの高い順）
    signals = fresh_perfect_order_tickers(liquidity_passed_tickers(), load_close_wide_cached())
    if not signals:
        return pd.DataFrame(columns=["33業種区分", "銘柄名", "ticker", "po_date", "rs"])
    df = pd.DataFrame({"ticker": list(signals), "po_date": [d.strftime("%Y-%m-%d") for d in signals.values()]})
    df["rs"] = df["ticker"].map(load_latest_rs())
    universe = pd.read_csv(UNIVERSE_FILE)[["ticker", "銘柄名", "33業種区分"]]
    df = df.merge(universe, on="ticker", how="left")
    return df.sort_values(["po_date", "rs"], ascending=[False, False])


@st.cache_data(ttl=600)
def load_watch_list():
    # PO新規+RSプラスから1〜WATCH_DAYS営業日目の銘柄と、RS上位にいた日数・割合（経過日数の長い順、同日は割合の高い順）
    df = watch_list(liquidity_passed_tickers(), load_close_wide_cached())
    df["po_date"] = df["po_date"].dt.strftime("%Y-%m-%d")
    df["rs"] = df["ticker"].map(load_latest_rs())
    universe = pd.read_csv(UNIVERSE_FILE)[["ticker", "銘柄名", "33業種区分"]]
    df = df.merge(universe, on="ticker", how="left")
    return df.sort_values(["elapsed", "rs_top_share"], ascending=[False, False])


def data_as_of(ohlcv):
    # スクリーニングの基準日（RS計算と揃えるためベンチマークの最新日を採用）
    return ohlcv.loc[ohlcv["ticker"] == BENCHMARK_TICKER, "date"].max()


RIBBON_SLOW = 12  # リボンの遅い側のEMA（速い側は5EMA）。TradingViewの表示に合わせる
RIBBON_UP_COLOR = "rgba(41, 182, 246, 0.30)"  # 5EMA > 12EMA（水色）
RIBBON_DOWN_COLOR = "rgba(255, 138, 101, 0.30)"  # 5EMA < 12EMA（ピンク寄りのオレンジ）


def add_ema_ribbon(fig, dates, fast, slow):
    # 速いEMAと遅いEMAの間を、上下関係に応じて2色で塗る
    # 色ごとに1本のトレースにまとめ、区間をNoneで区切ってfill="toself"で区間ごとに塗る（区間ごとにトレースを作ると数が増え重くなるため）
    dates, fast, slow = list(dates), list(fast), list(slow)
    up = [f >= s for f, s in zip(fast, slow)]
    for is_up, color in [(True, RIBBON_UP_COLOR), (False, RIBBON_DOWN_COLOR)]:
        xs, ys = [], []
        i = 0
        while i < len(up):
            if up[i] != is_up:
                i += 1
                continue
            j = i
            while j + 1 < len(up) and up[j + 1] == is_up:
                j += 1
            end = min(j + 1, len(up) - 1)  # 次の区間の始まりまで伸ばして、色の切り替わりで隙間ができないようにする
            xs += dates[i:end + 1] + dates[i:end + 1][::-1] + [None]
            ys += fast[i:end + 1] + slow[i:end + 1][::-1] + [None]
            i = j + 1
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", line=dict(width=0),
            fill="toself", fillcolor=color, hoverinfo="skip", showlegend=False,
        ), row=1, col=1)


def render_chart(ticker, ohlcv, close_wide, key_prefix, display_days=None, show_rs=True, height=600, title=None,
                 po_date=None, shade_watch=False, entry_date=None):
    full_data = ohlcv[ohlcv["ticker"] == ticker].sort_values("date")

    if full_data.empty:
        st.warning("価格データがありません")
        return

    # 指標は全期間データで計算してから表示範囲を絞り込む（EMA等のウォームアップを保つため）
    ema25 = full_data["close"].ewm(span=25, adjust=False).mean()
    ema_short = full_data["close"].ewm(span=GOLDEN_CROSS_SHORT, adjust=False).mean()
    ema12 = full_data["close"].ewm(span=RIBBON_SLOW, adjust=False).mean()
    ema75 = full_data["close"].ewm(span=75, adjust=False).mean()
    ema200 = full_data["close"].ewm(span=200, adjust=False).mean()
    rolling_std = full_data["close"].rolling(25).std()
    # ゴールデンクロス・デッドクロス（5EMAが25EMAを下から上・上から下に抜けた日。golden_cross_tickers()と同じ判定）。助走期間は除く
    ema_gap = ema_short - ema25
    golden_cross = (ema_gap.shift(1) <= 0) & (ema_gap > 0)
    dead_cross = (ema_gap.shift(1) >= 0) & (ema_gap < 0)
    golden_cross.iloc[:EMA_WARMUP_DAYS] = False
    dead_cross.iloc[:EMA_WARMUP_DAYS] = False
    if show_rs:
        rs_series = relative_strength_series(ticker, close_wide, BENCHMARK_TICKER, RS_PERIOD)
        _, rs_threshold = load_rs_top()

    if display_days is not None:
        cutoff = full_data["date"].max() - pd.Timedelta(days=display_days)
        mask = full_data["date"] >= cutoff
        ticker_data = full_data[mask]
        ema25 = ema25[mask]
        ema_short = ema_short[mask]
        ema12 = ema12[mask]
        ema75 = ema75[mask]
        ema200 = ema200[mask]
        rolling_std = rolling_std[mask]
        golden_cross = golden_cross[mask]
        dead_cross = dead_cross[mask]
        if show_rs:
            rs_series = rs_series[rs_series.index >= cutoff]
            rs_threshold = rs_threshold[rs_threshold.index >= cutoff]
    else:
        ticker_data = full_data

    if show_rs:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.8, 0.2], vertical_spacing=0.03,
            subplot_titles=("", f"対TOPIX相対強度（{RS_PERIOD}営業日騰落率差, %）"),
        )
    else:
        fig = make_subplots(rows=1, cols=1)

    # 5EMAと12EMAのリボン（ローソク足の下に塗りが来るよう先に描く。5EMA・12EMAの線自体は描かない）
    add_ema_ribbon(fig, ticker_data["date"], ema_short, ema12)

    fig.add_trace(go.Candlestick(
        x=ticker_data["date"],
        open=ticker_data["open"],
        high=ticker_data["high"],
        low=ticker_data["low"],
        close=ticker_data["close"],
        name="株価",
        # 線の色はTradingViewの表示に合わせている（陽線・75EMAは青緑系、陰線・25EMAは赤系、200EMAは青）
        increasing=dict(line=dict(color="#26C6DA", width=1.2), fillcolor="#26C6DA"),
        decreasing=dict(line=dict(color="#F23645", width=1.2), fillcolor="#F23645"),
    ), row=1, col=1)

    for ema, span, color in [(ema25, 25, "#E53935"), (ema75, 75, "#00ACC1"), (ema200, 200, "#2962FF")]:
        fig.add_trace(go.Scatter(
            x=ticker_data["date"], y=ema, mode="lines", name=f"{span}EMA",
            line=dict(color=color, width=1.5),
        ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=ticker_data["date"], y=ema25 + 2 * rolling_std, mode="lines", name="+2σ",
        line=dict(color="#64B5F6", width=0.5), showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=ticker_data["date"], y=ema25 - 2 * rolling_std, mode="lines", name="-2σ",
        line=dict(color="#64B5F6", width=0.5),
        fill="tonexty", fillcolor="rgba(100, 181, 246, 0.07)",
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

    # ゴールデンクロスはローソク足の下に▲、デッドクロスは上に▼で示す
    for cross, price, offset, symbol, color, label in [
        (golden_cross, "low", 0.97, "triangle-up", GC_MARK_COLOR, "GC"),
        (dead_cross, "high", 1.03, "triangle-down", DC_MARK_COLOR, "DC"),
    ]:
        cross_days = ticker_data[cross]
        fig.add_trace(go.Scatter(
            x=cross_days["date"], y=cross_days[price] * offset, mode="markers", name=label,
            marker=dict(symbol=symbol, size=8, color=color),
            hovertemplate=f"{label} %{{x|%m/%d}}<extra></extra>",
        ), row=1, col=1)

    if show_rs:
        # RS上位の境目（RSの線が上にあればその日は上位）。RSの線の下に来るよう先に描く
        fig.add_trace(go.Scatter(
            x=rs_threshold.index,
            y=rs_threshold.values * 100,
            mode="lines",
            name=f"RS上位{RS_TOP_PCT:.0%}の境目",
            line=dict(color=RS_THRESHOLD_COLOR, width=1, dash="dash"),
        ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=rs_series.index,
            y=rs_series.values * 100,
            mode="lines",
            name="RS(%)",
            line=dict(color="#1f77b4"),
        ), row=2, col=1)
        fig.add_hline(y=0, line_dash="dot", line_color="gray", row=2, col=1)

    if po_date is not None:
        # PO成立日の縦線と、監視中なら成立日から最新日までの薄い塗り（成立日が表示範囲より前なら縦線は省き、塗りは表示範囲の先頭から）
        po_date = pd.Timestamp(po_date)
        first_day, last_day = ticker_data["date"].min(), ticker_data["date"].max()
        for row in ((1, 2) if show_rs else (1,)):
            if po_date >= first_day:
                fig.add_vline(x=po_date, line_dash="dot", line_color=PO_MARK_COLOR, line_width=1.5, row=row, col=1)
            if shade_watch:
                fig.add_vrect(x0=max(po_date, first_day), x1=last_day, fillcolor=PO_MARK_COLOR, opacity=0.08,
                              line_width=0, row=row, col=1)
    if entry_date is not None and pd.Timestamp(entry_date) >= ticker_data["date"].min():
        # RS上位に初めて入った日の縦線
        for row in ((1, 2) if show_rs else (1,)):
            fig.add_vline(x=pd.Timestamp(entry_date), line_dash="dot", line_color=ENTRY_MARK_COLOR, line_width=1.5,
                          row=row, col=1)

    # 休場日（土日・祝日）や売買停止日を詰めて表示するため、データのない日をx軸から除外する
    all_days = pd.date_range(ticker_data["date"].min(), ticker_data["date"].max())
    missing_days = all_days.difference(ticker_data["date"])
    fig.update_xaxes(rangebreaks=[dict(values=missing_days.strftime("%Y-%m-%d").tolist())])

    fig.update_layout(
        height=height,
        boxgap=0.4,  # ローソク足の実体幅（日付間隔に対する隙間の割合。既定0.3）
        xaxis_rangeslider_visible=False,
        margin=dict(l=20, r=20, t=60 if title else 30, b=20),
        showlegend=False,
    )
    if title:
        fig.update_layout(title=dict(text=title, font=dict(size=12)))
    st.plotly_chart(fig, width="stretch", key=f"chart_{key_prefix}")


def to_tradingview_watchlist(tickers):
    # TradingViewのウォッチリストにインポートできる形式（"TSE:7203,TSE:6758,..."）
    return ",".join(f"TSE:{t.removesuffix('.T')}" for t in tickers)


def render_screening_tab(display_df, key_prefix, ohlcv, close_wide, display_days, download_name, highlight=None,
                         po_date_col=None, shade_watch=False, entry_date_col=None):
    if display_df.empty:
        st.info("該当する銘柄はありません")
        return

    col_list, col_chart = st.columns([1, 2])

    with col_list:
        st.subheader("リスト")
        # highlight（display_dfと同じindexのbool）がTrueの行を強調する。Stylerは小数を6桁で表示するため桁を指定する
        table = display_df
        if highlight is not None:
            table = display_df.style.apply(
                lambda row: [HIGHLIGHT_STYLE if highlight[row.name] else ""] * len(row), axis=1
            ).format(precision=2, na_rep="")
        event = st.dataframe(
            table,
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
        # 日付の列が空欄（NaN）の銘柄は印を付けない
        selected = display_df.iloc[selected_idx]
        po_date = selected[po_date_col] if po_date_col and pd.notna(selected[po_date_col]) else None
        entry_date = selected[entry_date_col] if entry_date_col and pd.notna(selected[entry_date_col]) else None
        render_chart(selected_ticker, ohlcv, close_wide, key_prefix, display_days,
                     po_date=po_date, shade_watch=shade_watch, entry_date=entry_date)


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
        sector = st.selectbox("業種", options, key="breadth_sector", filter_mode=None)
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


def render_sector_overview_tab(ohlcv, close_wide, display_days):
    leaders = load_sector_leaders()
    ratio, _ = load_sector_breadth("rs")
    latest = ratio.iloc[-1].drop("全体")
    sectors = [s for s in latest.sort_values(ascending=False).index if s in leaders.index]

    # 3列ずつ行を分けて並べる（行ごとにst.columnsを作り、チャートの高さを行内で揃える）
    for row_start in range(0, len(sectors), 3):
        cols = st.columns(3)
        for col, sector in zip(cols, sectors[row_start:row_start + 3]):
            leader = leaders.loc[sector]
            with col:
                render_chart(
                    leader["ticker"], ohlcv, close_wide, f"overview_{sector}", display_days,
                    show_rs=False, height=280,
                    title=f"<b>{sector}</b>（RSプラス {latest[sector] * 100:.0f}%）<br>"
                          f"{leader['銘柄名']}（{leader['ticker']}・{str(leader['規模区分']).replace('TOPIX ', '')}）",
                )


def render_sector_candidates_tab(ohlcv, close_wide, display_days):
    ratio, _ = load_sector_breadth("rs")
    sectors = ratio.iloc[-1].drop("全体").sort_values(ascending=False).index.tolist()
    sector = st.selectbox("業種（RSがプラスの割合が高い順）", sectors, key="candidate_sector", filter_mode=None)

    universe = pd.read_csv(UNIVERSE_FILE).set_index("ticker")
    df = load_latest_rs().rename("rs").to_frame().join(universe[["銘柄名", "33業種区分"]])
    df = df[df["33業種区分"] == sector]
    # 買い候補を探すタブのため、TOPIXに負けている（RSがマイナスの）銘柄は表示しない
    positive = df[df["rs"] > 0]
    top = positive.head(CANDIDATE_COUNT)
    st.caption(f"{sector}: RSを計算できた{len(df)}銘柄のうち、RSがプラスの{len(positive)}銘柄から上位{len(top)}銘柄")
    if top.empty:
        st.info("この業種にRSがプラスの銘柄はありません")
        return

    for row_start in range(0, len(top), 3):
        cols = st.columns(3)
        for col, (ticker, row) in zip(cols, top.iloc[row_start:row_start + 3].iterrows()):
            with col:
                render_chart(
                    ticker, ohlcv, close_wide, f"candidate_{ticker}", display_days,
                    show_rs=False, height=280,
                    title=f"<b>{row['銘柄名']}</b>（{ticker}）<br>RS {row['rs'] * 100:+.1f}%",
                )


st.title("📈 日本株 スクリーニング")

with st.sidebar:
    st.subheader("表示設定")
    period_map = {"1ヶ月": 30, "3ヶ月": 90, "6ヶ月": 180, "1年": 365, "全期間": None}
    # 選択ボックスはいずれも文字入力による絞り込みを無効にする（入力は想定せず、カーソルの点滅が気になるため）
    period_label = st.selectbox("チャート表示期間", list(period_map.keys()), index=2, filter_mode=None)
    display_days = period_map[period_label]

ohlcv = load_ohlcv()
close_wide = load_close_wide_cached()

as_of = data_as_of(ohlcv)
st.caption(f"データ基準日: {as_of:%Y-%m-%d}（{'月火水木金土日'[as_of.weekday()]}）の終値")

# 選択中のタブだけを実行する（既定では全タブを毎回描画するため、チャートの多いタブを足すと全操作が重くなる）
tab_rs, tab_po, tab_watch, tab_golden, tab_breadth, tab_overview, tab_candidates = st.tabs(
    ["RS上位", "PO新規", "監視中", "ゴールデンクロス", "業種別", "業種俯瞰", "業種内候補"],
    default="監視中", key="main_tab", on_change="rerun"
)

if tab_rs.open:
    with tab_rs:
        ranking = load_ranking()
        st.caption(f"流動性フィルタ通過後、TOPIX(1306.T)に対する相対強度(21営業日)の上位{len(ranking)}銘柄")
        display_df = ranking.copy()
        display_df["RS(%)"] = (display_df["relative_strength"] * 100).round(2)
        # 境目の1銘柄ほどはrs_ranking.csv（件数×10%を切り捨て）とload_rs_top()の上位がずれ、上位入りの列が空欄になる
        display_df = display_df.merge(load_rs_entry(), on="ticker", how="left")
        display_df["last_fresh_po"] = display_df["ticker"].map(load_last_fresh_po())
        display_df = display_df[
            ["33業種区分", "銘柄名", "ticker", "RS(%)", "entry_date", "entry_elapsed", "entry_top_days", "last_fresh_po"]
        ].rename(columns={"33業種区分": "業種", "entry_date": "上位入り日", "entry_elapsed": "経過日数",
                          "entry_top_days": "RS上位日数", "last_fresh_po": "PO新規日"})
        render_screening_tab(display_df, "rs", ohlcv, close_wide, display_days, f"rs_top_{as_of:%Y%m%d}.txt",
                             po_date_col="PO新規日", shade_watch=True, entry_date_col="上位入り日")

if tab_po.open:
    with tab_po:
        po = load_perfect_order()
        st.caption(
            f"流動性フィルタ通過銘柄のうち、パーフェクトオーダー（5EMA > 25EMA > 75EMA）が直近{PO_LOOKBACK}営業日以内に新規成立"
            f"（直前20営業日は不成立）し、成立日のRSがプラスの{len(po)}銘柄。データが63営業日に満たない銘柄は対象外"
        )
        display_df = po.copy()
        display_df["RS(%)"] = (display_df["rs"] * 100).round(2)
        display_df = display_df[["33業種区分", "銘柄名", "ticker", "RS(%)", "po_date"]].rename(
            columns={"33業種区分": "業種", "po_date": "PO成立日"}
        )
        render_screening_tab(display_df, "po", ohlcv, close_wide, display_days, f"perfect_order_{as_of:%Y%m%d}.txt",
                             po_date_col="PO成立日")

if tab_watch.open:
    with tab_watch:
        watch = load_watch_list()
        st.caption(
            f"PO新規+RSプラスの翌日から{WATCH_DAYS}営業日目までの{len(watch)}銘柄と、その間にRS上位{RS_TOP_PCT:.0%}にいた日数。"
            f"青字は{WATCH_MIN_ELAPSED}日目以降でRS上位にいた日が{WATCH_MIN_SHARE:.0%}以上の銘柄。参考情報（買いの合図ではない）"
        )
        display_df = watch.copy()
        display_df["RS上位(%)"] = (display_df["rs_top_share"] * 100).round().astype(int)
        display_df["RS(%)"] = (display_df["rs"] * 100).round(2)
        display_df = display_df[
            ["33業種区分", "銘柄名", "ticker", "RS(%)", "po_date", "elapsed", "rs_top_days", "RS上位(%)"]
        ].rename(columns={"33業種区分": "業種", "po_date": "PO成立日", "elapsed": "経過日数", "rs_top_days": "RS上位日数"})
        staying = (watch["rs_top_share"] >= WATCH_MIN_SHARE) & (watch["elapsed"] >= WATCH_MIN_ELAPSED)
        render_screening_tab(display_df, "watch", ohlcv, close_wide, display_days,
                             f"watch_list_{as_of:%Y%m%d}.txt", highlight=staying, po_date_col="PO成立日", shade_watch=True)

if tab_golden.open:
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

if tab_breadth.open:
    with tab_breadth:
        st.caption("流動性フィルタ通過銘柄のうち、各条件を満たす銘柄の割合（業種別）")
        render_breadth_tab(display_days)

if tab_overview.open:
    with tab_overview:
        st.caption("業種ごとの代表銘柄（JPXの規模区分が最上位の銘柄のうち売買代金が最大）の日足。業種別タブの「RSがプラス」の割合が高い順")
        render_sector_overview_tab(ohlcv, close_wide, display_days)

if tab_candidates.open:
    with tab_candidates:
        st.caption("選択した業種の流動性フィルタ通過銘柄を、TOPIXに対する相対強度（21営業日）の高い順に表示")
        render_sector_candidates_tab(ohlcv, close_wide, display_days)

st.divider()
st.caption(
    "⚠️ 本ダッシュボードは情報提供目的のみです。投資判断はご自身の責任で行ってください。"
)
