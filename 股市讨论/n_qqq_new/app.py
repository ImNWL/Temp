from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
STOCK_DATA_DIR = APP_DIR.parent / "stockData"
FETCH_SCRIPT_PATH = STOCK_DATA_DIR / "fetch_data.py"
TRADING_DAYS_PER_YEAR = 252

# Curated editorial financial palette anchored on Sapphire Blue
FIXED_COLORS: dict[str, str] = {
    "QQQ": "#1D4ED8",   # Sapphire Blue
    "QLD": "#0891B2",   # Cerulean Cyan
    "TQQQ": "#E11D48",  # Rose Crimson
}
FALLBACK_PALETTE = [
    "#7C3AED",  # Violet
    "#D97706",  # Amber Gold
    "#0284C7",  # Sky Blue
    "#DB2777",  # Berry Pink
    "#4F46E5",  # Indigo
    "#0D9488",  # Teal
    "#EA580C",  # Burnt Orange
    "#65A30D",  # Olive Green
]
SYNTH_CUSTOM_COLOR = "#059669"  # Emerald

@dataclass
class DatasetEntry:
    key: str
    kind: str  # "market" or "strategy"
    path: Path
    series: pd.Series


@dataclass
class CurveMeta:
    name: str
    kind_label: str  # "真实行情" | "策略" | "合成杠杆" | "合成参考"
    color: str
    dash: str
    width: float
    is_partial: bool = False
    actual_start: date | None = None
    wiped_out: bool = False
    wipeout_date: date | None = None


st.set_page_config(
    page_title="策略对比",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    /* Typography & Layout */
    html, body, [class*="css"] {
        font-family: "Inter", -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Helvetica Neue", sans-serif;
        font-variant-numeric: tabular-nums;
    }
    .block-container,
    [data-testid="stMainBlockContainer"] {
        padding-top: 1.25rem;
        padding-bottom: 2.25rem;
        padding-left: 0.75rem;
        padding-right: 0.75rem;
        max-width: none;
    }
    header[data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stSidebarHeader"] {
        height: 2rem;
        min-height: 2rem;
    }

    /* Sidebar subtle separation & polish */
    section[data-testid="stSidebar"] {
        background-color: #FFFFFF;
        border-right: 1px solid #E2E8F0;
    }
    section[data-testid="stSidebar"] .block-container {
        padding-top: 1.25rem;
    }

    /* Title Banner */
    .hero-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 0.4rem;
        margin-bottom: 0.35rem;
    }
    [data-testid="stMarkdownContainer"] h1.hero-title {
        font-size: 1.05rem;
        font-weight: 600;
        color: #0F172A;
        letter-spacing: -0.02em;
        margin: 0;
        padding: 0;
        line-height: 1.3;
    }
    .hero-title span.accent {
        color: #2563EB;
    }
    .hero-subtitle {
        font-size: 0.875rem;
        color: #64748B;
        margin: 0.25rem 0 0 0;
    }
    .hero-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: #EFF6FF;
        color: #1D4ED8;
        border: 1px solid #BFDBFE;
        border-radius: 999px;
        padding: 2px 8px;
        font-size: 0.7rem;
        font-weight: 500;
        line-height: 1.3;
    }

    /* Soft elevated cards for bordered containers */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0 !important;
        border-radius: 14px !important;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03), 0 6px 20px rgba(37, 99, 235, 0.04);
        padding: 0.9rem 1.1rem !important;
    }

    /* Blue-themed multiselect tags */
    span[data-baseweb="tag"] {
        background-color: #EFF6FF !important;
        color: #1E40AF !important;
        border: 1px solid #BFDBFE !important;
        border-radius: 6px !important;
    }
    span[data-baseweb="tag"] svg {
        fill: #3B82F6 !important;
    }

    /* Compact rounded buttons */
    .stButton > button {
        border-radius: 8px;
        font-weight: 500;
        white-space: nowrap;
        padding-left: 0.45rem;
        padding-right: 0.45rem;
        transition: all 0.15s ease;
    }
    .stButton > button[kind="secondary"] {
        border-color: #CBD5E1;
        color: #334155;
        background-color: #F8FAFC;
    }
    .stButton > button[kind="secondary"]:hover {
        border-color: #3B82F6;
        color: #1D4ED8;
        background-color: #EFF6FF;
    }

    /* Divider softness */
    hr {
        border-color: #E2E8F0 !important;
        margin: 0.85rem 0 !important;
    }

    /* Dataframe crisp header */
    div[data-testid="stDataFrame"] {
        border-radius: 10px;
        overflow: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _load_fetcher_module():
    spec = importlib.util.spec_from_file_location("stockdata_fetch", FETCH_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载数据拉取模块: {FETCH_SCRIPT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_csv_series(csv_path: Path, name: str, force_strategy: bool = False) -> DatasetEntry:
    raw = pd.read_csv(csv_path)
    if raw.empty:
        raise ValueError(f"{csv_path.name} 为空")

    lower_map = {str(col).strip().lower(): col for col in raw.columns}

    date_aliases = ("date", "datetime", "time", "timestamp", "trade_date", "日期", "交易日")
    date_col = next((lower_map[a] for a in date_aliases if a in lower_map), None)
    if date_col is None:
        raise ValueError(f"{csv_path.name} 缺少日期列（如 date / 日期）")

    nav_aliases = (
        "close",
        "adj_close",
        "adj close",
        "nav",
        "equity",
        "portfolio_value",
        "value",
        "price",
        "net_value",
        "收盘",
        "收盘价",
        "净值",
        "策略净值",
        "资产",
        "总资产",
    )
    cum_ret_aliases = ("cum_return", "cumulative_return", "total_return", "累计收益", "累计收益率")
    daily_ret_aliases = ("daily_return", "return", "ret", "日收益率", "收益率")

    dates = pd.to_datetime(raw[date_col], errors="coerce")

    val_col = next((lower_map[a] for a in nav_aliases if a in lower_map), None)
    if val_col is not None:
        vals = pd.to_numeric(raw[val_col], errors="coerce")
        series = pd.Series(vals.values, index=dates, name=name).dropna().sort_index()
        series = series[series > 0]
    else:
        cum_col = next((lower_map[a] for a in cum_ret_aliases if a in lower_map), None)
        if cum_col is not None:
            rets = pd.to_numeric(raw[cum_col], errors="coerce")
            series = pd.Series(1.0 + rets.values, index=dates, name=name).dropna().sort_index()
            series = series[series > 0]
        else:
            daily_col = next((lower_map[a] for a in daily_ret_aliases if a in lower_map), None)
            if daily_col is None:
                raise ValueError(
                    f"{csv_path.name} 缺少净值/价格或收益列（如 close / nav / equity / cum_return）"
                )
            rets = pd.to_numeric(raw[daily_col], errors="coerce").fillna(0.0)
            nav_vals = (1.0 + rets).cumprod()
            series = pd.Series(nav_vals.values, index=dates, name=name).dropna().sort_index()
            series = series[series > 0]

    series.index = series.index.tz_localize(None)
    series = series[~series.index.duplicated(keep="last")]

    is_ohlcv = {"open", "high", "low", "close"}.issubset(set(lower_map.keys()))
    kind = "strategy" if (force_strategy or not is_ohlcv) else "market"
    return DatasetEntry(key=name, kind=kind, path=csv_path, series=series)


@st.cache_data(ttl=60 * 60)
def load_catalog() -> tuple[dict[str, DatasetEntry], list[str]]:
    catalog: dict[str, DatasetEntry] = {}
    parse_errors: list[str] = []

    candidates: list[tuple[Path, bool]] = []
    if STOCK_DATA_DIR.exists():
        for p in sorted(STOCK_DATA_DIR.glob("*.csv")):
            candidates.append((p, False))
        strat_dir = STOCK_DATA_DIR / "strategies"
        if strat_dir.exists():
            for p in sorted(strat_dir.glob("*.csv")):
                candidates.append((p, True))

    for csv_path, force_strat in candidates:
        key = f"strategies/{csv_path.stem}" if force_strat else csv_path.stem
        try:
            entry = _parse_csv_series(csv_path, key, force_strategy=force_strat)
            if not entry.series.empty:
                catalog[key] = entry
        except Exception as exc:
            parse_errors.append(f"{csv_path.name}: {exc}")

    priority = ["QQQ", "QLD", "TQQQ"]
    ordered: dict[str, DatasetEntry] = {}
    for k in priority:
        if k in catalog:
            ordered[k] = catalog[k]
    for k, v in catalog.items():
        if k not in ordered:
            ordered[k] = v
    return ordered, parse_errors


def _assign_color(name: str, kind: str, color_idx: int) -> str:
    if name in FIXED_COLORS:
        return FIXED_COLORS[name]
    if kind == "合成":
        return SYNTH_CUSTOM_COLOR
    return FALLBACK_PALETTE[color_idx % len(FALLBACK_PALETTE)]


def _build_synthetic_leverage(
    base_prices: pd.Series,
    leverage: float,
    name: str = "",
) -> tuple[pd.Series, date | None]:
    daily_returns = base_prices.pct_change(fill_method=None).fillna(0.0)
    daily_factors = 1.0 + leverage * daily_returns
    daily_factors.iloc[0] = 1.0

    wipeout_mask = daily_factors <= 0
    wipeout_date: date | None = None
    if wipeout_mask.any():
        first_idx = daily_factors.index[wipeout_mask][0]
        wipeout_date = first_idx.date()
        clamped = daily_factors.copy()
        clamped.loc[first_idx:] = 0.0
        nav = clamped.cumprod()
    else:
        nav = daily_factors.cumprod()

    nav.name = name
    return nav, wipeout_date


def _parse_symbol_tokens(raw_text: str) -> list[str]:
    tokens = [t.strip().upper() for t in re.split(r"[,，\s]+", raw_text.strip()) if t.strip()]
    seen: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.append(t)
    return seen


def _compute_preset_dates(
    preset: str,
    min_date: date,
    max_date: date,
) -> tuple[date, date]:
    if preset == "YTD":
        s = max(min_date, date(max_date.year, 1, 1))
        e = max_date
    elif preset == "近1年":
        s = max(min_date, max_date - timedelta(days=365))
        e = max_date
    elif preset == "近3年":
        s = max(min_date, max_date - timedelta(days=365 * 3 + 1))
        e = max_date
    elif preset == "近5年":
        s = max(min_date, max_date - timedelta(days=365 * 5 + 2))
        e = max_date
    elif preset == "近10年":
        s = max(min_date, max_date - timedelta(days=365 * 10 + 3))
        e = max_date
    elif preset == "全部":
        s = min_date
        e = max_date
    else:
        s = max(min_date, max_date - timedelta(days=365 * 10 + 3))
        e = max_date

    if s >= e:
        s = max(min_date, e - timedelta(days=1))
    return s, e


def build_main_chart(
    nav_df: pd.DataFrame,
    meta_map: dict[str, CurveMeta],
    use_log_scale: bool = False,
) -> go.Figure:
    is_return_mode = not use_log_scale
    plot_df = (nav_df - 1.0) if is_return_mode else nav_df.copy()

    if use_log_scale:
        plot_df = plot_df.where(plot_df > 0)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.74, 0.26],
    )
    chart_height = 670

    hover_fmt = "%{y:+.2%}" if is_return_mode else "%{y:.2f}x"

    for col in plot_df.columns:
        meta = meta_map[col]
        series = plot_df[col]
        valid = series.dropna()
        if valid.empty:
            continue

        opacity = 1.0
        line_width = meta.width
        use_subtle_fill = not use_log_scale and col == "QQQ"
        fill_color = "rgba(37, 99, 235, 0.05)" if use_subtle_fill else None

        last_val = float(valid.iloc[-1])
        val_txt = f"{last_val:+.1%}" if is_return_mode else f"{last_val:.2f}x"
        legend_name = f"{col}  {val_txt}"

        fig.add_trace(
            go.Scatter(
                x=series.index,
                y=series,
                mode="lines",
                name=legend_name,
                legendgroup=col,
                line=dict(color=meta.color, width=line_width, dash=meta.dash),
                fill="tozeroy" if (use_subtle_fill and is_return_mode) else None,
                fillcolor=fill_color if is_return_mode else None,
                opacity=opacity,
                connectgaps=False,
                hovertemplate=f"<b>{col}</b>: {hover_fmt}<extra></extra>",
            ),
            row=1,
            col=1,
        )

        nav_valid = nav_df[col].dropna()
        if not nav_valid.empty:
            dd = (nav_valid / nav_valid.cummax() - 1.0).reindex(nav_df.index)
            fig.add_trace(
                go.Scatter(
                    x=dd.index,
                    y=dd,
                    mode="lines",
                    name=f"{col} 回撤",
                    legendgroup=col,
                    showlegend=False,
                    line=dict(color=meta.color, width=max(0.8, line_width - 0.3), dash=meta.dash),
                    fill="tozeroy" if col == "QQQ" else None,
                    fillcolor="rgba(37, 99, 235, 0.07)" if col == "QQQ" else None,
                    opacity=opacity,
                    connectgaps=False,
                    hovertemplate=f"{col} 回撤: %{{y:.1%}}<extra></extra>",
                ),
                row=2,
                col=1,
            )

    baseline_y = 0.0 if is_return_mode else 1.0
    if not (use_log_scale and not is_return_mode):
        fig.add_hline(
            y=baseline_y,
            line_width=1.2,
            line_dash="dot",
            line_color="rgba(100, 116, 139, 0.45)",
            row=1,
            col=1,
        )

    x_min = plot_df.index.min()
    x_max = plot_df.index.max()
    x_range = [x_min, x_max]

    fig.update_layout(
        height=chart_height,
        margin=dict(l=8, r=18, t=42, b=8),
        showlegend=True,
        hovermode="x unified",
        hoverlabel=dict(
            align="left",
            bgcolor="rgba(255, 255, 255, 0.97)",
            bordercolor="rgba(37, 99, 235, 0.25)",
            font=dict(size=12, color="#0F172A"),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=12, color="#334155"),
            itemclick="toggle",
            itemdoubleclick="toggleothers",
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#334155"),
    )

    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        hoverformat="%Y-%m-%d",
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikedash="dot",
        spikethickness=1,
        spikecolor="rgba(37, 99, 235, 0.40)",
        tickfont=dict(color="#64748B", size=11),
        range=x_range,
    )

    fig.update_yaxes(
        title_text="累计收益率" if is_return_mode else "净值倍数",
        title_font=dict(color="#475569", size=12),
        tickfont=dict(color="#64748B", size=11),
        tickformat=".0%" if is_return_mode else ".2f",
        type="log" if (use_log_scale and not is_return_mode) else "linear",
        showgrid=True,
        gridcolor="rgba(203, 213, 225, 0.35)",
        zeroline=False,
        row=1,
        col=1,
    )

    fig.update_yaxes(
        title_text="回撤",
        title_font=dict(color="#475569", size=12),
        tickfont=dict(color="#64748B", size=11),
        tickformat=".0%",
        showgrid=True,
        gridcolor="rgba(203, 213, 225, 0.35)",
        zeroline=True,
        zerolinecolor="rgba(148, 163, 184, 0.45)",
        row=2,
        col=1,
    )
    fig.update_xaxes(title_text="", row=2, col=1)

    return fig


def calculate_summary(nav_df: pd.DataFrame, meta_map: dict[str, CurveMeta]) -> pd.DataFrame:
    rows = []
    for name in nav_df.columns:
        nav = nav_df[name].dropna()
        if nav.empty:
            continue
        years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1.0 / 365.25)
        final_mult = float(nav.iloc[-1] / nav.iloc[0])
        total_ret = final_mult - 1.0
        cagr = -1.0 if final_mult <= 0 else final_mult ** (1.0 / years) - 1.0

        daily_ret = nav.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        ann_vol = float(daily_ret.std() * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(daily_ret) > 1 else np.nan
        max_dd = float((nav / nav.cummax() - 1.0).min())

        rows.append(
            {
                "标的": name,
                "类型": meta_map[name].kind_label,
                "有效区间": f"{nav.index[0]:%Y-%m-%d} ~ {nav.index[-1]:%Y-%m-%d}",
                "最终倍数": final_mult,
                "累计收益": total_ret,
                "年化收益": cagr,
                "年化波动率": ann_vol,
                "最大回撤": max_dd,
            }
        )
    return pd.DataFrame(rows)


# --- Load Catalog ---
catalog, parse_errors = load_catalog()
if not catalog:
    st.warning("`stockData/` 中暂无可用数据，正在自动拉取 QQQ / QLD / TQQQ ...")
    try:
        fetcher = _load_fetcher_module()
        fetcher.fetch_symbols(("QQQ", "QLD", "TQQQ"), data_dir=STOCK_DATA_DIR)
        load_catalog.clear()
        catalog, parse_errors = load_catalog()
    except Exception as exc:
        st.error(f"拉取失败：{exc}")

if not catalog:
    st.error(f"`{STOCK_DATA_DIR}` 中未找到有效 CSV 数据。")
    st.stop()

all_keys = list(catalog.keys())
default_selected = [k for k in ("QQQ", "QLD", "TQQQ") if k in catalog] or [all_keys[0]]

# --- Session State Init ---
if "selected_items" not in st.session_state:
    st.session_state["selected_items"] = default_selected
else:
    st.session_state["selected_items"] = [k for k in st.session_state["selected_items"] if k in catalog]

if "leverage_val" not in st.session_state:
    st.session_state["leverage_val"] = 1.5
if "date_preset" not in st.session_state:
    st.session_state["date_preset"] = "近10年"


# --- Sidebar ---
with st.sidebar:
    st.markdown("### 对比篮子")

    with st.form("add_ticker_form", clear_on_submit=True):
        new_ticker_raw = st.text_input("添加新标的")
        submitted = st.form_submit_button("加入", type="primary", use_container_width=True)

    if submitted and new_ticker_raw.strip():
        tokens = _parse_symbol_tokens(new_ticker_raw)
        with st.spinner(f"正在拉取 {', '.join(tokens)} ..."):
            try:
                fetcher = _load_fetcher_module()
                results = fetcher.fetch_symbols(tokens, data_dir=STOCK_DATA_DIR)
                ok = [r.symbol for r in results if r.success]
                fail = [f"{r.symbol}({r.error})" for r in results if not r.success]
                if ok:
                    load_catalog.clear()
                    catalog, parse_errors = load_catalog()
                    all_keys = list(catalog.keys())
                    curr = list(st.session_state.get("selected_items", []))
                    for sym in ok:
                        if sym in catalog and sym not in curr:
                            curr.append(sym)
                    st.session_state["selected_items"] = curr
                    st.success(f"已拉取并加入：{', '.join(ok)}")
                if fail:
                    st.error("拉取失败：" + "；".join(fail))
                if ok:
                    st.rerun()
            except Exception as exc:
                st.error(f"拉取异常：{exc}")

    selected_items = st.multiselect(
        "已选股票 / 策略",
        options=all_keys,
        key="selected_items",
        format_func=lambda k: f"{k} [策略]" if catalog[k].kind == "strategy" else k,
        help="自动扫描 stockData/*.csv 与 stockData/strategies/*.csv；将策略净值 CSV 放入 stockData 即可勾选。",
    )

    st.divider()
    st.markdown("### 合成杠杆（如 xQQQ）")
    enable_synth = st.checkbox("叠加合成杠杆曲线", value=True)
    if enable_synth:
        synth_base_options = all_keys
        default_base_idx = synth_base_options.index("QQQ") if "QQQ" in synth_base_options else 0
        synth_base = st.selectbox(
            "合成基准标的",
            options=synth_base_options,
            index=default_base_idx,
            format_func=lambda k: f"{k} [策略]" if catalog[k].kind == "strategy" else k,
        )

        leverage = st.number_input(
            "杠杆倍数 x",
            min_value=-5.0,
            max_value=10.0,
            step=0.1,
            key="leverage_val",
        )
    else:
        synth_base = "QQQ" if "QQQ" in catalog else all_keys[0]
        leverage = float(st.session_state["leverage_val"])

    market_selected = [k for k in selected_items if catalog[k].kind == "market"]
    if market_selected:
        st.divider()
        full_update = st.checkbox("全量拉取（2015年起）", value=False)
        if st.button("更新已选行情", use_container_width=True):
            with st.spinner("正在更新正式行情数据..."):
                fetcher = _load_fetcher_module()
                res = fetcher.fetch_symbols(market_selected, data_dir=STOCK_DATA_DIR, full=full_update)
                fail = [f"{r.symbol}: {r.error}" for r in res if not r.success]
                load_catalog.clear()
                if fail:
                    st.session_state["update_notice"] = ("error", "部分更新失败：" + "；".join(fail))
                else:
                    mode_txt = "全量更新" if full_update else "增量更新"
                    detail = "、".join(f"{r.symbol}(+{r.added_rows})" for r in res if r.success)
                    st.session_state["update_notice"] = ("success", f"{mode_txt}完成：{detail}")
                    st.rerun()

        if "update_notice" in st.session_state:
            lvl, msg = st.session_state.pop("update_notice")
            if lvl == "error":
                st.error(msg)
            else:
                st.success(msg)

    if parse_errors:
        st.warning("部分 CSV 未加载：" + " | ".join(parse_errors))


# --- Validate at least 1 active series ---
active_keys = list(selected_items)
active_base_series = [catalog[k].series for k in active_keys]
if enable_synth:
    active_base_series.append(catalog[synth_base].series)

if not active_base_series:
    st.info("请在左侧栏至少选择一个股票/策略，或开启合成杠杆曲线。")
    st.stop()

global_min_date = min(s.index.min().date() for s in active_base_series)
global_max_date = max(s.index.max().date() for s in active_base_series)

if "start_date_input" not in st.session_state or "end_date_input" not in st.session_state:
    s0, e0 = _compute_preset_dates("近10年", global_min_date, global_max_date)
    st.session_state["start_date_input"] = s0
    st.session_state["end_date_input"] = e0
else:
    st.session_state["start_date_input"] = max(global_min_date, min(st.session_state["start_date_input"], global_max_date))
    st.session_state["end_date_input"] = max(global_min_date, min(st.session_state["end_date_input"], global_max_date))


def _on_preset_change() -> None:
    preset = st.session_state.get("date_preset")
    if preset:
        s, e = _compute_preset_dates(preset, global_min_date, global_max_date)
        st.session_state["start_date_input"] = s
        st.session_state["end_date_input"] = e


def _on_manual_date_change() -> None:
    st.session_state["date_preset"] = None


# --- Header Banner ---
active_count = len(active_keys) + (1 if enable_synth else 0)
st.markdown(
    f"""
    <div class="hero-header">
        <div>
            <h1 class="hero-title">策略对比</h1>
        </div>
        <div class="hero-pill">● 当前对比 {active_count} 条曲线 · 最新数据至 {global_max_date:%Y-%m-%d}</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# --- Time Range Bar ---
preset_opts = ["YTD", "近1年", "近3年", "近5年", "近10年", "全部"]
t_col1, t_col2, t_col3 = st.columns([3.4, 1.0, 1.0])
with t_col1:
    st.segmented_control(
        "快捷周期",
        options=preset_opts,
        key="date_preset",
        on_change=_on_preset_change,
        label_visibility="collapsed",
    )
with t_col2:
    start_date = st.date_input(
        "开始日期",
        min_value=global_min_date,
        max_value=global_max_date,
        key="start_date_input",
        on_change=_on_manual_date_change,
        label_visibility="collapsed",
    )
with t_col3:
    end_date = st.date_input(
        "结束日期",
        min_value=global_min_date,
        max_value=global_max_date,
        key="end_date_input",
        on_change=_on_manual_date_change,
        label_visibility="collapsed",
    )

if start_date >= end_date:
    st.error("开始日期必须早于结束日期。")
    st.stop()


# --- Assemble Normalized Curves ---
series_list: list[pd.Series] = []
meta_map: dict[str, CurveMeta] = {}
color_counter = 0

for k in active_keys:
    entry = catalog[k]
    s_win = entry.series.loc[
        (entry.series.index.date >= start_date) & (entry.series.index.date <= end_date)
    ]
    if s_win.empty:
        continue

    display_name = f"{k} [策略]" if entry.kind == "strategy" else k
    norm = (s_win / s_win.iloc[0]).rename(display_name)
    series_list.append(norm)

    color = _assign_color(k, entry.kind, color_counter)
    if k not in FIXED_COLORS:
        color_counter += 1

    meta_map[display_name] = CurveMeta(
        name=display_name,
        kind_label="策略" if entry.kind == "strategy" else "真实行情",
        color=color,
        dash="solid",
        width=1.4 if entry.kind == "strategy" else 1.2,
        is_partial=s_win.index[0].date() > start_date + timedelta(days=5),
        actual_start=s_win.index[0].date(),
    )

if enable_synth:
    base_entry = catalog[synth_base]
    base_win = base_entry.series.loc[
        (base_entry.series.index.date >= start_date) & (base_entry.series.index.date <= end_date)
    ]
    if not base_win.empty:
        synth_name = f"{leverage:g}x {synth_base} (合成)"
        synth_nav, wipe_dt = _build_synthetic_leverage(base_win, leverage, synth_name)
        series_list.append(synth_nav)
        meta_map[synth_name] = CurveMeta(
            name=synth_name,
            kind_label="合成杠杆",
            color=_assign_color(synth_name, "合成", 0),
            dash="solid",
            width=1.2,
            is_partial=base_win.index[0].date() > start_date + timedelta(days=5),
            actual_start=base_win.index[0].date(),
            wiped_out=wipe_dt is not None,
            wipeout_date=wipe_dt,
        )

if not series_list:
    st.warning("所选时间范围内没有任何可用数据，请调整日期范围。")
    st.stop()

nav_df = pd.concat(series_list, axis=1).sort_index().dropna(how="all")


# --- Hero Chart Card ---
with st.container(border=True):
    partial_list = [f"**{m.name}** (`{m.actual_start}` 起)" for m in meta_map.values() if m.is_partial]
    if partial_list:
        st.caption(
            "注：以下曲线晚于所选开始日期，按其区间内首个交易日归零起算："
            + "、".join(partial_list)
            + "。"
        )

    wipe_list = [f"**{m.name}** 在 `{m.wipeout_date}` 触发单日归零" for m in meta_map.values() if m.wiped_out]
    if wipe_list:
        st.warning("；".join(wipe_list) + "（已按清零截断）。")

    fig = build_main_chart(nav_df, meta_map)
    log_fig = build_main_chart(nav_df, meta_map, use_log_scale=True)
    scale_buttons = []
    for label, mode_fig in [("线性", fig), ("对数", log_fig)]:
        scale_buttons.append(
            dict(
                label=label,
                method="update",
                args=[
                    dict(
                        y=[trace.y for trace in mode_fig.data],
                        name=[trace.name for trace in mode_fig.data],
                        hovertemplate=[trace.hovertemplate for trace in mode_fig.data],
                        fill=[trace.fill for trace in mode_fig.data],
                        fillcolor=[trace.fillcolor for trace in mode_fig.data],
                    ),
                    {
                        "yaxis": {**mode_fig.layout.yaxis.to_plotly_json(), "autorange": True},
                        "shapes": [shape.to_plotly_json() for shape in mode_fig.layout.shapes],
                    },
                ],
            )
        )
    fig.update_layout(
        margin=dict(l=8, r=18, t=88, b=8),
        updatemenus=[
            dict(
                type="buttons",
                direction="right",
                x=0,
                y=1.02,
                xanchor="left",
                yanchor="bottom",
                active=0,
                showactive=True,
                pad=dict(l=0, r=0, t=0, b=0),
                bgcolor="#F8FAFC",
                bordercolor="#CBD5E1",
                font=dict(size=12, color="#334155"),
                buttons=scale_buttons,
            )
        ],
    )
    st.plotly_chart(fig, width="stretch")


# --- Summary Card ---
with st.container(border=True):
    st.markdown("#### 区间表现汇总")
    summary_df = calculate_summary(nav_df, meta_map)
    st.dataframe(
        summary_df,
        width="stretch",
        hide_index=True,
        column_config={
            "最终倍数": st.column_config.NumberColumn(format="%.2fx"),
            "累计收益": st.column_config.NumberColumn(format="percent"),
            "年化收益": st.column_config.NumberColumn(format="percent"),
            "年化波动率": st.column_config.NumberColumn(format="percent"),
            "最大回撤": st.column_config.NumberColumn(format="percent"),
        },
    )
