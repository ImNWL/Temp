# QQQ 杠杆收益率对比

这是一个 Streamlit 小工具，用于对比：

- 自定义 `x` 倍 QQQ 合成曲线
- `QQQ`
- `QLD`
- `TQQQ`

数据完全来自本地文件 `../stockData/QQQ.csv`。自定义 `x` 倍、`QLD` 和 `TQQQ` 曲线都按 QQQ 每日收益乘以目标杠杆并每日再平衡合成，不包含融资成本、管理费、税费、滑点和真实 ETF 跟踪误差。

## 运行

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 说明

这里展示的 `QLD` 约等于每日 2x QQQ 合成曲线，`TQQQ` 约等于每日 3x QQQ 合成曲线。长期收益不会等于 QQQ 长期收益的简单整数倍，因为每日再平衡会受到路径和波动率影响。
