<h1 align="center">Tesla Notifier</h1>
<p align="center"><a href="README.md">简体中文</a> | English</p>

<p align="center">
  <a href="https://github.com/MokoYee/tesla-notifier/releases"><img src="https://img.shields.io/github/v/release/MokoYee/tesla-notifier?style=flat-square&color=blue" alt="Release"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/stargazers"><img src="https://img.shields.io/github/stars/MokoYee/tesla-notifier?style=flat-square" alt="Stars"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/blob/main/LICENSE"><img src="https://img.shields.io/github/license/MokoYee/tesla-notifier?style=flat-square" alt="License"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/actions/workflows/build.yml"><img src="https://img.shields.io/github/actions/workflow/status/MokoYee/tesla-notifier/build.yml?style=flat-square&label=CI" alt="CI"></a>
</p>

A TeslaMate push notification plugin - Get real-time notifications for trip completion, charging status, and sentry events on your iPhone.

## Features

### Real-time Notifications (MQTT-driven)

- **Trip Summary** - Automatic push after each trip (origin/destination, distance, energy consumption, efficiency, driving score)
- **Charging Complete** - Push notification when charging finishes (energy added, peak power, cost)
- **Sentry Mode** - Notifications when sentry mode activates/deactivates or events are triggered

### Scheduled Reports (Cron-driven)

- **Daily Briefing** - Morning push with weather forecast + yesterday's driving summary + driving score
- **Weekly Report** - Every Monday with weekly driving statistics
- **Monthly Report** - On the 1st of each month with last month's statistics + driving score

## Quick Start

### Option 1: Standalone Deployment

1. Download `docker-compose.yml` to the same directory as TeslaMate:

```bash
curl -O https://raw.githubusercontent.com/MokoYee/tesla-notifier/main/docker-compose.yml
```

2. Edit `docker-compose.yml` and configure `BARK_KEY` and other required settings (see comments in file)

3. Start the service:

```bash
docker-compose up -d
```

### Option 2: Merge with TeslaMate

Add the following service to your TeslaMate `docker-compose.yml`:

```yaml
services:
  # ... other TeslaMate services ...

  tesla-notifier:
    image: ghcr.io/mokoyee/tesla-notifier:latest
    container_name: tesla-notifier
    restart: unless-stopped
    environment:
      - ENABLE_MQTT=true
      - ENABLE_CRON=true
      - BARK_KEY=your_bark_key  # Required: Replace with your Bark Key
      # More options: https://github.com/MokoYee/tesla-notifier
```

> When merging, no need to configure database or MQTT addresses - defaults are compatible with standard TeslaMate setup.

### Update Image

```bash
docker-compose pull && docker-compose up -d
```

## Configuration

See [docker-compose.yml](docker-compose.yml) for all configuration options with comments.

**Required:**
- `BARK_KEY` - Bark push notification key

**Optional:**
- `CAIYUN_TOKEN` - Caiyun Weather token for detailed weather info (China)
- `AMAP_KEY` - Amap (Gaode) key for accurate Chinese addresses

## Weather Services

- **Caiyun Weather** (Recommended for China) - Configure `CAIYUN_TOKEN` for air quality, UV index, etc.
  - [Get Token](https://platform.caiyunapp.com/)
  - [API Docs](https://docs.caiyunapp.com/weather-api/v2/v2.6/1-realtime.html)
- **Open-Meteo** (Fallback) - Free, no configuration needed, auto-fallback when Caiyun fails

## Amap Geocoding Service

Configure `AMAP_KEY` to enable reverse geocoding, converting GPS coordinates to accurate Chinese addresses.

**Setup:**

1. Register at [Amap Open Platform](https://lbs.amap.com/)
2. Go to Console → App Management → Create New App
3. Add Key, select "Web Service" as platform
4. Set the key to `AMAP_KEY` environment variable

**API Quota:**
- Personal developers: 5,000 free calls per day
- Enterprise verification increases quota

**Reference:**
- [Reverse Geocoding API](https://lbs.amap.com/api/webservice/guide/api/georegeo)

## Architecture

```
Tesla API → TeslaMate → PostgreSQL
                ↓
            MQTT Broker
                ↓
          Tesla Notifier → Bark → iPhone
```

## License

GPL-3.0
