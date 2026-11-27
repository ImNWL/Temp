from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
STOCK_DATA_DIR = APP_DIR.parent / "stockData"
FETCH_SCRIPT_PATH = STOCK_DATA_DIR / "fetch_data.py"


@dataclass
class DatasetEntry:
    key: str
    kind: str  # "market" | "strategy"
    path: Path
    dates: list[str]
    values: list[float]


class AddTickerRequest(BaseModel):
    raw: str


class RefreshTickerRequest(BaseModel):
    symbols: list[str]
    full: bool = False


def _load_fetcher_module():
    spec = importlib.util.spec_from_file_location("stockdata_fetch", FETCH_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载数据拉取模块: {FETCH_SCRIPT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_symbol_tokens(raw_text: str) -> list[str]:
    tokens = [t.strip().upper() for t in re.split(r"[,，\s]+", raw_text.strip()) if t.strip()]
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return seen


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
    if series.empty:
        raise ValueError(f"{csv_path.name} 无有效正数价格/净值记录")

    is_ohlcv = {"open", "high", "low", "close"}.issubset(set(lower_map.keys()))
    kind = "strategy" if (force_strategy or not is_ohlcv) else "market"

    date_strs = series.index.strftime("%Y-%m-%d").tolist()
    float_vals = [round(float(v), 6) for v in series.values]
    return DatasetEntry(
        key=name,
        kind=kind,
        path=csv_path,
        dates=date_strs,
        values=float_vals,
    )


def scan_catalog(auto_bootstrap: bool = True) -> dict[str, Any]:
    catalog: dict[str, DatasetEntry] = {}
    parse_errors: list[str] = []

    def _scan_once() -> None:
        catalog.clear()
        parse_errors.clear()
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
                catalog[key] = entry
            except Exception as exc:
                parse_errors.append(f"{csv_path.name}: {exc}")

    _scan_once()
    if not catalog and auto_bootstrap:
        try:
            fetcher = _load_fetcher_module()
            fetcher.fetch_symbols(("QQQ", "QLD", "TQQQ"), data_dir=STOCK_DATA_DIR)
            _scan_once()
        except Exception as exc:
            parse_errors.append(f"自动拉取默认数据失败: {exc}")

    priority = ["QQQ", "QLD", "TQQQ"]
    ordered_keys: list[str] = [k for k in priority if k in catalog]
    ordered_keys.extend(k for k in catalog.keys() if k not in ordered_keys)

    items = []
    for k in ordered_keys:
        entry = catalog[k]
        items.append(
            {
                "key": entry.key,
                "kind": entry.kind,
                "startDate": entry.dates[0],
                "endDate": entry.dates[-1],
                "count": len(entry.dates),
                "dates": entry.dates,
                "values": entry.values,
            }
        )

    return {
        "items": items,
        "parseErrors": parse_errors,
    }


app = FastAPI(title="策略对比", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.get("/api/catalog")
def api_get_catalog() -> dict[str, Any]:
    return scan_catalog(auto_bootstrap=True)


@app.post("/api/tickers/add")
def api_add_tickers(req: AddTickerRequest) -> dict[str, Any]:
    tokens = _parse_symbol_tokens(req.raw)
    if not tokens:
        raise HTTPException(status_code=400, detail="请输入有效的股票或 ETF 代码")

    try:
        fetcher = _load_fetcher_module()
        results = fetcher.fetch_symbols(tokens, data_dir=STOCK_DATA_DIR, full=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"拉取异常：{exc}") from exc

    ok = [r.symbol for r in results if r.success]
    failed = [{"symbol": r.symbol, "error": r.error} for r in results if not r.success]
    return {
        "ok": ok,
        "failed": failed,
        "catalog": scan_catalog(auto_bootstrap=False),
    }


@app.post("/api/tickers/refresh")
def api_refresh_tickers(req: RefreshTickerRequest) -> dict[str, Any]:
    current = scan_catalog(auto_bootstrap=False)
    market_keys = {item["key"] for item in current["items"] if item["kind"] == "market"}
    targets = [s for s in req.symbols if s in market_keys]
    if not targets:
        raise HTTPException(status_code=400, detail="当前未选中可更新的正式行情标的")

    try:
        fetcher = _load_fetcher_module()
        results = fetcher.fetch_symbols(targets, data_dir=STOCK_DATA_DIR, full=req.full)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新异常：{exc}") from exc

    updated = [
        {"symbol": r.symbol, "addedRows": r.added_rows, "rows": r.rows}
        for r in results
        if r.success
    ]
    failed = [{"symbol": r.symbol, "error": r.error} for r in results if not r.success]
    return {
        "mode": "full" if req.full else "incremental",
        "updated": updated,
        "failed": failed,
        "catalog": scan_catalog(auto_bootstrap=False),
    }


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _run_server() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="启动策略对比 Web 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8501")), help="端口，默认 8501")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"\n  策略对比已启动: {url}\n")
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    if "streamlit" in sys.modules:
        import streamlit as st

        st.warning("本项目已升级为独立高性能 Web 应用，请在终端运行：`python3 app.py`")
    else:
        _run_server()
