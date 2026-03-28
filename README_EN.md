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

The recommended setup is a standalone `tesla-notifier` container attached to the same Docker network as TeslaMate. In most cases, you only need to edit `.env`, not `docker-compose.yml`.

### 1. Download the templates

```bash
curl -O https://raw.githubusercontent.com/MokoYee/tesla-notifier/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/MokoYee/tesla-notifier/main/.env.example
```

### 2. Edit `.env`

At minimum, set:

- `BARK_KEY`
- `DB_PASSWORD` if your TeslaMate database password is not the default

The default template already matches a standard TeslaMate stack:

- `DB_HOST=database`
- `MQTT_URL=mqtt://mosquitto:1883`

Only change them if your TeslaMate service names or ports are different.

### 3. Adjust the Docker network name if needed

The repository template uses the external network `teslamate_default` by default.  
If your TeslaMate network has a different name, change this block once:

```yaml
networks:
  teslamate_default:
    external: true
```

### 4. Start the service

```bash
docker compose up -d
```

### 5. Update the image

```bash
docker compose pull && docker compose up -d
```

## Quick `.env` Example

```bash
BARK_KEY=your_bark_key

DB_HOST=database
DB_PORT=5432
DB_NAME=teslamate
DB_USER=teslamate
DB_PASSWORD=teslamate

ENABLE_MQTT=true
MQTT_URL=mqtt://mosquitto:1883

ENABLE_CRON=true
DAILY_CRON=0 8 * * *
WEEKLY_CRON=0 9 * * mon
MONTHLY_CRON=0 9 1 * *

CAR_ID=1
TZ=Asia/Shanghai
```

## Configuration

See [docs/environment.md](docs/environment.md) for the full environment variable reference.

Common options:

- `BARK_KEY`: required, your Bark push key
- `CAIYUN_TOKEN`: optional, richer weather details
- `AMAP_KEY`: optional, better Chinese addresses
- `SENTRY_NOTIFY_ENABLED`: optional, sentry notifications
- `DEPARTURE_SAFETY_NOTIFY_ENABLED`: optional, departure safety alerts
- `TPMS_NOTIFY_ENABLED`: optional, tire pressure alerts
- `CHARGING_ISSUE_NOTIFY_ENABLED`: optional, charging issue alerts
- `TRAFFIC_ANALYSIS_ENABLED`: optional, traffic-aware trip analysis

Enabled by default and usually not needed in `.env`:

- weak-network trip compensation
- system health notifications
- push state persistence under `./data`

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
