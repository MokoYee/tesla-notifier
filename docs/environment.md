# 环境变量说明

本文档说明 `tesla-notifier` 当前支持的环境变量。推荐先复制 [`.env.example`](../.env.example) 或直接参考 [`docker-compose.yml`](../docker-compose.yml)。

## 使用约定

- `ENABLE_*` 开关使用 `true/false`
- 告警类开关使用 `ON/OFF`
- 未特别说明时，留空表示使用默认值
- 合并部署到 TeslaMate 默认栈时，数据库和 MQTT 默认值通常无需修改

## 数据库

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `DB_HOST` | `localhost` | 否 | TeslaMate PostgreSQL 主机名 |
| `DB_PORT` | `5432` | 否 | TeslaMate PostgreSQL 端口 |
| `DB_NAME` | `teslamate` | 否 | 数据库名 |
| `DB_USER` | `teslamate` | 否 | 数据库用户名 |
| `DB_PASSWORD` | 空 | 视部署而定 | 数据库密码 |

## MQTT

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `ENABLE_MQTT` | `true` | 否 | 是否启用 MQTT 实时推送链路 |
| `MQTT_URL` | `mqtt://localhost:1883` | `ENABLE_MQTT=true` 时建议配置 | MQTT Broker 地址 |
| `MQTT_USERNAME` | 空 | 否 | MQTT 用户名 |
| `MQTT_PASSWORD` | 空 | 否 | MQTT 密码 |

## 定时任务

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `ENABLE_CRON` | `true` | 否 | 是否启用日报、周报、月报 |
| `DAILY_CRON` | `0 8 * * *` | 否 | 每日简报 cron |
| `WEEKLY_CRON` | `0 9 * * mon` | 否 | 周报 cron |
| `MONTHLY_CRON` | `0 9 1 * *` | 否 | 月报 cron |

## Bark 推送

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `BARK_URL` | `https://api.day.app` | 否 | Bark 服务地址，自建服务时改为自建地址 |
| `BARK_KEY` | 空 | 是 | Bark 推送 key |
| `BARK_ICON` | Tesla Logo URL | 否 | 推送图标 URL |

## 第三方服务

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `CAIYUN_TOKEN` | 空 | 否 | 彩云天气 token，配置后天气信息更完整 |
| `AMAP_KEY` | 空 | 否 | 高德地图 key，用于更准确的中文地址解析 |

## 行程路况分析

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `TRAFFIC_ANALYSIS_ENABLED` | `OFF` | 否 | 是否开启行程中高德路况低频采样，并将结果并入驾驶评分 |
| `TRAFFIC_SAMPLE_INTERVAL` | `300` | 否 | 两次路况采样之间的最小时间间隔（秒） |
| `TRAFFIC_SAMPLE_MIN_DISTANCE_KM` | `3` | 否 | 车辆位移达到该距离时，即使未到时间阈值也会补采一次 |
| `TRAFFIC_QUERY_RADIUS` | `1000` | 否 | 高德交通态势查询半径（米），官方上限 `4999` |

## 车辆与通用配置

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `CAR_ID` | `1` | 否 | TeslaMate 车辆 ID，需与 MQTT / 数据库中的车辆一致 |
| `MIN_TRIP_DISTANCE` | `1` | 否 | 小于该里程的行程不推送 |
| `TZ` | `Asia/Shanghai` | 否 | 应用时区 |
| `STATE_FILE` | `./data/state.json` | 否 | 已推送记录持久化文件路径 |
| `LOG_LEVEL` | `INFO` | 否 | 日志级别，支持 `DEBUG`、`INFO`、`WARNING`、`ERROR` |

## 实时告警开关

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `SENTRY_NOTIFY_ENABLED` | `OFF` | 否 | 是否开启哨兵录制事件通知 |
| `SENTRY_RECORDING_COOLDOWN` | `300` | 否 | 哨兵录制事件防抖秒数 |
| `DEPARTURE_SAFETY_NOTIFY_ENABLED` | `OFF` | 否 | 是否开启离车安全提醒 |
| `DEPARTURE_SAFETY_DELAY` | `45` | 否 | 检测到离车后延迟多少秒执行安全检查 |
| `TPMS_NOTIFY_ENABLED` | `OFF` | 否 | 是否开启胎压异常提醒 |
| `TPMS_NOTIFY_COOLDOWN` | `1800` | 否 | 胎压异常防抖秒数 |
| `CHARGING_ISSUE_NOTIFY_ENABLED` | `OFF` | 否 | 是否开启充电异常提醒 |
| `CHARGING_ISSUE_COOLDOWN` | `900` | 否 | 充电异常防抖秒数 |
| `CHARGING_STOPPED_MIN_SOC_GAP` | `3` | 否 | 当充电变为 `Stopped` 且距离目标 SoC 仍大于该值时，判定为异常停止 |

## 推荐配置示例

```env
ENABLE_MQTT=true
ENABLE_CRON=true

BARK_KEY=your_bark_key
AMAP_KEY=your_amap_key
TRAFFIC_ANALYSIS_ENABLED=ON

SENTRY_NOTIFY_ENABLED=ON
DEPARTURE_SAFETY_NOTIFY_ENABLED=ON
TPMS_NOTIFY_ENABLED=ON
CHARGING_ISSUE_NOTIFY_ENABLED=ON
```

## 说明

- 当前哨兵逻辑不再依赖任何“功率阈值”配置。
- 哨兵录制事件基于 TeslaMate MQTT 实时状态判断，并使用 `SENTRY_RECORDING_COOLDOWN` 防重复推送。
- 如果你只想用实时通知，可以保留 `ENABLE_MQTT=true`，并按需关闭 `ENABLE_CRON`。
- 行程路况分析使用本地 JSON 缓存，不会额外引入数据库；缓存目录默认为 `./data/traffic_snapshots/`。
- 根据高德官方文档，交通态势查询属于高级服务接口；如果 key 没有权限，服务会自动跳过路况采样，不影响基础通知。
