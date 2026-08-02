<h1 align="center">Tesla Notifier</h1>
<p align="center"><a href="README.md">简体中文</a> | English</p>

<p align="center">
  <a href="https://github.com/MokoYee/tesla-notifier/releases"><img src="https://img.shields.io/github/v/release/MokoYee/tesla-notifier?style=flat-square&color=blue" alt="Release"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/stargazers"><img src="https://img.shields.io/github/stars/MokoYee/tesla-notifier?style=flat-square" alt="Stars"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/blob/main/LICENSE"><img src="https://img.shields.io/github/license/MokoYee/tesla-notifier?style=flat-square" alt="License"></a>
  <a href="https://github.com/MokoYee/tesla-notifier/actions/workflows/build.yml"><img src="https://img.shields.io/github/actions/workflow/status/MokoYee/tesla-notifier/build.yml?style=flat-square&label=CI" alt="CI"></a>
</p>

An iPhone notification companion powered by TeslaMate data, delivering trip, charging, sentry, and safety alerts in real time so you can stay on top of your vehicle.

## 📸 Notification Examples
<p align="center">
  <a href=".github/assets/notify-featured.jpg">
    <img src=".github/assets/notify-featured.jpg" alt="Notification examples">
  </a>
</p>

<details>
  <summary>View all examples</summary>
  <table>
    <tr>
      <td width="50%"><img src=".github/assets/notify-trip-and-daily.jpg" alt="Daily briefing and trip summary example"></td>
      <td width="50%"><img src=".github/assets/notify-sentry-cycle.jpg" alt="Sentry notification example"></td>
    </tr>
    <tr>
      <td width="50%"><img src=".github/assets/notify-charging-complete.jpg" alt="Charging complete example"></td>
      <td width="50%"><img src=".github/assets/notify-charging-alerts.jpg" alt="Charging alerts example"></td>
    </tr>
  </table>
</details>

## Features

### Real-time Notifications (MQTT-driven)

- **Trip Summary** - Automatic push after each trip (origin/destination, distance, energy consumption, 100-point driving score, local trip commentary)
- **Charging Complete** - Push notification when charging finishes (energy added, peak power, AC/DC type, charging efficiency)
- **Sentry Mode** - Notifications when sentry mode activates/deactivates, plus realtime recording event alerts
- **Departure Safety Alert** - Detects unlocked car, open windows/doors, open trunks, or an open charge port after leaving the vehicle
- **Tire Pressure Alert** - Realtime tire pressure warning based on TeslaMate `tpms_soft_warning_*`
- **Charging Issue Alert** - Detects `NoPower` or charging stopped early before reaching the target SoC
- **Traffic-aware Trip Analysis** - Optional Amap traffic sampling during a drive, merged into trip scoring and commentary
- **Notification Trust Metadata** - System notifications and logs include event IDs, event type, priority, and trigger reasons for easier troubleshooting

### Scheduled Reports (Cron-driven)

- **Daily Briefing** - Morning push with weather forecast + yesterday's driving summary
- **Weekly Report** - Every Monday with weekly driving statistics
- **Monthly Report** - On the 1st of each month with last month's statistics

## Quick Start

The recommended setup is the one-click installer. It detects your TeslaMate Docker Compose file, reads PostgreSQL, MQTT, and Docker network settings, then creates a separate `tesla-notifier/` deployment directory next to TeslaMate. Your original TeslaMate compose file is not modified.

Run this on the server where TeslaMate is deployed:

```bash
curl -fsSL https://raw.githubusercontent.com/MokoYee/tesla-notifier/main/install.sh | bash
```

The installer will ask for:

- `BARK_KEY`: uses the official Bark service `https://api.day.app` by default
- `AMAP_KEY`: optional; enables Chinese addresses, weather, and traffic-aware trip analysis

If you prefer to inspect the script before running it:

```bash
curl -fsSLO https://raw.githubusercontent.com/MokoYee/tesla-notifier/main/install.sh
bash install.sh
```

After installation, view logs with:

```bash
cd tesla-notifier
docker compose logs -f tesla-notifier
```

Update the image:

```bash
cd tesla-notifier
docker compose pull tesla-notifier && docker compose up -d tesla-notifier
```

<details>
<summary>Manual deployment</summary>

You can use the repository [`docker-compose.yml`](docker-compose.yml) as a standalone deployment template.  
In that mode, notifier must join the Docker external network used by TeslaMate.

If startup says the network does not exist, run:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}'
docker network ls
```

First identify the TeslaMate-related container from the container list, then find the matching `xxx_default` network in the network list.
Replace `teslamate_default` in the standalone template with the actual network name.
</details>

## Configuration

- See [docs/environment_en.md](docs/environment_en.md) for the full environment variable reference
- Common optional features: `AMAP_KEY`, `SENTRY_NOTIFY_ENABLED`, `DEPARTURE_SAFETY_NOTIFY_ENABLED`, `TPMS_NOTIFY_ENABLED`, `CHARGING_ISSUE_NOTIFY_ENABLED`
- Weak-network trip compensation, system health notifications, and push state persistence under `./data` are enabled by default and usually do not need extra setup

## Weather Services

- **Amap Weather** - Configure `AMAP_KEY` to use Chinese weather data based on the current administrative district
- **Open-Meteo** (Fallback) - Free, no configuration needed, auto-fallback when Amap weather is unavailable

## Amap Services

Configure `AMAP_KEY` to enable:

- Reverse geocoding for better Chinese addresses
- Weather lookup for daily briefings
- Traffic-aware trip analysis by sampling Amap traffic status during a drive

**Setup:**

1. Register at [Amap Open Platform](https://lbs.amap.com/)
2. Go to Console → App Management → Create New App
3. Add Key, select "Web Service" as platform
4. Set the key to `AMAP_KEY` environment variable

**Reference:**
- [Reverse Geocoding API](https://lbs.amap.com/api/webservice/guide/api/georegeo)
- [Weather API](https://lbs.amap.com/api/webservice/guide/api/weatherinfo)
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
- This repository is currently licensed under `GPL-3.0`. See [LICENSE](LICENSE) for the full text.
- The actual deployer is responsible for assessing and bearing any compliance, trademark, licensing, or operational risks arising from their usage model.

## License

GPL-3.0
