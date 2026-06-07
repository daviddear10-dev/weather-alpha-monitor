# Weather Alpha Monitor

最小可用版天气预报监控工具，用来手动抓取配置城市的明日最高温和最低温，并保存到 SQLite 数据库。

## 功能

- 使用 Open-Meteo API 获取配置城市的明日最高温和最低温
- 额外使用香港天文台 Open Data API 获取香港明日最高温和最低温
- 额外使用 NOAA/NWS API 获取纽约、洛杉矶、迈阿密明日最高温和最低温
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
│   ├── markets_draft.json
│   ├── polymarket_candidates.json
│   └── weather_data.json
├── .github/
│   └── workflows/
│       └── weather-monitor.yml
└── weather_monitor/
    ├── __init__.py
    ├── __main__.py
    ├── cities.json
    ├── markets_draft.py
    ├── monitor.py
    ├── nws_official.py
    ├── polymarket_candidates.py
    ├── test_nws_official.py
    ├── test_shenzhen_official.py
    └── test_singapore_nea.py
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

## 如何新增城市

城市配置文件位于：

```text
weather_monitor/cities.json
```

每个城市格式：

```json
{
  "name": "上海",
  "latitude": 31.2304,
  "longitude": 121.4737,
  "timezone": "Asia/Shanghai",
  "enabled": true
}
```

说明：

- `name`：页面和数据库中显示的城市名称
- `latitude` / `longitude`：城市坐标
- `timezone`：城市当地时区，例如 `Asia/Shanghai`、`America/New_York`、`Europe/London`
- `enabled=true`：会抓取该城市
- `enabled=false`：暂时跳过该城市

Open-Meteo 会使用城市自己的 `timezone` 计算该城市当地的明天日期。比如纽约会按 `America/New_York` 的当地日期取明日预报，不会按北京时间取日期。

如果某个城市缺少 `timezone` 或填写了无效时区，会默认使用 `Asia/Shanghai`。如果 `cities.json` 不存在或格式错误，程序会自动回退到默认城市：深圳、香港、北京，避免自动任务崩溃。

香港天文台数据只会在“香港”启用时额外抓取一次。其他城市只使用 Open-Meteo。

## GitHub Actions 自动运行

仓库包含 GitHub Actions 配置：

```text
.github/workflows/weather-monitor.yml
```

它会自动安装依赖并运行：

```bash
python -m weather_monitor
python -m weather_monitor.polymarket_candidates
```

定时运行时间：

| 北京时间 | UTC 时间 | 批次 |
|---|---|---|
| 18:00 | 10:00 | `evening_1800` |
| 20:30 | 12:30 | `evening_2030` |
| 23:00 | 15:00 | `night_2300` |
| 07:00 | 前一天 23:00 | `morning_0700` |

运行完成后，如果天气数据、SQLite 数据库或 Polymarket 候选市场有变化，workflow 会自动提交并推送回仓库；如果没有变化，会正常结束，不会报错。

同时 workflow 会提交：

- `weather_forecasts.sqlite`
- `docs/weather_data.json`
- `docs/polymarket_candidates.json`

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

页面会读取 `weather_data.json`、`markets.json`、`markets_draft.json`、`polymarket_candidates.json` 并展示：

- 统计摘要（天气城市数、正式/草稿/候选市场数量、数据覆盖情况）
- 最新记录表格
- 按城市筛选
- 按数据源筛选
- 明日最高温折线图
- 明日最低温折线图
- 不同批次对比
- Polymarket 决策建议表（仅基于 `markets.json`，作为正式交易参考）
- Polymarket 草稿市场表（`markets_draft.json`，日期已匹配天气数据的候选市场草稿）
- Polymarket 候选市场表（`polymarket_candidates.json`，全部候选市场含日期匹配状态）

正式交易建议只基于 `markets.json`。`markets_draft.json` 和 `polymarket_candidates.json` 仅用于辅助筛选和人工确认，不作为交易建议。

在 GitHub 仓库开启 Pages：

```text
Settings -> Pages -> Build and deployment -> Deploy from a branch
Branch: main
Folder: /docs
```

保存后，GitHub Pages 会发布 `docs/index.html`。每次 GitHub Actions 自动运行并提交新的 `docs/weather_data.json` 或 `docs/polymarket_candidates.json` 后，页面数据会随仓库更新。

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

## Polymarket 候选市场抓取

可以从 Polymarket Gamma API 抓取 weather / temperature 相关候选市场：

```bash
python -m weather_monitor.polymarket_candidates
```

输出文件：

```text
docs/polymarket_candidates.json
```

这个文件只用于辅助发现候选市场，不会自动覆盖 `docs/markets.json`。候选市场需要人工确认 `forecast_date`、`condition`、`threshold`、`yes_price`、城市和结算规则后，再手动写入 `docs/markets.json`。

抓取逻辑：

- 使用 `https://gamma-api.polymarket.com/events`
- 只请求 active 且未关闭的事件
- 筛选文本中包含 `weather`、`temperature`、`high temperature`、`highest temperature`、`max temperature` 的事件/市场
- 再按 `weather_monitor/cities.json` 里的城市名和英文别名匹配城市
- 如果标题或问题里能识别 `30°C`、`30℃`、`86°F`、`86 degrees` 这类盘口，会附加温度盘口字段
- 会尝试从标题、问题和 slug 中解析 `forecast_date`，支持 `on June 6`、`on Jun 6`、`June 6, 2026`、`highest-temperature-in-hong-kong-on-june-6-2026`
- 会根据 `or below`、`or lower`、`at or below` 判断 `condition <=`，根据 `or higher`、`or above`、`at or above` 判断 `condition >=`；没有方向短语时暂按 `=` 处理，并输出 `condition_reason`

日期过滤规则：

- 只保留城市当地「今天」和「明天」的市场，过期市场（昨天及更早）自动丢弃
- 使用 `weather_monitor/cities.json` 中每个城市的 `timezone` 计算当地日期
- 已关闭（`closed=true`）或非活跃（`active=false`）的市场自动过滤
- 解析不出 `forecast_date` 的市场直接丢弃

脚本运行后会输出统计信息：API 原始市场数量、各类过滤数量、最终候选市场数量。

如果 API 请求失败，命令会打印错误并输出空数组，避免程序崩溃。

## Polymarket markets 草稿生成

可以根据候选市场和已有天气数据日期生成 `markets.json` 草稿：

```bash
python -m weather_monitor.markets_draft
```

输入文件：

```text
docs/polymarket_candidates.json
docs/weather_data.json
```

输出文件：

```text
docs/markets_draft.json
```

`markets_draft.json` 是从候选市场和天气数据日期匹配后生成的草稿，不会覆盖 `docs/markets.json`。它只保留城市和预报日期已经存在于 `weather_data.json`、盘口线可解析、市场仍 active 且未 closed 的候选市场。

如果同一个 `city + forecast_date + threshold + condition` 有重复，脚本会保留 `volume24hr` 更高的一条。生成后仍需人工确认 `forecast_date`、`condition`、`threshold`、`yes_price` 和结算规则，再复制到 `docs/markets.json`。

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

## 深圳官方源测试

深圳官方天气源仍处于独立测试阶段，暂未接入 `python -m weather_monitor` 主采集流程。

接入前提：正式 API 需返回目标日期的完整逐时预报（至少 6 个时段且包含至少 2 个白天时段 09:00/11:00/13:00/15:00/17:00），否则脚本会拒绝生成 ForecastRecord。

测试脚本：

```bash
python -m weather_monitor.test_shenzhen_official
```

脚本会尝试：

- 读取深圳市政府数据开放平台接口文档
- 使用 **POST** 请求调用深圳市气象局相关天气预报接口
- 自动附加 `startDate` / `endDate` 参数（深圳当地今天/明天日期，格式 `yyyymmdd`）
- 打印正式 API 返回中所有 FORECASTTIME 日期分布
- 优先解析 `AREANAME="福田区"` 的逐时预报，避免多个区温度混在一起
- 如果没有福田区数据，退回使用全部区域
- 打印目标日期的可用小时列表及完整性检查结果
- 如果目标日期不足 6 个时段或缺少白天预报时段，拒绝生成 ForecastRecord
- 如果解析成功，转换成 `ForecastRecord` 格式并打印

正式 API 请求参数：

- `page=1`
- `rows=10000`
- `appKey`：从环境变量 `SZ_OPEN_DATA_APP_KEY` 读取
- `startDate`：深圳当地今天日期（`yyyymmdd`）
- `endDate`：深圳当地明天日期（`yyyymmdd`）

参考页面：

```text
https://opendata.sz.gov.cn/data/api/toApiDetails/29200_00900269
```

注意：该平台正式 API 通常需要订阅后的 `appKey`。如果已有 appKey，可以这样测试：

```bash
SZ_OPEN_DATA_APP_KEY=你的appKey python -m weather_monitor.test_shenzhen_official
```

如果没有设置 `SZ_OPEN_DATA_APP_KEY`，脚本会跳过正式 API，只尝试预览接口。

如果请求失败或字段不匹配，脚本只会打印错误，不影响现有天气监控程序。

## NOAA/NWS 美国城市官方源测试

NOAA/NWS 官方天气源已接入 `python -m weather_monitor` 主采集流程。运行主程序时，纽约、洛杉矶、迈阿密三城会在 Open-Meteo 之外额外抓取 NOAA/NWS 数据作为第二数据源。NOAA/NWS 获取失败不影响 Open-Meteo 数据和其他城市。独立模块 `weather_monitor/nws_official.py` 提供可复用函数 `fetch_nws_forecast(city_name, latitude, longitude, timezone)`。

测试脚本针对美国三个城市：

- 纽约 (40.7128, -74.0060)，时区 `America/New_York`
- 洛杉矶 (34.0522, -118.2437)，时区 `America/Los_Angeles`
- 迈阿密 (25.7617, -80.1918)，时区 `America/New_York`

运行命令：

```bash
python -m weather_monitor.test_nws_official
```

脚本流程：

1. 调用 `weather_monitor/nws_official.py` 的 `fetch_nws_forecast()`
2. `fetch_nws_forecast()` 内部调用 `https://api.weather.gov/points/{lat},{lon}` 获取网格点元数据
3. 从返回的 `properties.forecast` 获取每日预报 URL
4. 请求每日预报，按 `startTime` 解析每个 period 的当地日期
5. 筛选城市当地明天日期的白天/夜晚时段
6. NWS 默认返回华氏度 (°F)，自动转换为摄氏度 `C = (F - 32) * 5 / 9`
7. 取当天所有时段的温度最低/最高值，组装成 `ForecastRecord`
8. 只打印结果，不写入 SQLite，不接入主采集流程
9. 如果请求失败或字段不匹配，只打印清楚错误，不让程序崩溃

参考页面：

- NWS API 文档: `https://www.weather.gov/documentation/services-web-api`
- 网格点示例: `https://api.weather.gov/points/40.7128,-74.0060`


## 新加坡 NEA/MSS 官方源独立测试

新加坡官方天气源仍处于独立测试阶段，暂未接入 `python -m weather_monitor` 主采集流程。

数据来源：新加坡政府 data.gov.sg 的 4-day Weather Forecast API。

测试脚本：

```bash
python -m weather_monitor.test_singapore_nea
```

脚本流程：

1. 调用 `https://api.data.gov.sg/v1/environment/4-day-weather-forecast`
2. 从返回的 `items[0].forecasts` 中解析 4 天预报
3. 使用 `Asia/Singapore` 时区计算新加坡当地明天日期
4. 从匹配日期的 forecast 中提取 `temperature.low` 和 `temperature.high`
5. 温度默认摄氏度，无需转换
6. 组装 `ForecastRecord`，source 固定为 `NEA/MSS`
7. 只打印结果，不写入 SQLite，不接入主采集流程
8. 请求失败或字段缺失时打印错误并返回 None

参考页面：

- Singapore 4-day Weather Forecast: `https://beta.data.gov.sg/collections/3075/datasets/d_f131f6e343bf8168e4057a04c4326a0a/view`


## 数据源

- Open-Meteo: `https://api.open-meteo.com/v1/forecast`
- 香港天文台: `https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=fnd&lang=sc`
- 深圳市政府数据开放平台: `https://opendata.sz.gov.cn/data/api/toApiDetails/29200_00900269`，独立测试中
- 美国 NOAA/NWS: `https://api.weather.gov`，已接入主流程（纽约、洛杉矶、迈阿密第二数据源）
- 新加坡 NEA/MSS: `https://api.data.gov.sg/v1/environment/4-day-weather-forecast`，独立测试中

## 备注

自动定时任务由 GitHub Actions 执行。GitHub 的 schedule 任务可能会有几分钟延迟，实际抓取批次仍按脚本运行时的北京时间判断。
