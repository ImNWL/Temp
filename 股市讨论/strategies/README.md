# 策略目录 (`strategies`)

存放自定义量化与择时策略代码，`n_qqq_new` 启动及刷新行情时会自动扫描并加载本目录下的动态策略（按观察窗口起点实时重算，视角 B），同时也兼容导出到 `../stockData/strategies/*.csv` 的静态净值文件。

## 已内置策略

### 1. `tqqq_qqq_weekly_ma_rsi.py` — **TQQQ/QQQ 周线择时**

- **初始状态**：在当前所选观察窗口首个交易日满仓持有 `QQQ`（未进入熊市前一直持有 `QQQ`，不在牛市中途直接切 `TQQQ`）。
- **熊市判断**：当 `QQQ` 周线收盘跌破周线 `MA40` 且周线 `RSI(14) < 50` 时，判断进入熊市，并记录对应的 `QQQ` 历史前高。
- **反转操作**：在已进入熊市后，当 `QQQ` 周线收盘同时站上 `MA10`、`MA20`、`MA40` 上方时，判断迎来反转，于**下一交易日**起将 `QQQ` 全部转换为 `TQQQ`。
- **获利了结**：持有 `TQQQ` 期间，当 `QQQ` 收盘上涨到「前高 × 1.15」时，于**下一交易日**起将 `TQQQ` 全部转换回 `QQQ`，继续等待下一个熊市周期（若在反转操作之后、获利了结之前又触发了新的熊市判断，则保持持有 `TQQQ`，并将「前高」更新为新触发熊市判断对应的前高）。

### 2. `tqqq_qqq_weekly_bear_guard.py` — **TQQQ/QQQ 周线防熊**

- 在 `TQQQ/QQQ 周线择时` 的基础上增加**熊市风控**：无论当前是否持有 `TQQQ`，只要触发周线熊市判断（`QQQ` 周线收盘 `< MA40` 且 `RSI(14) < 50`），即于下一交易日起全部换回 `QQQ`，记录新的前高并回到熊市等待反转状态；其余反转切 `TQQQ` 与 `前高 × 1.15` 止盈回 `QQQ` 规则完全一致。

### 3. `qld_qqq_weekly_ma_rsi.py` — **QLD/QQQ 周线择时**

- 与 `TQQQ/QQQ 周线择时` 规则完全一致（同样基于 `QQQ` 周线 `MA10/20/40` 与 `RSI(14)` 判断熊市、反转及前高 × 1.15 获利了结，并在反转后二次入熊时刷新前高），区别仅在于熊市反转后转换为 **`QLD`**（2x）而非 `TQQQ`。

### 4. `qld_qqq_weekly_bear_guard.py` — **QLD/QQQ 周线防熊**

- 与 `TQQQ/QQQ 周线防熊` 规则完全一致（周线触发熊市判断时一律于次一交易日切回 `QQQ` 防熊，反转后切换杠杆并在 `前高 × 1.15` 止盈回 `QQQ`），区别仅在于熊市反转后转换为 **`QLD`**（2x）而非 `TQQQ`。

### 5. `tqqq_qqq_daily_sma20_40_rsi.py` — **TQQQ/QQQ 日线反弹**

- **常态底仓**：观察窗口首日及常态下满仓持有 `100% QQQ`（不可空仓，最低满仓 `QQQ`）。
- **两级吃反弹**（全部基于 `QQQ` 日线指标，次一交易日起生效）：近 `30 个交易日`内曾出现 `日线 RSI(14) < 35` 后：
  - **第一级**：`QQQ` 日线收盘站上 `日线 SMA20` 且 `日线 RSI(14) > 50` → 切入 `50% TQQQ + 50% QQQ`（2x）；
  - **第二级**：进一步站上 `日线 SMA40` → 升至 `100% TQQQ`（3x）。
- **离场回 `QQQ`**：一旦 `QQQ` 日线收盘 `< 日线 SMA20`，全部切回 `100% QQQ`。

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
