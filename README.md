# Tesla Notifier

[![Docker Image](https://github.com/MokoYee/tesla-notifier/actions/workflows/build-and-push.yml/badge.svg)](https://github.com/MokoYee/tesla-notifier/actions/workflows/build-and-push.yml)

TeslaMate 推送通知服务 - 轻量级推送服务，通过 [Bark](https://bark.day.app/) 将车辆状态实时推送到 iOS 设备。

## 功能

### 实时推送（MQTT 驱动）

- **行程结束推送** - 行程结束后自动推送详情（起终点、里程、能耗、效率、驾驶评分）
- **充电完成推送** - 充电完成后推送充电详情（充入电量、峰值功率、费用）
- **哨兵模式推送** - 哨兵模式激活/关闭/事件触发时推送

### 定时报告（Cron 驱动）

- **每日简报** - 每天早上推送天气预报 + 昨日驾驶汇总 + 驾驶评分
- **周报** - 每周一推送本周驾驶统计
- **月报** - 每月1日推送上月驾驶统计 + 驾驶评分


## 快速开始

### Docker 部署（推荐）

**使用构建镜像：**

```bash
docker pull ghcr.io/mokoyee/tesla-notifier:latest
```

**或使用 docker-compose：**

1. 创建 `docker-compose.yml`：
```yaml
version: '3'
services:
  tesla-notifier:
    image: ghcr.io/mokoyee/tesla-notifier:latest
    container_name: tesla-notifier
    restart: unless-stopped
    env_file:
      - .env
    networks:
      - teslamate_default

networks:
  teslamate_default:
    external: true
```

2. 复制并编辑配置文件：
```bash
cp .env.example .env
# 编辑 .env 配置 Bark Key 和数据库连接
```

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
| `WEEKLY_CRON` | 周报 cron | 0 9 * * mon |
| `MONTHLY_CRON` | 月报 cron | 0 9 1 * * |
| `BARK_URL` | Bark 服务地址 | https://api.day.app |
| `BARK_KEY` | Bark 推送 Key | - |
| `CAIYUN_TOKEN` | 彩云天气 Token（可选） | - |
| `AMAP_KEY` | 高德地图 Key（可选） | - |
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
  - [申请 Token](https://platform.caiyunapp.com/)
  - [API 文档](https://docs.caiyunapp.com/weather-api/v2/v2.6/1-realtime.html)
- **Open-Meteo**（备用）- 免费，无需配置，彩云失败时自动回退

## 高德地图服务

配置 `AMAP_KEY` 后启用逆地理编码功能，将 GPS 坐标转换为更精确的中文地址。
**申请步骤：**

1. 注册 [高德开放平台](https://lbs.amap.com/) 账号
2. 进入控制台 → 应用管理 → 创建新应用
3. 添加 Key，服务平台选择「Web服务」
4. 将获取的 Key 配置到 `AMAP_KEY` 环境变量

**API 配额：**
- 个人开发者：每日 5000 次免费调用
- 企业认证后可提升配额

**参考文档：**
- [逆地理编码 API](https://lbs.amap.com/api/webservice/guide/api/georegeo)

## 架构

```
Tesla API → TeslaMate → PostgreSQL
                ↓
            MQTT Broker
                ↓
          Tesla Notifier → Bark → iOS
```

## 许可证

MIT
