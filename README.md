# Weather Alpha Monitor

最小可用版天气预报监控工具，用来手动抓取深圳、香港、北京明日最高温和最低温，并保存到 SQLite 数据库。

## 功能

- 使用 Open-Meteo API 获取深圳、香港、北京明日最高温和最低温
- 额外使用香港天文台 Open Data API 获取香港明日最高温和最低温
- 每次运行保存结果到 SQLite
- 每次运行导出最近 100 条记录到 `docs/weather_data.json`
- 每次抓取会根据北京时间自动标记批次 `forecast_run_label`
- 每次运行输出一张表，包含：
  - 抓取时间
  - 批次
  - 城市
  - 数据源
  - 预报日期
  - 明日最低温
  - 明日最高温
  - 数据更新时间

## 项目结构

```text
weather-alpha-monitor/
├── README.md
├── requirements.txt
├── weather_forecasts.sqlite
├── docs/
│   ├── index.html
│   ├── markets.json
│   └── weather_data.json
├── .github/
│   └── workflows/
│       └── weather-monitor.yml
└── weather_monitor/
    ├── __init__.py
    ├── __main__.py
    └── monitor.py
```

## 安装

```bash
cd /Users/a122/Documents/weather-alpha-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 手动运行一次

```bash
python -m weather_monitor
```

默认数据库文件会保存在：

```text
/Users/a122/Documents/weather-alpha-monitor/weather_forecasts.sqlite
```

也可以指定数据库路径：

```bash
python -m weather_monitor --db ./data/weather.sqlite
```

抓取批次规则按当前北京时间判断：

- `17:00-19:30`：`evening_1800`
- `20:00-21:30`：`evening_2030`
- `22:30-23:59`：`night_2300`
- `06:00-08:00`：`morning_0700`
- 其他时间：`manual`

## GitHub Actions 自动运行

仓库包含 GitHub Actions 配置：

```text
.github/workflows/weather-monitor.yml
```

它会自动安装依赖并运行：

```bash
python -m weather_monitor
```

定时运行时间：

| 北京时间 | UTC 时间 | 批次 |
|---|---|---|
| 18:00 | 10:00 | `evening_1800` |
| 20:30 | 12:30 | `evening_2030` |
| 23:00 | 15:00 | `night_2300` |
| 07:00 | 前一天 23:00 | `morning_0700` |

运行完成后，如果 `weather_forecasts.sqlite` 有变化，workflow 会自动提交并推送回仓库；如果没有变化，会正常结束，不会报错。

同时 workflow 会提交：

- `weather_forecasts.sqlite`
- `docs/weather_data.json`

也可以在 GitHub 页面手动触发：

```text
Actions -> Weather Monitor -> Run workflow
```

## GitHub Pages 可视化页面

静态页面文件位于：

```text
docs/index.html
```

数据文件位于：

```text
docs/weather_data.json
```

页面会读取 `weather_data.json` 并展示：

- 最新记录表格
- 按城市筛选
- 按数据源筛选
- 明日最高温折线图
- 明日最低温折线图
- 不同批次对比
- Polymarket 决策建议表

在 GitHub 仓库开启 Pages：

```text
Settings -> Pages -> Build and deployment -> Deploy from a branch
Branch: main
Folder: /docs
```

保存后，GitHub Pages 会发布 `docs/index.html`。每次 GitHub Actions 自动运行并提交新的 `docs/weather_data.json` 后，页面数据会随仓库更新。

## Polymarket 决策建议

页面会读取盘口配置：

```text
docs/markets.json
```

示例：

```json
[
  {
    "city": "香港",
    "forecast_date": "2026-06-07",
    "metric": "max_temp",
    "market_question": "香港 2026-06-07 最高温是否达到 30℃？",
    "threshold": 30,
    "condition": ">=",
    "yes_price": 0.48
  }
]
```

字段说明：

- `city`：城市，需要和 `weather_data.json` 中的城市一致
- `forecast_date`：预报日期
- `metric`：`max_temp` 或 `min_temp`
- `market_question`：市场问题
- `threshold`：盘口温度线
- `condition`：目前支持 `>=`、`>`、`<=`、`<`
- `yes_price`：YES 当前价格，手动填写

决策逻辑：

- 页面按 `city + forecast_date` 找到最新预测，并按数据源去重
- 多数据源时计算最低温范围、最高温范围、平均最低温、平均最高温、数据源数量和数据源分歧
- `metric=max_temp` 使用平均最高温判断，`metric=min_temp` 使用平均最低温判断
- 单数据源时固定显示暂不交易，不直接给 YES/NO 方向；理由为“只有一个数据源，缺少交叉验证，不给交易方向。”
- 数据源分歧 `>= 3℃`：暂不交易，低置信度
- 只有数据源数量 `>= 2` 时，预测值高于盘口至少 `1.5℃`：偏 YES
- 只有数据源数量 `>= 2` 时，预测值低于盘口至少 `1.5℃`：偏 NO
- 距离盘口小于 `1.5℃`：暂不交易

仓位建议：

- 高置信度：小仓
- 中置信度：观察 / 极小仓
- 低置信度：0，不交易

如果 `docs/markets.json` 不存在或为空，页面会显示：

```text
请先配置 docs/markets.json。
```

## 查询最近 20 条记录

```bash
python -m weather_monitor --show
```

这个命令只查询 SQLite 数据库，不会抓取新的天气数据。输出字段包括：

- 抓取时间
- 批次
- 城市
- 数据源
- 预报日期
- 最低温
- 最高温
- 更新时间

## 多源对比

```bash
python -m weather_monitor --compare
```

这个命令只读取 SQLite 历史记录，不会抓取新的天气数据。它会按「预报日期 + 城市」分组，对比同一个城市、同一天、不同数据源的最低温和最高温。

输出字段包括：

- 预报日期
- 城市
- 数据源数量
- 最低温范围
- 最高温范围
- 最低温差值
- 最高温差值
- 可信度

可信度规则：

- 最低温差值和最高温差值都 `<= 1℃`：可信度高
- 任意一个差值超过 `1℃` 且小于 `3℃`：中等
- 任意一个差值 `>= 3℃`：分歧大
- 只有一个数据源：数据源不足

## 查看数据库

```bash
sqlite3 weather_forecasts.sqlite
```

进入 SQLite 后：

```sql
.headers on
.mode column
select * from weather_forecasts order by id desc limit 20;
```

## 数据源

- Open-Meteo: `https://api.open-meteo.com/v1/forecast`
- 香港天文台: `https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=fnd&lang=sc`

## 备注

自动定时任务由 GitHub Actions 执行。GitHub 的 schedule 任务可能会有几分钟延迟，实际抓取批次仍按脚本运行时的北京时间判断。
