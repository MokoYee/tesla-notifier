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
| `GRAFANA_BASE_URL` | 空 | 否 | TeslaMate Grafana 访问地址，配置后行程和充电通知可点击进入详情页 |

## 第三方服务

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `AMAP_KEY` | 空 | 否 | 高德地图 key，用于中文地址解析、每日简报天气和可选路况分析 |

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

## 弱网行程补偿

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `TRIP_COMPENSATION_INTERVAL` | `300` | 否 | 后台巡检最近未推送行程的周期，单位秒 |
| `TRIP_OFFLINE_RECONCILE_DELAY` | `960` | 否 | 驾驶中若 MQTT 状态变为 `offline`，延迟多久执行一次离线补偿检查，单位秒 |
| `TRIP_COMPENSATION_MAX_AGE_HOURS` | `24` | 否 | 只补最近多少小时内结束的行程，避免把过久以前的旧行程重新推送 |

## 实时告警开关

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `SENTRY_NOTIFY_ENABLED` | `OFF` | 否 | 是否开启哨兵录制事件通知 |
| `SENTRY_RECORDING_COOLDOWN` | `300` | 否 | 哨兵录制事件防抖秒数 |
| `DEPARTURE_SAFETY_NOTIFY_ENABLED` | `OFF` | 否 | 是否开启离车安全提醒 |
| `DEPARTURE_SAFETY_DELAY` | `180` | 否 | 检测到离车后延迟多少秒执行安全检查，默认拉长到 3 分钟以降低搬东西、插枪等短暂停留场景的误报 |
| `DEPARTURE_SAFETY_COOLDOWN` | `600` | 否 | 离车安全提醒冷却秒数，避免 `is_user_present` 抖动时短时间重复提醒 |
| `TPMS_NOTIFY_ENABLED` | `OFF` | 否 | 是否开启胎压异常提醒 |
| `TPMS_NOTIFY_COOLDOWN` | `1800` | 否 | 胎压异常防抖秒数 |
| `CHARGING_ISSUE_NOTIFY_ENABLED` | `OFF` | 否 | 是否开启充电异常提醒 |
| `CHARGING_ISSUE_COOLDOWN` | `900` | 否 | 充电异常防抖秒数 |
| `CHARGING_NO_POWER_GRACE_PERIOD` | `180` | 否 | 插枪后 `NoPower` 的冷却确认秒数；若 3 分钟内进入 `Starting/Charging`，则不发送无供电异常 |
| `CHARGING_STOPPED_MIN_SOC_GAP` | `3` | 否 | 当充电变为 `Stopped` 且距离目标 SoC 仍大于该值时，判定为异常停止 |

## 系统健康通知

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `SYSTEM_HEALTH_NOTIFY_ENABLED` | `ON` | 否 | 服务启动完成后发送一次链路自检通知 |
| `FAILURE_ALERT_NOTIFY_ENABLED` | `ON` | 否 | 是否开启数据库 / MQTT 关键链路故障告警 |
| `DB_FAILURE_ALERT_THRESHOLD` | `3` | 否 | 数据库连续失败达到该次数后触发系统告警 |
| `MQTT_DISCONNECT_ALERT_AFTER` | `300` | 否 | MQTT 持续断链达到该秒数后触发系统告警 |
| `MQTT_FRESHNESS_MONITOR_ENABLED` | `ON` | 否 | 是否监控 TeslaMate 数据库写入与 MQTT 实时状态是否同步 |
| `MQTT_FRESHNESS_CHECK_INTERVAL` | `300` | 否 | MQTT 新鲜度巡检周期，单位秒 |
| `MQTT_FRESHNESS_STALE_AFTER` | `900` | 否 | 数据库仍有新位置但 MQTT 长时间未更新达到该秒数后触发告警 |
| `MQTT_FRESHNESS_DB_ACTIVE_WINDOW` | `1800` | 否 | 只有数据库最近位置在该窗口内更新时才判定 MQTT 停滞，避免车辆长期离线时误报 |

## 通知可信度规则

### 类型定义

| 类型 | 含义 | 典型来源 |
| --- | --- | --- |
| `事实事件` | 来自 TeslaMate 已确认的实时状态或已落盘记录 | MQTT `state` / `sentry_mode` / `tpms_soft_warning_*`，数据库已结束的行程与充电记录 |
| `分析结果` | 由规则、聚合或评分逻辑推导出的结论 | 每日简报、周报、月报、离车安全风险、行程评分与建议 |
| `系统状态` | 由本插件自身链路检查产生 | 启动自检、数据库故障、MQTT 断链与恢复 |

### 优先级规则

| 优先级 | Bark level | 适用通知 |
| --- | --- | --- |
| `高` | `timeSensitive` | 哨兵录制、胎压异常、充电异常、离车安全、关键链路故障 |
| `中` | `active` | 行程结束、充电完成、哨兵开启/关闭、系统恢复、自检正常 |
| `低` | `passive` | 每日简报、周报、月报 |

### 事件映射

| 通知 | 类型 | 优先级 | 触发依据 |
| --- | --- | --- | --- |
| 行程结束 | `事实事件` | `中` | TeslaMate 已确认 `driving -> online/asleep` 且行程已写入数据库 |
| 充电完成 | `事实事件` | `中` | `charging_state` 结束且充电记录已落盘 |
| 哨兵开启 / 关闭 | `事实事件` | `中` | MQTT `sentry_mode` 状态变化 |
| 哨兵录制 | `事实事件` | `高` | MQTT `center_display_state = 7` |
| 胎压异常 | `事实事件` | `高` | MQTT `tpms_soft_warning_* = true` |
| 充电异常 | `事实事件` | `高` | MQTT `charging_state=NoPower` 或 `Stopped` 且 SoC 未达目标 |
| 离车安全提醒 | `分析结果` | `高` | 离车延迟校验后，门锁 / 车窗 / 备箱 / 充电口等规则命中 |
| 每日 / 周 / 月报 | `分析结果` | `低` | 天气、历史行程、充电数据聚合 |
| 启动自检 / 链路告警 | `系统状态` | `中/高` | 启动探活、数据库连续失败、MQTT 长时间断链或实时数据停滞 |

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

- 哨兵录制事件基于 TeslaMate MQTT 实时状态判断，并使用 `SENTRY_RECORDING_COOLDOWN` 防重复推送。
- 离车安全提醒默认会等待 180 秒再检查，并在 600 秒冷却窗口内抑制重复提醒，更适合下车后仍在后备箱取物、插枪等短暂停留场景。
- 充电异常中的 `NoPower` 默认会等待 180 秒确认，过滤慢充刚插枪时的短暂无供电握手阶段。
- 如果你只想用实时通知，可以保留 `ENABLE_MQTT=true`，并按需关闭 `ENABLE_CRON`。
- MQTT 新鲜度监控会比较数据库 `positions` 最近写入时间和 MQTT 实时消息时间；当数据库仍在更新但 MQTT 长时间停滞时，会发送系统告警。
- 行程路况分析使用本地 JSON 缓存，不会额外引入数据库；缓存目录默认为 `./data/traffic_snapshots/`。
- 行程点评会综合速度、急加速 / 急减速、路况压力以及海拔起伏特征自动调整表达强度。
- 根据高德官方文档，交通态势查询属于高级服务接口；如果 key 没有权限，服务会自动跳过路况采样，不影响基础通知。
- 业务通知默认不展示技术性元数据；事件 ID、类型、优先级与触发依据主要用于日志归因与系统通知排障。
- Bark 自身失败时无法再通过 Bark 反向告警，因此当前策略是记录详细日志，并在链路恢复后继续发送后续通知。
