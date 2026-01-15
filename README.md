# Tesla Notifier

TeslaMate 推送通知服务 - 独立部署的轻量级推送服务。

## 功能

- **行程结束推送** - 通过 MQTT 监听车辆状态，行程结束后自动推送
- **充电完成推送** - 充电完成后自动推送充电详情
- **每日简报** - 每天早上推送天气和昨日驾驶汇总
- **周报/月报** - 定时推送驾驶统计报告

## 快速开始

### Docker 部署（推荐）

1. 复制配置文件：
```bash
cp .env.example .env
```

2. 编辑 `.env` 配置 Bark Key 和数据库连接

3. 启动服务：
```bash
docker-compose up -d
```

### 本地运行

1. 安装依赖：
```bash
pip install -e .
```

2. 配置环境变量（或创建 `.env` 文件）

3. 运行：
```bash
python -m tesla_notifier.main
```

## 配置说明

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `DB_HOST` | PostgreSQL 主机 | localhost |
| `DB_PORT` | PostgreSQL 端口 | 5432 |
| `DB_NAME` | 数据库名 | teslamate |
| `DB_USER` | 数据库用户 | teslamate |
| `DB_PASSWORD` | 数据库密码 | - |
| `ENABLE_MQTT` | 启用 MQTT 订阅 | false |
| `MQTT_URL` | MQTT 服务器地址 | mqtt://localhost:1883 |
| `ENABLE_CRON` | 启用定时任务 | false |
| `DAILY_CRON` | 每日简报 cron | 0 8 * * * |
| `WEEKLY_CRON` | 周报 cron | 0 9 * * 1 |
| `MONTHLY_CRON` | 月报 cron | 0 9 1 * * |
| `BARK_URL` | Bark 服务地址 | https://api.day.app |
| `BARK_KEY` | Bark 推送 Key | - |
| `CAIYUN_TOKEN` | 彩云天气 Token（可选） | - |
| `CAR_ID` | 车辆 ID | 1 |
| `MIN_TRIP_DISTANCE` | 最小行程距离(km) | 1 |
| `TZ` | 时区 | Asia/Shanghai |

## 与 TeslaMate 集成

确保 `docker-compose.yml` 中的网络配置正确，使服务能够访问 TeslaMate 的数据库和 MQTT 服务。

```yaml
networks:
  teslamate_default:
    external: true
```

## 天气服务

- **彩云天气**（推荐）- 配置 `CAIYUN_TOKEN` 后使用，支持空气质量、紫外线等数据
- **Open-Meteo**（备用）- 免费，无需配置，自动回退

## 许可证

MIT
