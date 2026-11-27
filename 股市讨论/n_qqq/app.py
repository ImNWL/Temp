from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


DEFAULT_LOOKBACK_DAYS = 365 * 10 + 3
DEFAULT_START_DATE = date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
APP_DIR = Path(__file__).resolve().parent
QQQ_PRICE_PATH = APP_DIR.parent / "stockData" / "QQQ.csv"


st.set_page_config(
    page_title="QQQ 杠杆收益率对比",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
)


def _to_percent(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"{value:.2%}"


def _to_multiple(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"{value:.2f}x"


@st.cache_data(ttl=60 * 60)
def load_qqq_prices() -> pd.Series:
    raw = pd.read_csv(QQQ_PRICE_PATH)
    columns = {column.lower(): column for column in raw.columns}
    if "date" not in columns or "close" not in columns:
        raise ValueError("QQQ.csv 必须包含 date 和 close 列")

    dates = pd.to_datetime(raw[columns["date"]], errors="coerce")
    close = pd.to_numeric(raw[columns["close"]], errors="coerce")

    prices = pd.Series(close.values, index=dates, name="QQQ").dropna().sort_index()
    prices.index = prices.index.tz_localize(None)
    return prices[~prices.index.duplicated(keep="last")]


def build_synthetic_leverage(qqq_prices: pd.Series, leverage: float) -> pd.Series:
    daily_returns = qqq_prices.pct_change(fill_method=None).fillna(0)
    daily_factors = 1 + leverage * daily_returns

    synthetic_nav = daily_factors.cumprod()
    synthetic_nav.name = f"{leverage:g}x QQQ"
    return synthetic_nav


def build_nav_curves(qqq_prices: pd.Series, leverage: float) -> pd.DataFrame:
    qqq_nav = qqq_prices / qqq_prices.iloc[0]
    qqq_nav.name = "QQQ"

    custom_nav = build_synthetic_leverage(qqq_prices, leverage)
    qld_nav = build_synthetic_leverage(qqq_prices, 2.0).rename("QLD")
    tqqq_nav = build_synthetic_leverage(qqq_prices, 3.0).rename("TQQQ")

    return pd.concat([custom_nav, qqq_nav, qld_nav, tqqq_nav], axis=1).dropna(how="all")


def calculate_max_drawdown(nav: pd.Series) -> float:
    drawdown = nav / nav.cummax() - 1
    return drawdown.min()


def calculate_summary(nav_curves: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, nav in nav_curves.items():
        nav = nav.dropna()
        if nav.empty:
            continue

        years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1 / 365.25)
        total_return = nav.iloc[-1] / nav.iloc[0] - 1
        annual_return = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
        annual_volatility = nav.pct_change().std() * (252**0.5)

        rows.append(
            {
                "标的": name,
                "最终倍数": _to_multiple(nav.iloc[-1] / nav.iloc[0]),
                "累计收益": _to_percent(total_return),
                "年化收益": _to_percent(annual_return),
                "年化波动率": _to_percent(annual_volatility),
                "最大回撤": _to_percent(calculate_max_drawdown(nav)),
            }
        )

    return pd.DataFrame(rows)


def get_line_color(name: str) -> str:
    if name == "QQQ":
        return "#0072B2"
    if name.startswith("QLD"):
        return "#E69F00"
    if name.startswith("TQQQ"):
        return "#D55E00"
    return "#27ae60"


def build_chart(curves: pd.DataFrame, use_log_scale: bool) -> go.Figure:
    fig = go.Figure()
    for column in curves.columns:
        fig.add_trace(
            go.Scatter(
                x=curves.index,
                y=curves[column],
                mode="lines",
                name=column,
                line=dict(color=get_line_color(column), width=1),
                customdata=[column] * len(curves),
                hovertemplate="%{customdata}<br>%{y:.1%}<extra></extra>"
                if not use_log_scale
                else "%{customdata}<br>%{y:.1f}x<extra></extra>",
            )
        )

    fig.update_layout(
        height=620,
        margin=dict(l=20, r=20, t=30, b=20),
        hovermode="x unified",
        hoverlabel=dict(align="left", bgcolor="rgba(255,255,255,0.85)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        yaxis=dict(
            title="净值倍数" if use_log_scale else "累计收益率",
            tickformat=".2f" if use_log_scale else ".0%",
            type="log" if use_log_scale else "linear",
        ),
        xaxis=dict(title="日期", hoverformat="%Y-%m-%d"),
    )
    return fig


st.title("QQQ / QLD / TQQQ / 自定义 x 倍 QQQ 收益率对比")


try:
    qqq_prices_all = load_qqq_prices()
except Exception as exc:
    st.error(f"读取本地 QQQ 数据失败：{exc}")
    st.stop()

if qqq_prices_all.empty:
    st.error("本地 QQQ 数据为空。")
    st.stop()

data_start_date = qqq_prices_all.index.min().date()
data_end_date = qqq_prices_all.index.max().date()
default_start_date = max(data_start_date, min(DEFAULT_START_DATE, data_end_date))

with st.sidebar:
    st.header("参数")
    leverage = st.number_input("自定义杠杆倍数 x", min_value=-5.0, max_value=10.0, value=1.5, step=0.1)
    start_date = st.date_input("开始日期", value=default_start_date, min_value=data_start_date, max_value=data_end_date)
    end_date = st.date_input("结束日期", value=data_end_date, min_value=data_start_date, max_value=data_end_date)
    use_log_scale = st.checkbox("用净值倍数展示并使用对数刻度", value=False)

    st.divider()
    st.markdown(
        "说明：这里的 `QLD` / `TQQQ` 是基于 QQQ 每日收益合成的 2x / 3x 曲线，"
        "不是从真实 ETF 价格文件读取的历史净值。"
    )
    st.caption(f"本地数据源：`{QQQ_PRICE_PATH.relative_to(APP_DIR)}`")

if start_date >= end_date:
    st.error("开始日期必须早于结束日期。")
    st.stop()

qqq_prices = qqq_prices_all.loc[(qqq_prices_all.index.date >= start_date) & (qqq_prices_all.index.date <= end_date)]
if qqq_prices.empty:
    st.error("所选日期范围内没有 QQQ 数据，请调整日期范围后重试。")
    st.stop()

if (1 + leverage * qqq_prices.pct_change(fill_method=None).dropna()).le(0).any():
    st.warning("所选杠杆倍数在历史某些交易日会让合成净值小于或等于 0，结果仅作数学模拟参考。")

nav_curves = build_nav_curves(qqq_prices, leverage)
return_curves = nav_curves - 1

chart_curves = nav_curves if use_log_scale else return_curves
if use_log_scale and chart_curves.le(0).any().any():
    st.warning("对数刻度无法显示小于或等于 0 的净值点，这些点已在图中隐藏。")
    chart_curves = chart_curves.where(chart_curves > 0)
st.plotly_chart(build_chart(chart_curves, use_log_scale), use_container_width=True)

st.subheader("区间表现")
st.dataframe(calculate_summary(nav_curves), width="stretch", hide_index=True)
