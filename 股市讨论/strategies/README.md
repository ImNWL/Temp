# 策略目录 (`strategies`)

存放自定义量化与择时策略代码，`n_qqq_new` 启动及刷新行情时会自动扫描并加载本目录下的动态策略（按观察窗口起点实时重算，视角 B），同时也兼容导出到 `../stockData/strategies/*.csv` 的静态净值文件。

## 已内置策略

### 1. `tqqq_qqq_weekly_ma_rsi.py` — **TQQQ/QQQ 周线择时**

- **初始状态**：在当前所选观察窗口首个交易日满仓持有 `QQQ`（未进入熊市前一直持有 `QQQ`，不在牛市中途直接切 `TQQQ`）。
- **熊市判断**：当 `QQQ` 周线收盘跌破周线 `MA40` 且周线 `RSI(14) < 50` 时，判断进入熊市。
- **反转操作**：在已进入熊市后，当 `QQQ` 周线收盘同时站上 `MA10`、`MA20`、`MA40` 上方时，判断迎来反转，于**下一交易日**起将 `QQQ` 全部转换为 `TQQQ`，并锁定当时 `QQQ` 历史前高。
- **获利了结**：持有 `TQQQ` 期间，当 `QQQ` 收盘上涨到「前高 × 1.2」时，于**下一交易日**起将 `TQQQ` 全部转换回 `QQQ`，继续等待下一个熊市周期。

### 2. `tqqq_qld_weekly_ma_rsi.py` — **TQQQ/QLD 周线择时**

- **初始状态**：在当前所选观察窗口首个交易日满仓持有 `QLD`（未进入熊市前一直持有 `QLD`，不在牛市中途直接切 `TQQQ`）。
- **熊市判断**：以 `QQQ` 为信号基准，当 `QQQ` 周线收盘跌破周线 `MA40` 且周线 `RSI(14) < 50` 时，判断进入熊市。
- **反转操作**：在已进入熊市后，当 `QQQ` 周线收盘同时站上 `MA10`、`MA20`、`MA40` 上方时，判断迎来反转，于**下一交易日**起将 `QLD` 全部转换为 `TQQQ`，并锁定当时 `QQQ` 历史前高。
- **获利了结**：持有 `TQQQ` 期间，当 `QQQ` 收盘上涨到「前高 × 1.2」时，于**下一交易日**起将 `TQQQ` 全部转换回 `QLD`，继续等待下一个熊市周期。

命令行单独查看某区间回测与换仓明细：

```bash
../n_qqq_new/.venv/bin/python tqqq_qqq_weekly_ma_rsi.py
../n_qqq_new/.venv/bin/python tqqq_qqq_weekly_ma_rsi.py --start 2021-11-01 --end 2024-06-01
```

## 新增动态策略规范

`n_qqq_new` 只负责纯画图，**所有策略净值、持仓与换仓点标记均由本目录下的 Python 模块实时计算**。

在本目录下新建任意不以 `_` 开头的 `.py` 文件，并实现以下两个函数即可被 `n_qqq_new` 自动识别（修改保存策略文件后无需重启服务，刷新或拖动时间轴自动热加载）：

- `describe_strategy(market_series: dict[str, pd.Series]) -> dict | None`：返回策略 `key`、`label`、`description` 与有效日期列表。
- `evaluate_strategy(market_series: dict[str, pd.Series], start_date: str | None, end_date: str | None) -> dict | None`：按当前观察窗口 `[start_date, end_date]` 实时计算并返回 `dates`、`values`（净值）、`holdings`（每日持仓）与 `markers`（换仓点日期、上下方向、颜色及标签文字）。
