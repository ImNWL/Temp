from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
DEFAULT_SYMBOLS = ("QQQ", "QLD", "TQQQ")
DEFAULT_FULL_START_DATE = "2015-01-01"

# Eastmoney US market prefixes: 105=NASDAQ, 106=NYSE, 107=NYSE Arca / AMEX
KNOWN_EM_CODES: dict[str, str] = {
    "QQQ": "105.QQQ",
    "TQQQ": "105.TQQQ",
    "SQQQ": "105.SQQQ",
    "QLD": "107.QLD",
    "QID": "107.QID",
    "SPY": "107.SPY",
    "UPRO": "107.UPRO",
    "SOXL": "107.SOXL",
    "AAPL": "105.AAPL",
    "MSFT": "105.MSFT",
    "NVDA": "105.NVDA",
    "TSLA": "105.TSLA",
    "AMZN": "105.AMZN",
    "GOOG": "105.GOOG",
    "META": "105.META",
}
EM_MARKET_PREFIXES = ("105", "107", "106")


@dataclass
class FetchResult:
    symbol: str
    success: bool
    source: str
    mode: str = "incremental"
    rows: int = 0
    added_rows: int = 0
    start_date: str = ""
    end_date: str = ""
    output_path: str = ""
    error: str = ""


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"缺少必要列: {missing}")

    out = df[required].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=["date", "close"])
    out = out[out["close"] > 0]
    out["date"] = out["date"].dt.tz_localize(None)
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out.reset_index(drop=True)


def _fetch_from_eastmoney(
    symbol: str,
    start_date: str = DEFAULT_FULL_START_DATE,
    end_date: str = "22220101",
) -> pd.DataFrame:
    import akshare as ak

    em_start = start_date.replace("-", "")
    symbol_upper = symbol.upper().strip()
    candidates: list[str] = []
    if symbol_upper in KNOWN_EM_CODES:
        candidates.append(KNOWN_EM_CODES[symbol_upper])
    for prefix in EM_MARKET_PREFIXES:
        code = f"{prefix}.{symbol_upper}"
        if code not in candidates:
            candidates.append(code)

    last_exc: Exception | None = None
    for em_code in candidates:
        try:
            raw = ak.stock_us_hist(
                symbol=em_code,
                period="daily",
                start_date=em_start,
                end_date=end_date,
                adjust="qfq",
            )
            if raw is None or raw.empty:
                continue

            renamed = raw.rename(
                columns={
                    "日期": "date",
                    "开盘": "open",
                    "收盘": "close",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                }
            )
            return _normalize_ohlcv(renamed)
        except Exception as exc:  # pragma: no cover - network dependent
            last_exc = exc
            continue

    raise RuntimeError(f"Eastmoney 无法拉取 {symbol_upper}: {last_exc or '无返回数据'}")


def _fetch_from_yfinance(symbol: str, start_date: str = DEFAULT_FULL_START_DATE) -> pd.DataFrame:
    import yfinance as yf

    symbol_upper = symbol.upper().strip()
    ticker = yf.Ticker(symbol_upper)
    raw = ticker.history(start=start_date, interval="1d", auto_adjust=True)
    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance 返回空数据: {symbol_upper}")

    raw = raw.reset_index()
    col_map = {c.lower(): c for c in raw.columns}
    date_col = col_map.get("date") or col_map.get("datetime")
    if not date_col:
        raise ValueError(f"yfinance 返回缺少日期列: {list(raw.columns)}")

    standardized = pd.DataFrame(
        {
            "date": raw[date_col],
            "open": raw[col_map["open"]],
            "high": raw[col_map["high"]],
            "low": raw[col_map["low"]],
            "close": raw[col_map["close"]],
            "volume": raw[col_map["volume"]],
        }
    )
    return _normalize_ohlcv(standardized)


def _merge_incremental(existing_df: pd.DataFrame, new_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Merge newly fetched rows onto existing data, rescaling history if a recent split/dividend shifted qfq."""
    common_dates = sorted(set(existing_df["date"]) & set(new_df["date"]))
    base = existing_df.copy()
    if common_dates:
        anchor_date = common_dates[-1]
        old_close = float(base.loc[base["date"] == anchor_date, "close"].iloc[-1])
        new_close = float(new_df.loc[new_df["date"] == anchor_date, "close"].iloc[-1])
        if old_close > 0 and new_close > 0:
            factor = new_close / old_close
            if abs(factor - 1.0) > 1e-4:
                for col in ("open", "high", "low", "close"):
                    base[col] = base[col] * factor

    merged = pd.concat([base, new_df], ignore_index=True)
    merged = _normalize_ohlcv(merged)
    added_rows = max(len(merged) - len(existing_df), 0)
    return merged, added_rows


def fetch_symbol(
    symbol: str,
    data_dir: Path | str = DATA_DIR,
    source: str = "auto",
    full: bool = False,
) -> FetchResult:
    symbol_upper = symbol.upper().strip()
    target_dir = Path(data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / f"{symbol_upper}.csv"

    existing_df: pd.DataFrame | None = None
    fetch_start = DEFAULT_FULL_START_DATE
    actual_mode = "full"

    if not full and output_path.exists():
        try:
            loaded = _normalize_ohlcv(pd.read_csv(output_path))
            if not loaded.empty:
                existing_df = loaded
                last_dt = pd.to_datetime(loaded["date"].iloc[-1])
                fetch_start = (last_dt - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
                actual_mode = "incremental"
        except Exception:
            existing_df = None
            fetch_start = DEFAULT_FULL_START_DATE
            actual_mode = "full"

    sources_to_try = ["eastmoney", "yfinance"] if source == "auto" else [source]

    errors: list[str] = []
    for src in sources_to_try:
        try:
            if src == "eastmoney":
                df_fetched = _fetch_from_eastmoney(symbol_upper, start_date=fetch_start)
            elif src == "yfinance":
                df_fetched = _fetch_from_yfinance(symbol_upper, start_date=fetch_start)
            else:
                raise ValueError(f"未知数据源: {src}")

            if df_fetched.empty:
                raise ValueError("清洗后数据为空")

            if existing_df is not None and actual_mode == "incremental":
                final_df, added_rows = _merge_incremental(existing_df, df_fetched)
            else:
                final_df = df_fetched
                added_rows = len(final_df)

            final_df.to_csv(output_path, index=False)
            return FetchResult(
                symbol=symbol_upper,
                success=True,
                source=src,
                mode=actual_mode,
                rows=len(final_df),
                added_rows=added_rows,
                start_date=str(final_df["date"].iloc[0]),
                end_date=str(final_df["date"].iloc[-1]),
                output_path=str(output_path),
            )
        except Exception as exc:
            errors.append(f"{src}: {exc}")

    return FetchResult(
        symbol=symbol_upper,
        success=False,
        source=source,
        mode=actual_mode,
        output_path=str(output_path),
        error=" | ".join(errors),
    )


def fetch_symbols(
    symbols: Iterable[str] = DEFAULT_SYMBOLS,
    data_dir: Path | str = DATA_DIR,
    source: str = "auto",
    full: bool = False,
) -> list[FetchResult]:
    return [fetch_symbol(sym, data_dir=data_dir, source=source, full=full) for sym in symbols]


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取美股 ETF 官方前复权日线数据并保存至 stockData/")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_SYMBOLS),
        help="需要拉取的标的代码，默认 QQQ QLD TQQQ",
    )
    parser.add_argument(
        "--source",
        choices=("auto", "eastmoney", "yfinance"),
        default="auto",
        help="数据源优先级，默认 auto（优先 eastmoney 前复权，失败回退 yfinance）",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=f"全量拉取（自 {DEFAULT_FULL_START_DATE} 起）；不加则默认增量更新",
    )
    parser.add_argument(
        "--dir",
        default=str(DATA_DIR),
        help="CSV 保存目录，默认当前 stockData 目录",
    )
    args = parser.parse_args()

    results = fetch_symbols(symbols=args.symbols, data_dir=args.dir, source=args.source, full=args.full)
    has_failure = False
    for res in results:
        if res.success:
            print(
                f"[OK] {res.symbol:5s} | mode={res.mode:11s} | source={res.source:9s} | "
                f"rows={res.rows:5d} (+{res.added_rows}) | {res.start_date} ~ {res.end_date} -> {res.output_path}"
            )
        else:
            has_failure = True
            print(f"[FAIL] {res.symbol:5s} | {res.error}")

    return 1 if has_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
