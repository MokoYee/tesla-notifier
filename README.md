<h1 align="center">Tesla Notifier</h1>
<p align="center">简体中文 ｜ <a href="README_EN.md">English</a></p>

<p align="center">
  <a href="https://github.com/MokoYee/tesla-notifier/releases"><img src="https://img.shields.io/github/v/release/MokoYee/tesla-notifier?style=flat-square&color=blue" alt="Release"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/stargazers"><img src="https://img.shields.io/github/stars/MokoYee/tesla-notifier?style=flat-square" alt="Stars"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/blob/main/LICENSE"><img src="https://img.shields.io/github/license/MokoYee/tesla-notifier?style=flat-square" alt="License"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/actions/workflows/build.yml"><img src="https://img.shields.io/github/actions/workflow/status/MokoYee/tesla-notifier/build.yml?style=flat-square&label=CI" alt="CI"></a>
</p>

TeslaMate 车辆状态推送插件，聚焦通知而不是重后台: 行程结束、充电完成、哨兵事件和安全提醒实时推送到 iPhone，让你随时掌握爱车动态。

## 📸 推送示例
<table>
  <tr>
    <td width="25%"><img src=".github/assets/notify-trip-and-daily.jpg" alt="日报与行程结束示例"></td>
    <td width="25%"><img src=".github/assets/notify-charging-complete.jpg" alt="充电完成示例"></td>
    <td width="25%"><img src=".github/assets/notify-charging-alerts.jpg" alt="充电异常示例"></td>
    <td width="25%"><img src=".github/assets/notify-sentry-cycle.jpg" alt="哨兵通知示例"></td>
  </tr>
</table>

## 功能

### 实时推送（MQTT 驱动）

- **行程结束推送** - 行程结束后自动推送详情（起终点、里程、能耗、100 分制驾驶评分、本地匹配行程点评）
- **充电完成推送** - 充电完成后推送充电详情（充入电量、峰值功率、AC/DC 类型、充电效率）
- **哨兵模式推送** - 哨兵模式激活/关闭状态推送，支持实时录制事件提醒
- **离车安全提醒** - 离车后检测未锁车、门窗未关、前后备箱未关、充电口未关
- **胎压异常提醒** - 基于 TeslaMate `tpms_soft_warning_*` 实时推送异常轮位和胎压
- **充电异常提醒** - 检测 `NoPower` 或充电提前停止且未达到目标电量的情况
- **行程路况分析** - 可选接入高德交通态势，低频采样后并入行程评分与点评文案
- **通知可信度元数据** - 系统通知和日志会记录事件 ID、事件类型、优先级和触发依据，便于回溯与排障

### 定时报告（Cron 驱动）

- **每日简报** - 每天早上推送天气预报 + 昨日驾驶汇总
- **周报** - 每周一推送本周驾驶统计
- **月报** - 每月1日推送上月驾驶统计


## 快速开始

推荐直接合并到你现有的 TeslaMate `docker-compose.yml` 中。这样最省心，不需要理解外部网络，也不用单独维护第二份部署文件。

### 1. 下载 `.env` 模板

```bash
curl -o .env https://raw.githubusercontent.com/MokoYee/tesla-notifier/main/.env.example
```

### 2. 编辑 `.env`

- 至少填写 `BARK_KEY`
- 如果你的 TeslaMate 数据库不是默认密码，再修改 `DB_PASSWORD`
- 默认模板已适配 TeslaMate 标准服务名：`DB_HOST=database`、`MQTT_URL=mqtt://mosquitto:1883`

### 3. 在 TeslaMate 的 compose 中加入服务

```yaml
services:
  tesla-notifier:
    image: mokoyee/tesla-notifier:latest
    container_name: tesla-notifier
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./data:/app/data
```

然后启动：

```bash
docker compose up -d tesla-notifier
```

更新镜像：

```bash
docker compose pull tesla-notifier && docker compose up -d tesla-notifier
```

<details>
<summary>如果你更喜欢把 notifier 单独维护</summary>

可以直接使用仓库里的 [`docker-compose.yml`](docker-compose.yml) 作为独立部署模板。  
这种方式需要把 notifier 接入 TeslaMate 所在的 Docker 外部网络。

如果启动时报网络不存在，可先执行：

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}'
docker network ls
```

先从容器列表里找到 TeslaMate 相关容器，再从网络列表里找到对应的 `xxx_default` 网络名称。
把独立模板里的 `teslamate_default` 改成实际网络名即可。
</details>

## 配置说明

- 完整环境变量说明请参考 [docs/environment.md](docs/environment.md)
- 常用可选功能：`AMAP_KEY`、`CAIYUN_TOKEN`、`SENTRY_NOTIFY_ENABLED`、`DEPARTURE_SAFETY_NOTIFY_ENABLED`、`TPMS_NOTIFY_ENABLED`、`CHARGING_ISSUE_NOTIFY_ENABLED`
- 弱网行程补偿、系统健康通知和 `./data` 状态持久化默认已启用，一般无需额外配置

## 天气服务

- **彩云天气**（推荐）- 配置 `CAIYUN_TOKEN` 后使用，支持空气质量、紫外线等数据
  - [申请 Token](https://platform.caiyunapp.com/)
  - [API 文档](https://docs.caiyunapp.com/weather-api/v2/v2.6/1-realtime.html)
- **Open-Meteo**（备用）- 免费，无需配置，彩云失败时自动回退

## 高德地图服务

配置 `AMAP_KEY` 后可启用两类能力：

- 逆地理编码: 将 GPS 坐标转换为更精确的中文地址
- 行程路况分析: 在行程中低频调用高德交通态势接口，汇总后并入行程评分与分析文案

**申请步骤：**

1. 注册 [高德开放平台](https://lbs.amap.com/) 账号
2. 进入控制台 → 应用管理 → 创建新应用
3. 添加 Key，服务平台选择「Web服务」
4. 将获取的 Key 配置到 `AMAP_KEY` 环境变量

**参考文档：**
- [逆地理编码 API](https://lbs.amap.com/api/webservice/guide/api/georegeo)
- [交通态势查询 API](https://lbs.amap.com/api/webservice/guide/api-advanced/traffic-situation-inquiry)

> 说明：逆地理编码通常是标准 Web 服务能力；交通态势查询在高德官方文档中标注为高级服务接口。若你的 key 无权限，本项目会自动降级为“无路况增强”，不会影响主通知。

## 架构

```
Tesla API → TeslaMate → PostgreSQL
                ↓
            MQTT Broker
                ↓
          Tesla Notifier → Bark → iPhone
```

## 免责声明与第三方声明

- 本项目是非官方社区工具，与 TeslaMate 官方项目不存在隶属、授权、赞助或背书关系。
- `TeslaMate` 是其原项目及相关权利人的名称/标识。本项目仅用于兼容说明，不主张相关商标权。
- 本项目默认通过 TeslaMate 已公开的 MQTT / PostgreSQL 数据进行读取与通知，不包含 TeslaMate 官方源码分发。
- 如果你自行修改、分发或通过网络提供修改后的 TeslaMate 实例，请自行遵守 TeslaMate 上游项目的 `AGPL-3.0` 许可证及其商标政策。
- 本仓库当前采用 `GPL-3.0` 许可证，具体条款请参见 [LICENSE](LICENSE)。
- 因上游协议、商标使用、部署方式或合规要求带来的风险，需要由实际部署者自行评估并承担。

## 许可证

GPL-3.0
