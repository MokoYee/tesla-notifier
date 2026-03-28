<h1 align="center">Tesla Notifier</h1>
<p align="center"><a href="README.md">简体中文</a> | English</p>

<p align="center">
  <a href="https://github.com/MokoYee/tesla-notifier/releases"><img src="https://img.shields.io/github/v/release/MokoYee/tesla-notifier?style=flat-square&color=blue" alt="Release"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/stargazers"><img src="https://img.shields.io/github/stars/MokoYee/tesla-notifier?style=flat-square" alt="Stars"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/blob/main/LICENSE"><img src="https://img.shields.io/github/license/MokoYee/tesla-notifier?style=flat-square" alt="License"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/actions/workflows/build.yml"><img src="https://img.shields.io/github/actions/workflow/status/MokoYee/tesla-notifier/build.yml?style=flat-square&label=CI" alt="CI"></a>
</p>

TeslaMate notification companion focused on pushes instead of a heavy backend: get trip, charging, sentry, and safety alerts on your iPhone in real time.

## 📸 Notification Examples

<img src=".github/assets/example.jpg" width="400" alt="Notification Examples">

## Features

### Real-time Notifications (MQTT-driven)

- **Trip Summary** - Automatic push after each trip (origin/destination, distance, energy consumption, 100-point driving score, traffic-aware analysis)
- **Charging Complete** - Push notification when charging finishes (energy added, peak power, cost)
- **Sentry Mode** - Notifications when sentry mode activates/deactivates, plus realtime recording event alerts
- **Departure Safety Alert** - Detects unlocked car, open windows/doors, open trunks, or an open charge port after leaving the vehicle
- **Tire Pressure Alert** - Realtime tire pressure warning based on TeslaMate `tpms_soft_warning_*`
- **Charging Issue Alert** - Detects `NoPower` or charging stopped early before reaching the target SoC
- **Traffic-aware Trip Analysis** - Optional Amap traffic sampling during a drive, merged into trip scoring and narrative analysis
- **Notification Trust Metadata** - System notifications and logs include event IDs, event type, priority, and trigger reasons for easier troubleshooting

### Scheduled Reports (Cron-driven)

- **Daily Briefing** - Morning push with weather forecast + yesterday's driving summary
- **Weekly Report** - Every Monday with weekly driving statistics
- **Monthly Report** - On the 1st of each month with last month's statistics

## Quick Start

### Option 1: Standalone Deployment

1. Download `docker-compose.yml` to the same directory as TeslaMate:

```bash
curl -O https://raw.githubusercontent.com/MokoYee/tesla-notifier/main/docker-compose.yml
```

2. Edit `docker-compose.yml` and configure `BARK_KEY`; add `AMAP_KEY` only if you want better addresses and traffic-aware trip analysis

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
    image: mokoyee/tesla-notifier:latest
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

See [docs/environment.md](docs/environment.md) for the full environment variable reference and notification trust rules, and use [`.env.example`](.env.example) or [docker-compose.yml](docker-compose.yml) as deployment templates.

**Required:**
- `BARK_KEY` - Bark push notification key

**Optional:**
- `CAIYUN_TOKEN` - Caiyun Weather token for detailed weather info (China)
- `AMAP_KEY` - Amap (Gaode) key for accurate Chinese addresses
- `TRAFFIC_ANALYSIS_ENABLED` - Enable low-frequency traffic sampling during trips
- `DEPARTURE_SAFETY_NOTIFY_ENABLED` - Enable departure safety alerts
- `TPMS_NOTIFY_ENABLED` - Enable tire pressure alerts
- `CHARGING_ISSUE_NOTIFY_ENABLED` - Enable charging issue alerts
- `SYSTEM_HEALTH_NOTIFY_ENABLED` - Enable startup self-check notifications
- `FAILURE_ALERT_NOTIFY_ENABLED` - Enable database / MQTT critical path failure alerts

## Weather Services

- **Caiyun Weather** (Recommended for China) - Configure `CAIYUN_TOKEN` for air quality, UV index, etc.
  - [Get Token](https://platform.caiyunapp.com/)
  - [API Docs](https://docs.caiyunapp.com/weather-api/v2/v2.6/1-realtime.html)
- **Open-Meteo** (Fallback) - Free, no configuration needed, auto-fallback when Caiyun fails

## Amap Services

Configure `AMAP_KEY` to enable:

- Reverse geocoding for better Chinese addresses
- Traffic-aware trip analysis by sampling Amap traffic status during a drive

**Setup:**

1. Register at [Amap Open Platform](https://lbs.amap.com/)
2. Go to Console → App Management → Create New App
3. Add Key, select "Web Service" as platform
4. Set the key to `AMAP_KEY` environment variable

**Reference:**
- [Reverse Geocoding API](https://lbs.amap.com/api/webservice/guide/api/georegeo)
- [Traffic Status API](https://lbs.amap.com/api/webservice/guide/api-advanced/traffic-situation-inquiry)

> Note: reverse geocoding is a normal Web Service capability, while traffic status is documented by Amap as an advanced service. If your key has no access, Tesla Notifier will automatically fall back to trip pushes without traffic enhancement.

## Architecture

```
Tesla API → TeslaMate → PostgreSQL
                ↓
            MQTT Broker
                ↓
          Tesla Notifier → Bark → iPhone
```

## Disclaimer and Third-Party Notice

- This project is an unofficial community tool and is not affiliated with, endorsed by, sponsored by, or supported by the official TeslaMate project.
- `TeslaMate` is referenced only for compatibility purposes. All related names and marks remain the property of their respective owners.
- By default, this project only reads data exposed by TeslaMate through MQTT / PostgreSQL and does not redistribute TeslaMate source code.
- If you modify, distribute, or provide a modified TeslaMate instance over a network, you are responsible for complying with TeslaMate's upstream `AGPL-3.0` license and trademark policy.
- This project currently stays on `GPL-3.0`. Switching it straight to `MIT` is not recommended before you fully verify copyright ownership and confirm there is no GPL/AGPL-derived code in the tree.
- The actual deployer is responsible for assessing and bearing any compliance, trademark, licensing, or operational risks arising from their usage model.

## License

GPL-3.0
