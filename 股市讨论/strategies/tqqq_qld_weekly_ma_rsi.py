"""
TQQQ / QLD 周线均线 + RSI 牛熊周期择时策略（以 QQQ 作为信号指标，纯后端实时计算模块）

策略规则（按所选观察窗口 [start_date, end_date] 实时计算）：
1. 初始状态：在当前所选观察窗口首个交易日满仓持有 QLD（未进入熊市前一直持有 QLD，不在牛市中途直接切 TQQQ）。
2. 熊市判断：当 QQQ 周线收盘跌破周线 MA40 且周线 RSI(14) < 50 时，判断进入熊市。
3. 反转操作：在已进入熊市后，当 QQQ 周线收盘同时站上 MA10、MA20、MA40 上方时，判断迎来反转，
   于下一交易日起将 QLD 全部转换为 TQQQ，并锁定当时 QQQ 历史前高。
4. 获利了结：持有 TQQQ 期间，当 QQQ 日收盘上涨到「前高 × 1.2」时，
   于下一交易日起将 TQQQ 全部转换回 QLD，继续等待下一个熊市周期。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

STRATEGY_KEY = "strategy:tqqq_qld_weekly_ma_rsi"
STRATEGY_LABEL = "TQQQ/QLD 周线择时"
STRATEGY_DESC = "窗口首日满仓QLD；以QQQ判断：入熊后站上周线MA10/20/40转TQQQ，QQQ涨至前高×1.2止盈回QLD"
REQUIRED_SYMBOLS = ("QQQ", "QLD", "TQQQ")

# 缓存全历史预计算指标，拖动时间轴连续请求时无需重复计算均线与 RSI
_PREPARED_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def compute_wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """标准 Wilder 平滑 RSI。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    both_zero = (avg_loss == 0.0) & (avg_gain == 0.0)
    only_loss_zero = (avg_loss == 0.0) & (avg_gain > 0.0)
    rsi = rsi.mask(only_loss_zero, 100.0).mask(both_zero, 50.0)
    return rsi


def compute_weekly_signals(qqq_close: pd.Series) -> pd.DataFrame:
    """基于 QQQ 全历史日线收盘价提取每周最后交易日收盘，计算周线 MA10/20/40 与 RSI(14)。"""
    s = qqq_close.sort_index().dropna()
    if s.empty:
        return pd.DataFrame()

    df_daily = pd.DataFrame({"close": s})
    df_daily["week_period"] = df_daily.index.to_period("W-FRI")

    weekly_rows = df_daily.groupby("week_period", sort=True).tail(1).copy()
    w_close = weekly_rows["close"]

    ma10 = w_close.rolling(10, min_periods=10).mean()
    ma20 = w_close.rolling(20, min_periods=20).mean()
    ma40 = w_close.rolling(40, min_periods=40).mean()
    rsi14 = compute_wilder_rsi(w_close, period=14)

    bear_sig = (w_close < ma40) & (rsi14 < 50.0)
    rev_sig = (w_close > ma10) & (w_close > ma20) & (w_close > ma40)

    # 若最后一周尚未结束（非周五且距今不足 3 天），不提前触发未完结周线信号
    if len(weekly_rows) > 0:
        last_dt = weekly_rows.index[-1]
        if last_dt.weekday() < 4 and (pd.Timestamp.now().normalize() - last_dt).days < 3:
            bear_sig.iloc[-1] = False
            rev_sig.iloc[-1] = False

    return pd.DataFrame(
        {
            "w_close": w_close,
            "ma10": ma10,
            "ma20": ma20,
            "ma40": ma40,
            "rsi14": rsi14,
            "bear_sig": bear_sig.fillna(False).astype(bool),
            "rev_sig": rev_sig.fillna(False).astype(bool),
        },
        index=weekly_rows.index,
    )


def _get_prepared_context(market_series: dict[str, pd.Series]) -> dict[str, Any] | None:
    if not all(sym in market_series for sym in REQUIRED_SYMBOLS):
        return None

    qqq_full = market_series["QQQ"].sort_index().dropna()
    qld_full = market_series["QLD"].sort_index().dropna()
    tqqq_full = market_series["TQQQ"].sort_index().dropna()
    if qqq_full.empty or qld_full.empty or tqqq_full.empty:
        return None

    cache_key = (
        len(qqq_full),
        qqq_full.index[-1],
        float(qqq_full.iloc[-1]),
        len(qld_full),
        qld_full.index[-1],
        float(qld_full.iloc[-1]),
        len(tqqq_full),
        tqqq_full.index[-1],
        float(tqqq_full.iloc[-1]),
    )
    cached = _PREPARED_CACHE.get(cache_key)
    if cached is not None:
        return cached

    weekly_df = compute_weekly_signals(qqq_full)
    qqq_running_peak = qqq_full.cummax()

    aligned = pd.DataFrame(
        {
            "QQQ": qqq_full,
            "QLD": qld_full,
            "TQQQ": tqqq_full,
            "QQQ_PEAK": qqq_running_peak,
        }
    ).dropna()
    aligned = aligned[(aligned["QQQ"] > 0) & (aligned["QLD"] > 0) & (aligned["TQQQ"] > 0)]
    if len(aligned) < 2:
        return None

    bear_dates = set(weekly_df.index[weekly_df["bear_sig"]])
    rev_dates = set(weekly_df.index[weekly_df["rev_sig"]])

    ctx = {
        "dates": aligned.index.strftime("%Y-%m-%d").tolist(),
        "qqq_close": aligned["QQQ"].to_numpy(dtype=float),
        "qqq_peak": aligned["QQQ_PEAK"].to_numpy(dtype=float),
        "ret_qld": aligned["QLD"].pct_change().fillna(0.0).to_numpy(dtype=float),
        "ret_tqqq": aligned["TQQQ"].pct_change().fillna(0.0).to_numpy(dtype=float),
        "is_bear_sig": np.array([ts in bear_dates for ts in aligned.index], dtype=bool),
        "is_rev_sig": np.array([ts in rev_dates for ts in aligned.index], dtype=bool),
    }
    _PREPARED_CACHE.clear()
    _PREPARED_CACHE[cache_key] = ctx
    return ctx


def _bisect_left(arr: list[str], target: str) -> int:
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) >> 1
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _bisect_right(arr: list[str], target: str) -> int:
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) >> 1
        if arr[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def evaluate_strategy(
    market_series: dict[str, pd.Series],
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any] | None:
    """
    由后端按当前所选观察窗口 [start_date, end_date] 实时调用：
    完整计算并返回该区间内的每日净值、每日持仓及全部换仓点标记，前端只负责纯画图。
    """
    ctx = _get_prepared_context(market_series)
    if ctx is None:
        return None

    dates: list[str] = ctx["dates"]
    n_total = len(dates)
    i0 = _bisect_left(dates, start_date) if start_date else 0
    i1 = _bisect_right(dates, end_date) if end_date else n_total
    if i1 <= i0:
        return {
            "key": STRATEGY_KEY,
            "dates": [],
            "values": [],
            "holdings": [],
            "markers": [],
        }

    qqq_close = ctx["qqq_close"]
    qqq_peak = ctx["qqq_peak"]
    ret_qld = ctx["ret_qld"]
    ret_tqqq = ctx["ret_tqqq"]
    is_bear_sig = ctx["is_bear_sig"]
    is_rev_sig = ctx["is_rev_sig"]

    # State 0: 初始/止盈后持有 QLD（等待进入下一个熊市周期）
    # State 1: 已入熊市持有 QLD（等待 QQQ 反转信号）
    # State 2: 反转后持有 TQQQ（一直持有至 QQQ 涨到前高*1.2 获利了结）
    state_holdings = ("QLD", "QLD", "TQQQ")
    cur_state = 0
    cur_nav = 1.0
    tp_target: float | None = None

    out_dates: list[str] = []
    out_values: list[float] = []
    out_holdings: list[str] = []
    out_markers: list[dict[str, Any]] = []

    for i in range(i0, i1):
        if i > i0:
            daily_r = ret_tqqq[i] if cur_state == 2 else ret_qld[i]
            cur_nav *= 1.0 + float(daily_r)
            if cur_nav < 0.0:
                cur_nav = 0.0

        d_str = dates[i]
        out_dates.append(d_str)
        out_values.append(round(cur_nav, 6))
        out_holdings.append(state_holdings[cur_state])

        # 计算当日收盘后的状态转移（下一交易日起生效）
        next_state = cur_state
        marker_info: dict[str, Any] | None = None

        if cur_state == 0:
            if is_bear_sig[i]:
                next_state = 1
        elif cur_state == 1:
            if is_rev_sig[i]:
                next_state = 2
                tp_target = float(qqq_peak[i]) * 1.2
                marker_info = {
                    "date": d_str,
                    "effectiveDate": dates[i + 1] if i + 1 < i1 else d_str,
                    "direction": "up",
                    "color": "#109868",
                    "tag": "→TQQQ",
                    "hud": "反转→TQQQ",
                }
        elif cur_state == 2:
            if tp_target is not None and qqq_close[i] >= tp_target:
                next_state = 0
                marker_info = {
                    "date": d_str,
                    "effectiveDate": dates[i + 1] if i + 1 < i1 else d_str,
                    "direction": "down",
                    "color": "#D97706",
                    "tag": "止盈→QLD",
                    "hud": "止盈→QLD",
                }
                tp_target = None

        if marker_info is not None and i + 1 < i1:
            out_markers.append(marker_info)

        cur_state = next_state

    return {
        "key": STRATEGY_KEY,
        "dates": out_dates,
        "values": out_values,
        "holdings": out_holdings,
        "markers": out_markers,
    }


def describe_strategy(market_series: dict[str, pd.Series]) -> dict[str, Any] | None:
    """返回策略元信息及全区间兜底预览，供目录展示与时间轴范围对齐。"""
    full_eval = evaluate_strategy(market_series, None, None)
    if full_eval is None or not full_eval["dates"]:
        return None

    dates = full_eval["dates"]
    return {
        "key": STRATEGY_KEY,
        "label": STRATEGY_LABEL,
        "kind": "strategy",
        "mode": "remote_strategy",
        "description": STRATEGY_DESC,
        "startDate": dates[0],
        "endDate": dates[-1],
        "count": len(dates),
        "dates": dates,
        "values": full_eval["values"],
    }


def _load_local_close(csv_path: Path) -> pd.Series:
    df = pd.read_csv(csv_path)
    lower = {c.strip().lower(): c for c in df.columns}
    d_col = lower.get("date") or lower.get("日期")
    c_col = lower.get("close") or lower.get("adj_close") or lower.get("收盘")
    if not d_col or not c_col:
        raise ValueError(f"{csv_path} 缺少 date 或 close 列")
    s = pd.Series(
        pd.to_numeric(df[c_col], errors="coerce").values,
        index=pd.to_datetime(df[d_col], errors="coerce").dt.tz_localize(None),
    ).dropna()
    return s[s > 0].sort_index()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 TQQQ/QLD 周线均线+RSI 择时策略回测")
    parser.add_argument("--start", default=None, help="观察窗口开始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="观察窗口结束日期 YYYY-MM-DD")
    args = parser.parse_args()

    stock_dir = Path(__file__).resolve().parent.parent / "stockData"
    market = {
        sym: _load_local_close(stock_dir / f"{sym}.csv")
        for sym in REQUIRED_SYMBOLS
        if (stock_dir / f"{sym}.csv").exists()
    }
    res = evaluate_strategy(market, start_date=args.start, end_date=args.end)
    if res is None or not res["dates"]:
        raise SystemExit("缺少行情数据或所选日期区间无有效交易日")

    vals = np.array(res["values"], dtype=float)
    final_mult = vals[-1] / vals[0]
    max_dd = float((vals / np.maximum.accumulate(vals) - 1.0).min())
    print(f"策略: {STRATEGY_LABEL}")
    print(f"区间: {res['dates'][0]} ~ {res['dates'][-1]} ({len(vals)} 交易日)")
    print(f"最终倍数: {final_mult:.2f}x | 累计收益: {(final_mult - 1) * 100:+.2f}% | 最大回撤: {max_dd * 100:.2f}%")
    print(f"换仓次数: {len(res['markers'])}")
    for m in res["markers"]:
        print(f"  信号日 {m['date']} -> 生效日 {m['effectiveDate']}: {m['hud']}")


if __name__ == "__main__":
    main()
