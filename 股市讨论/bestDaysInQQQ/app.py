from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
QQQ_PRICE_PATH = APP_DIR.parent / "stockData" / "QQQ.csv"

FLOOR_COLOR = "#0F766E"
BROKEN_LATER_COLOR = "#CBD5E1"
EPSILON = 1e-9


st.set_page_config(
    page_title="QQQ 未来未跌破交易日",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
)


@st.cache_data(ttl=60 * 60)
def load_qqq_prices() -> pd.DataFrame:
    raw = pd.read_csv(QQQ_PRICE_PATH)
    columns = {column.lower(): column for column in raw.columns}
    required_columns = {"date", "close"}
    missing_columns = required_columns - set(columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"QQQ.csv 缺少必要列：{missing_text}")

    prices = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[columns["date"]], errors="coerce"),
            "close": pd.to_numeric(raw[columns["close"]], errors="coerce"),
        }
    )
    prices = prices.dropna(subset=["date", "close"]).sort_values("date")
    prices = prices.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    prices["date"] = prices["date"].dt.tz_localize(None)

    return ensure_price_annotations(prices)


def ensure_price_annotations(prices: pd.DataFrame) -> pd.DataFrame:
    required_columns = {"future_never_lower", "future_detail_text", "latest_lower_days_text"}
    if required_columns.issubset(prices.columns):
        return prices

    prices = prices.copy()

    # For each day, look only at later closes. If none are lower, this day is
    # on the right-to-left price floor and gets the "future never lower" color.
    future_min_close = prices["close"].shift(-1).iloc[::-1].cummin().iloc[::-1]
    prices["future_min_close"] = future_min_close
    prices["future_never_lower"] = future_min_close.isna() | (
        future_min_close >= prices["close"] - EPSILON
    )
    add_latest_lower_info(prices)
    return prices


def add_latest_lower_info(prices: pd.DataFrame) -> None:
    closes = prices["close"].tolist()
    dates = prices["date"].tolist()
    latest_lower_dates = [pd.NaT] * len(prices)

    for index, close in enumerate(closes):
        for future_index in range(len(closes) - 1, index, -1):
            if closes[future_index] < close - EPSILON:
                latest_lower_dates[index] = dates[future_index]
                break

    prices["latest_lower_date"] = latest_lower_dates
    prices["latest_lower_date_text"] = pd.to_datetime(prices["latest_lower_date"]).dt.strftime("%Y-%m-%d")
    prices["latest_lower_days"] = (pd.to_datetime(prices["latest_lower_date"]) - prices["date"]).dt.days
    prices["latest_lower_days_text"] = prices["latest_lower_days"].map(
        lambda value: "" if pd.isna(value) else f"{int(value)} 天后"
    )
    prices["future_detail_text"] = prices.apply(
        lambda row: "未来：未出现更低收盘价"
        if row["future_never_lower"]
        else f"最近跌破日期：{row['latest_lower_date_text']}（{row['latest_lower_days_text']}）",
        axis=1,
    )


def add_segment_trace(
    fig: go.Figure,
    window: pd.DataFrame,
    start: int,
    end: int,
    is_floor: bool,
    legend_seen: dict[bool, bool],
) -> None:
    label = "未来不再跌破该收盘价" if is_floor else "未来曾跌破该收盘价"
    color = FLOOR_COLOR if is_floor else BROKEN_LATER_COLOR
    segment = window.iloc[start:end]
    fig.add_trace(
        go.Scatter(
            x=segment["date"],
            y=segment["close"],
            mode="lines",
            name=label,
            line=dict(color=color, width=1),
            legendgroup=label,
            showlegend=not legend_seen[is_floor],
            hoverinfo="skip",
        )
    )
    legend_seen[is_floor] = True


def add_hover_trace(fig: go.Figure, window: pd.DataFrame) -> None:
    fig.add_trace(
        go.Scatter(
            x=window["date"],
            y=window["close"],
            customdata=window[["future_detail_text"]],
            mode="lines+markers",
            name="QQQ 收盘价",
            line=dict(color="rgba(0,0,0,0)", width=6),
            marker=dict(color="rgba(0,0,0,0)", size=4),
            showlegend=False,
            hovertemplate=(
                "QQQ 收盘价：%{y:.2f}<br>"
                "%{customdata[0]}<extra></extra>"
            ),
        )
    )


def build_chart(window: pd.DataFrame) -> go.Figure:
    fig = go.Figure()

    if len(window) == 1:
        is_floor = bool(window["future_never_lower"].iloc[0])
        label = "未来不再跌破该收盘价" if is_floor else "未来曾跌破该收盘价"
        color = FLOOR_COLOR if is_floor else BROKEN_LATER_COLOR
        fig.add_trace(
            go.Scatter(
                x=window["date"],
                y=window["close"],
                mode="markers",
                name=label,
                marker=dict(color=color, size=8),
                hoverinfo="skip",
            )
        )
        add_hover_trace(fig, window)
        return style_chart(fig)

    segment_flags = window["future_never_lower"].iloc[:-1].astype(bool).tolist()
    legend_seen = {True: False, False: False}
    segment_start = 0
    current_flag = segment_flags[0]

    for segment_index, flag in enumerate(segment_flags[1:], start=1):
        if flag == current_flag:
            continue

        add_segment_trace(
            fig=fig,
            window=window,
            start=segment_start,
            end=segment_index + 1,
            is_floor=current_flag,
            legend_seen=legend_seen,
        )
        segment_start = segment_index
        current_flag = flag

    add_segment_trace(
        fig=fig,
        window=window,
        start=segment_start,
        end=len(window),
        is_floor=current_flag,
        legend_seen=legend_seen,
    )

    add_hover_trace(fig, window)
    return style_chart(fig)


def style_chart(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        height=650,
        margin=dict(l=20, r=20, t=20, b=20),
        hovermode="x unified",
        hoverdistance=100,
        hoverlabel=dict(align="left", bgcolor="rgba(255,255,255,0.9)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(title="日期", hoverformat="%Y-%m-%d"),
        yaxis=dict(title="QQQ 收盘价（美元）", tickprefix="$"),
    )
    return fig


st.title("QQQ：未来未跌破交易日")
st.caption(
    "绿色表示该交易日之后，再也没有任何一天的 QQQ 收盘价低于当天收盘价；"
    "橙色表示之后曾出现更低收盘价。颜色按完整 CSV 的未来数据计算，时间范围只控制图表展示窗口。"
)

try:
    qqq_prices = load_qqq_prices()
except Exception as exc:
    st.error(f"读取本地 QQQ 数据失败：{exc}")
    st.stop()

if qqq_prices.empty:
    st.error("本地 QQQ 数据为空。")
    st.stop()

qqq_prices = ensure_price_annotations(qqq_prices)

data_start_date = qqq_prices["date"].min().date()
data_end_date = qqq_prices["date"].max().date()

with st.sidebar:
    st.header("参数")
    start_date = st.date_input("开始日期", value=data_start_date, min_value=data_start_date, max_value=data_end_date)
    end_date = st.date_input("结束日期", value=data_end_date, min_value=data_start_date, max_value=data_end_date)
    st.divider()
    st.markdown(
        "判定口径：使用 `close` 收盘价。某天之后所有交易日的收盘价都不低于当天收盘价时，"
        "该日标记为绿色。"
    )
    st.caption(f"本地数据源：`{QQQ_PRICE_PATH}`")

if start_date >= end_date:
    st.error("开始日期必须早于结束日期。")
    st.stop()

window = qqq_prices.loc[
    (qqq_prices["date"].dt.date >= start_date) & (qqq_prices["date"].dt.date <= end_date)
].copy()
if window.empty:
    st.error("所选日期范围内没有 QQQ 数据，请调整日期范围后重试。")
    st.stop()

st.plotly_chart(build_chart(window), use_container_width=True)
