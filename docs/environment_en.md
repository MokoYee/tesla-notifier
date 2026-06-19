# Environment Variables

This document describes the environment variables currently supported by `tesla-notifier`.
You can start from [`.env.example`](../.env.example) or use [`docker-compose.yml`](../docker-compose.yml) as a reference.

## Conventions

- `ENABLE_*` flags use `true/false`
- Alert-related switches use `ON/OFF`
- Unless otherwise noted, leaving a variable empty means the default behavior is used
- When deploying inside the default TeslaMate stack, the database and MQTT defaults usually do not need to be changed

## Database

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `DB_HOST` | `localhost` | No | TeslaMate PostgreSQL host |
| `DB_PORT` | `5432` | No | TeslaMate PostgreSQL port |
| `DB_NAME` | `teslamate` | No | Database name |
| `DB_USER` | `teslamate` | No | Database username |
| `DB_PASSWORD` | empty | Depends on deployment | Database password |

## MQTT

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `ENABLE_MQTT` | `true` | No | Whether to enable the MQTT realtime notification pipeline |
| `MQTT_URL` | `mqtt://localhost:1883` | Recommended when `ENABLE_MQTT=true` | MQTT broker URL |
| `MQTT_USERNAME` | empty | No | MQTT username |
| `MQTT_PASSWORD` | empty | No | MQTT password |

## Scheduled Jobs

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `ENABLE_CRON` | `true` | No | Whether to enable daily, weekly, and monthly reports |
| `DAILY_CRON` | `0 8 * * *` | No | Daily briefing cron |
| `WEEKLY_CRON` | `0 9 * * mon` | No | Weekly report cron |
| `MONTHLY_CRON` | `0 9 1 * *` | No | Monthly report cron |

## Bark Push

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `BARK_URL` | `https://api.day.app` | No | Bark service URL. Use your self-hosted endpoint if applicable |
| `BARK_KEY` | empty | Yes | Bark push key |
| `BARK_ICON` | Tesla logo URL | No | Push icon URL |
| `GRAFANA_BASE_URL` | empty | No | TeslaMate Grafana base URL. When set, trip and charging notifications link to detail dashboards |

## Third-Party Services

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `AMAP_KEY` | empty | No | Amap key used for Chinese address resolution, daily briefing weather, and optional traffic analysis |

## Trip Traffic Analysis

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `TRAFFIC_ANALYSIS_ENABLED` | `OFF` | No | Whether to enable low-frequency Amap traffic sampling during a trip and merge it into driving scoring |
| `TRAFFIC_SAMPLE_INTERVAL` | `300` | No | Minimum interval between two traffic samples, in seconds |
| `TRAFFIC_SAMPLE_MIN_DISTANCE_KM` | `3` | No | If the vehicle moves at least this far, an extra sample is taken even if the time threshold is not reached |
| `TRAFFIC_QUERY_RADIUS` | `1000` | No | Amap traffic query radius in meters, official upper limit is `4999` |

## Vehicle and General Settings

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `CAR_ID` | `1` | No | TeslaMate vehicle ID. Must match the vehicle used in MQTT and PostgreSQL |
| `MIN_TRIP_DISTANCE` | `1` | No | Trips shorter than this distance will not be pushed |
| `DRIVING_COMMENTARY_STYLE` | `normal` | No | Trip commentary style. Supported values: `normal` / `aggressive` |
| `TZ` | `Asia/Shanghai` | No | Application timezone |
| `STATE_FILE` | `./data/state.json` | No | Persistence file path for already-pushed events |
| `LOG_LEVEL` | `INFO` | No | Log level. Supported values: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Weak-Network Trip Compensation

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `TRIP_COMPENSATION_INTERVAL` | `300` | No | Background polling interval for recently finished but not yet pushed trips, in seconds |
| `TRIP_OFFLINE_RECONCILE_DELAY` | `960` | No | When MQTT state changes to `offline` during driving, delay before running one offline reconciliation check, in seconds |
| `TRIP_COMPENSATION_MAX_AGE_HOURS` | `24` | No | Only compensate trips that ended within this many hours, to avoid replaying stale trips |

## Realtime Alert Switches

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `SENTRY_NOTIFY_ENABLED` | `OFF` | No | Whether to enable sentry recording event notifications |
| `SENTRY_RECORDING_COOLDOWN` | `300` | No | Cooldown for sentry recording notifications, in seconds |
| `DEPARTURE_SAFETY_NOTIFY_ENABLED` | `OFF` | No | Whether to enable departure safety alerts |
| `DEPARTURE_SAFETY_DELAY` | `180` | No | Delay before running the departure safety check, in seconds. The default is stretched to 3 minutes to reduce false alerts while unloading items or plugging in |
| `DEPARTURE_SAFETY_COOLDOWN` | `600` | No | Cooldown for departure safety alerts, in seconds, to suppress repeated alerts when `is_user_present` bounces |
| `TPMS_NOTIFY_ENABLED` | `OFF` | No | Whether to enable tire pressure alerts |
| `TPMS_NOTIFY_COOLDOWN` | `1800` | No | Cooldown for tire pressure alerts, in seconds |
| `CHARGING_ISSUE_NOTIFY_ENABLED` | `OFF` | No | Whether to enable charging issue alerts |
| `CHARGING_ISSUE_COOLDOWN` | `900` | No | Cooldown for charging issue alerts, in seconds |
| `CHARGING_NO_POWER_GRACE_PERIOD` | `180` | No | Grace period for `NoPower` after plugging in. If charging reaches `Starting/Charging` within 3 minutes, the no-power alert is suppressed |
| `CHARGING_STOPPED_MIN_SOC_GAP` | `3` | No | When charging changes to `Stopped` and the target SoC is still farther away than this gap, it is treated as an abnormal stop |

## System Health Notifications

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `SYSTEM_HEALTH_NOTIFY_ENABLED` | `ON` | No | Send one startup self-check notification after the service finishes booting |
| `FAILURE_ALERT_NOTIFY_ENABLED` | `ON` | No | Whether to enable critical database / MQTT failure alerts |
| `DB_FAILURE_ALERT_THRESHOLD` | `3` | No | Trigger a system alert after this many consecutive database failures |
| `MQTT_DISCONNECT_ALERT_AFTER` | `300` | No | Trigger a system alert when MQTT remains disconnected for this many seconds |
| `MQTT_FRESHNESS_MONITOR_ENABLED` | `ON` | No | Whether to monitor TeslaMate PostgreSQL writes against MQTT realtime state freshness |
| `MQTT_FRESHNESS_CHECK_INTERVAL` | `300` | No | MQTT freshness check interval, in seconds |
| `MQTT_FRESHNESS_STALE_AFTER` | `900` | No | Trigger an alert after MQTT remains stale for this many seconds while the database still receives new positions |
| `MQTT_FRESHNESS_DB_ACTIVE_WINDOW` | `1800` | No | Only treat MQTT as stale when the latest database position is inside this active window, to avoid false alerts while the vehicle is inactive |

## Notification Confidence Rules

### Type Definitions

| Type | Meaning | Typical Sources |
| --- | --- | --- |
| `fact` | Realtime states or persisted records already confirmed by TeslaMate | MQTT `state` / `sentry_mode` / `tpms_soft_warning_*`, finished trips and charging sessions from PostgreSQL |
| `analysis` | Results inferred by rules, aggregation, or scoring logic | Daily briefing, weekly/monthly reports, departure safety risk, trip scoring and commentary |
| `system` | Health or pipeline status produced by this plugin itself | Startup self-check, database failure, MQTT disconnection and recovery |

### Priority Rules

| Priority | Bark level | Typical Notifications |
| --- | --- | --- |
| `high` | `timeSensitive` | Sentry recording, tire pressure alerts, charging issues, departure safety, critical pipeline failures |
| `medium` | `active` | Trip end, charging complete, sentry on/off, system recovery, startup self-check |
| `low` | `passive` | Daily briefing, weekly report, monthly report |

### Event Mapping

| Notification | Type | Priority | Trigger Basis |
| --- | --- | --- | --- |
| Trip End | `fact` | `medium` | TeslaMate confirms `driving -> online/asleep` and the trip record has been persisted |
| Charging Complete | `fact` | `medium` | `charging_state` ends and the charging record has been persisted |
| Sentry On / Off | `fact` | `medium` | MQTT `sentry_mode` state change |
| Sentry Recording | `fact` | `high` | MQTT `center_display_state = 7` |
| Tire Pressure Alert | `fact` | `high` | MQTT `tpms_soft_warning_* = true` |
| Charging Issue | `fact` | `high` | MQTT `charging_state=NoPower` or `Stopped` while SoC is still below target |
| Departure Safety Alert | `analysis` | `high` | Rule match after delayed departure check for lock / windows / trunks / charge port |
| Daily / Weekly / Monthly Reports | `analysis` | `low` | Aggregated weather, trip history, and charging data |
| Startup Self-check / Pipeline Alert | `system` | `medium/high` | Startup probes, consecutive database failures, long MQTT disconnection, or stale realtime data |

## Recommended Example

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

## Notes

- Sentry recording notifications are based on TeslaMate MQTT realtime state and use `SENTRY_RECORDING_COOLDOWN` to prevent duplicates.
- Departure safety alerts now wait 180 seconds by default and apply a 600-second cooldown window, which is better suited to short stays around the vehicle while unloading items or plugging in.
- `NoPower` charging alerts now wait 180 seconds by default to filter out short handshake phases right after an AC charging cable is plugged in.
- If you only want realtime notifications, keep `ENABLE_MQTT=true` and disable `ENABLE_CRON` as needed.
- MQTT freshness monitoring compares the latest `positions` write time with MQTT realtime message time; if the database keeps updating while MQTT stalls, a system alert is sent.
- Trip traffic analysis uses a local JSON cache and does not introduce any extra database dependency. The default cache directory is `./data/traffic_snapshots/`.
- `DRIVING_COMMENTARY_STYLE` defaults to `normal` and is intentionally not added to `.env.example`; only set it explicitly if you want the more outspoken `aggressive` style.
- Trip commentary is matched locally using speed, hard acceleration / hard braking, traffic pressure, and elevation-change features.
- According to Amap's official documentation, traffic status queries are part of its advanced service set. If your key does not have access, the service will automatically skip traffic sampling and continue sending normal trip notifications.
- User-facing notifications do not show technical metadata by default. Event IDs, event types, priority, and trigger reasons are mainly used for logs and troubleshooting.
- If Bark itself fails, Bark cannot be used to report its own failure. The current strategy is to keep detailed logs and continue delivering later notifications once the pipeline recovers.
