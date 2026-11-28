"""
TQQQ / QQQ 日线 SMA20/40 + RSI(14) 两级平滑反弹策略（方案 B，纯后端实时计算模块）

策略规则（全部基于 QQQ 日线收盘计算，次一交易日起生效，最低满仓 QQQ）：
1. 常态底仓：在当前所选观察窗口首个交易日及常态下，满仓持有 100% QQQ。
2. 两级吃反弹：近 30 个交易日内曾出现超卖（日线 RSI(14) < 35）：
   - 第一级：当 QQQ 日线收盘 > 日线 SMA20 且 日线 RSI(14) > 50 时，切入 50% TQQQ + 50% QQQ（2x）；
   - 第二级：进一步站上 日线 SMA40（QQQ > 日线 SMA40）时，升至 100% TQQQ（3x）
     （若进场当天已同时高于 日线 SMA20 与 日线 SMA40，则一步到位切入 100% TQQQ）。
3. 离场回 QQQ：持有杠杆仓位期间，一旦 QQQ 日线收盘 < 日线 SMA20，全部切回 100% QQQ。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

STRATEGY_KEY = "strategy:tqqq_qqq_daily_sma20_40_rsi"
STRATEGY_LABEL = "TQQQ/QQQ 日线反弹"
STRATEGY_DESC = "最低满仓QQQ；近30日RSI14<35后站上日线SMA20且RSI>50转半仓TQQQ，站上SMA40转满仓TQQQ，跌破SMA20回QQQ"
REQUIRED_SYMBOLS = ("QQQ", "TQQQ")

_PREPARED_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def compute_wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """标准 Wilder 平滑日线 RSI。"""
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


def _get_prepared_context(market_series: dict[str, pd.Series]) -> dict[str, Any] | None:
    if not all(sym in market_series for sym in REQUIRED_SYMBOLS):
        return None

    qqq_full = market_series["QQQ"].sort_index().dropna()
    tqqq_full = market_series["TQQQ"].sort_index().dropna()
    if qqq_full.empty or tqqq_full.empty:
        return None

    cache_key = (
        len(qqq_full),
        qqq_full.index[-1],
        float(qqq_full.iloc[-1]),
        len(tqqq_full),
        tqqq_full.index[-1],
        float(tqqq_full.iloc[-1]),
    )
    cached = _PREPARED_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # 基于 QQQ 全历史计算日线 SMA20、日线 SMA40、日线 RSI(14) 及近 30 日超卖标记
    sma20_full = qqq_full.rolling(20, min_periods=20).mean()
    sma40_full = qqq_full.rolling(40, min_periods=40).mean()
    rsi14_full = compute_wilder_rsi(qqq_full, period=14)
    was_oversold_full = rsi14_full.rolling(30, min_periods=1).min() < 35.0

    aligned = pd.DataFrame(
        {
            "QQQ": qqq_full,
            "TQQQ": tqqq_full,
            "SMA20": sma20_full,
            "SMA40": sma40_full,
            "RSI14": rsi14_full,
            "WAS_OVERSOLD": was_oversold_full,
        }
    ).dropna(subset=["QQQ", "TQQQ"])
    aligned = aligned[(aligned["QQQ"] > 0) & (aligned["TQQQ"] > 0)]
    if len(aligned) < 2:
        return None

    ctx = {
        "dates": aligned.index.strftime("%Y-%m-%d").tolist(),
        "qqq_close": aligned["QQQ"].to_numpy(dtype=float),
        "sma20": aligned["SMA20"].to_numpy(dtype=float),
        "sma40": aligned["SMA40"].to_numpy(dtype=float),
        "rsi14": aligned["RSI14"].to_numpy(dtype=float),
        "was_oversold": aligned["WAS_OVERSOLD"].fillna(False).to_numpy(dtype=bool),
        "ret_qqq": aligned["QQQ"].pct_change().fillna(0.0).to_numpy(dtype=float),
        "ret_tqqq": aligned["TQQQ"].pct_change().fillna(0.0).to_numpy(dtype=float),
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


def _holding_label(weight_tqqq: float) -> str:
    if weight_tqqq >= 0.99:
        return "TQQQ"
    if weight_tqqq >= 0.49:
        return "50%TQQQ"
    return "QQQ"


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
    sma20 = ctx["sma20"]
    sma40 = ctx["sma40"]
    rsi14 = ctx["rsi14"]
    was_oversold = ctx["was_oversold"]
    ret_qqq = ctx["ret_qqq"]
    ret_tqqq = ctx["ret_tqqq"]

    # cur_w 表示 TQQQ 仓位权重：0.0 = 100% QQQ, 0.5 = 50% TQQQ + 50% QQQ, 1.0 = 100% TQQQ
    cur_w = 0.0
    cur_nav = 1.0

    out_dates: list[str] = []
    out_values: list[float] = []
    out_holdings: list[str] = []
    out_markers: list[dict[str, Any]] = []

    for i in range(i0, i1):
        if i > i0:
            daily_r = cur_w * float(ret_tqqq[i]) + (1.0 - cur_w) * float(ret_qqq[i])
            cur_nav *= 1.0 + daily_r
            if cur_nav < 0.0:
                cur_nav = 0.0

        d_str = dates[i]
        out_dates.append(d_str)
        out_values.append(round(cur_nav, 6))
        out_holdings.append(_holding_label(cur_w))

        # 当日收盘后判断目标仓位（下一交易日起生效）
        p = qqq_close[i]
        s20 = sma20[i]
        s40 = sma40[i]
        r14 = rsi14[i]

        if np.isnan(s20) or p < s20:
            next_w = 0.0
        elif cur_w > 0.0:
            if not np.isnan(s40) and p > s40:
                next_w = 1.0
            else:
                next_w = cur_w
        else:
            if was_oversold[i] and not np.isnan(r14) and r14 > 50.0:
                next_w = 1.0 if (not np.isnan(s40) and p > s40) else 0.5
            else:
                next_w = 0.0

        if abs(next_w - cur_w) > 1e-6 and i + 1 < i1:
            eff_date = dates[i + 1]
            q_px = float(p)
            if next_w == 0.5:
                out_markers.append(
                    {
                        "date": d_str,
                        "effectiveDate": eff_date,
                        "qqqPrice": round(q_px, 2),
                        "direction": "up",
                        "color": "#22C55E",
                        "hollow": True,
                        "tag": "→半仓TQQQ",
                        "hud": "反弹→50%TQQQ",
                    }
                )
            elif next_w == 1.0:
                action_prefix = "加满" if cur_w > 0.0 else "反转"
                out_markers.append(
                    {
                        "date": d_str,
                        "effectiveDate": eff_date,
                        "qqqPrice": round(q_px, 2),
                        "direction": "up",
                        "color": "#065F46",
                        "hollow": False,
                        "tag": "→TQQQ",
                        "hud": f"{action_prefix}→TQQQ",
                    }
                )
            else:
                out_markers.append(
                    {
                        "date": d_str,
                        "effectiveDate": eff_date,
                        "qqqPrice": round(q_px, 2),
                        "direction": "down",
                        "color": "#E11D48",
                        "tag": "→QQQ",
                        "hud": "破SMA20→QQQ",
                    }
                )

        cur_w = next_w

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
    parser = argparse.ArgumentParser(description="运行 TQQQ/QQQ 日线 SMA20/40 + RSI(14) 两级平滑反弹策略回测")
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
