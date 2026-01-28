<h1 align="center">Tesla Notifier</h1>
<p align="center">简体中文 ｜ <a href="README_EN.md">English</a></p>

<p align="center">
  <a href="https://github.com/MokoYee/tesla-notifier/releases"><img src="https://img.shields.io/github/v/release/MokoYee/tesla-notifier?style=flat-square&color=blue" alt="Release"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/stargazers"><img src="https://img.shields.io/github/stars/MokoYee/tesla-notifier?style=flat-square" alt="Stars"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/blob/main/LICENSE"><img src="https://img.shields.io/github/license/MokoYee/tesla-notifier?style=flat-square" alt="License"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/actions/workflows/build.yml"><img src="https://img.shields.io/github/actions/workflow/status/MokoYee/tesla-notifier/build.yml?style=flat-square&label=CI" alt="CI"></a>
</p>

TeslaMate 车辆状态推送插件 - 行程结束、充电完成、哨兵事件实时通知到 iPhone，让你随时掌握爱车动态。

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

### 方式一：独立部署

1. 下载 `docker-compose.yml` 到 TeslaMate 同级目录：

```bash
curl -O https://raw.githubusercontent.com/MokoYee/tesla-notifier/main/docker-compose.yml
```

2. 编辑 `docker-compose.yml`，修改 `BARK_KEY` 等必要配置（详见文件内注释）

3. 启动服务：

```bash
docker-compose up -d
```

### 方式二：合并到 TeslaMate

在 TeslaMate 的 `docker-compose.yml` 中添加以下服务：

```yaml
services:
  # ... 其他 TeslaMate 服务 ...

  tesla-notifier:
    image: ghcr.io/mokoyee/tesla-notifier:latest
    container_name: tesla-notifier
    restart: unless-stopped
    environment:
      - ENABLE_MQTT=true
      - ENABLE_CRON=true
      - BARK_KEY=your_bark_key  # 必填：替换为你的 Bark Key
      # 更多配置项参考：https://github.com/MokoYee/tesla-notifier
```

> 合并部署时无需配置数据库和 MQTT 地址，默认值已适配 TeslaMate 标准配置。

### 更新镜像

```bash
docker-compose pull && docker-compose up -d
```

## 配置说明

完整配置项请参考 [docker-compose.yml](docker-compose.yml) 文件内的注释。

**必填配置：**
- `BARK_KEY` - Bark 推送密钥

**可选配置：**
- `CAIYUN_TOKEN` - 彩云天气 Token，提供更详细的天气信息
- `AMAP_KEY` - 高德地图 Key，提供更精确的中文地址

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
          Tesla Notifier → Bark → iPhone
```

## 许可证

GPL-3.0
