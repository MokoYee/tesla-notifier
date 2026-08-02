#!/usr/bin/env bash

set -Eeuo pipefail

IMAGE_NAME="${TESLA_NOTIFIER_IMAGE:-mokoyee/tesla-notifier:latest}"
INSTALL_DIR_NAME="${TESLA_NOTIFIER_DIR_NAME:-tesla-notifier}"
OFFICIAL_BARK_URL="https://api.day.app"
AMAP_APPLY_URL="https://console.amap.com/dev/key/app"
AMAP_WEATHER_URL="https://restapi.amap.com/v3/weather/weatherInfo"

WRITE_STARTED=0
INSTALL_DIR=""

on_interrupt() {
  echo
  if [[ "${WRITE_STARTED}" == "1" && -n "${INSTALL_DIR}" ]]; then
    echo "已中断。TeslaMate 原 docker-compose 未被修改。"
    echo "如需清理本次生成目录，可执行：rm -rf ${INSTALL_DIR}"
  else
    echo "已取消，未修改任何文件。"
  fi
  exit 130
}

trap on_interrupt INT TERM

info() {
  printf '\033[1;34m[INFO]\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33m[WARN]\033[0m %s\n' "$*"
}

fail() {
  printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"
}

detect_compose_command() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
    return 0
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
    return 0
  fi

  fail "未找到 Docker Compose。请先安装 docker compose 或 docker-compose。"
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

strip_bark_key() {
  local value
  value="$(trim "$1")"
  value="${value%/}"

  case "${value}" in
    https://api.day.app/*)
      value="${value#https://api.day.app/}"
      ;;
    http://api.day.app/*)
      value="${value#http://api.day.app/}"
      ;;
  esac

  value="${value%%/*}"
  printf '%s' "${value}"
}

yaml_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "${value}"
}

yaml_quote_literal() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\$\$}"
  printf '"%s"' "${value}"
}

strip_unquoted_comment() {
  local value="$1"
  local quote="" previous="" current="" output=""
  local i

  for ((i = 0; i < ${#value}; i++)); do
    current="${value:i:1}"

    if [[ -n "${quote}" ]]; then
      output+="${current}"
      if [[ "${current}" == "${quote}" && "${previous}" != "\\" ]]; then
        quote=""
      fi
    else
      case "${current}" in
        "'" | '"')
          quote="${current}"
          output+="${current}"
          ;;
        "#")
          if [[ -z "${output}" || "${output: -1}" =~ [[:space:]] ]]; then
            break
          fi
          output+="${current}"
          ;;
        *)
          output+="${current}"
          ;;
      esac
    fi

    previous="${current}"
  done

  trim "${output}"
}

normalize_compose_value() {
  local value
  value="$(strip_unquoted_comment "$1")"
  value="$(trim "${value}")"

  if (( ${#value} >= 2 )); then
    if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi

  printf '%s' "${value}"
}

http_get_amap_weather() {
  local key="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 8 --get \
      --data-urlencode "key=${key}" \
      --data-urlencode "city=110000" \
      --data-urlencode "extensions=base" \
      "${AMAP_WEATHER_URL}"
    return $?
  fi

  if command -v wget >/dev/null 2>&1; then
    local encoded_key
    encoded_key="$(printf '%s' "${key}" | sed 's/%/%25/g; s/+/%2B/g; s/&/%26/g; s/#/%23/g; s/?/%3F/g')"
    wget -qO- --timeout=8 \
      "${AMAP_WEATHER_URL}?city=110000&extensions=base&key=${encoded_key}"
    return $?
  fi

  return 127
}

validate_amap_key() {
  local key="$1"
  local response

  if ! response="$(http_get_amap_weather "${key}" 2>/dev/null)"; then
    warn "无法完成高德 Key 测试请求，已跳过 AMAP_KEY。"
    return 1
  fi

  if printf '%s' "${response}" | grep -q '"status"[[:space:]]*:[[:space:]]*"1"'; then
    info "高德 Key 测试通过。"
    return 0
  fi

  local info_text infocode
  info_text="$(printf '%s' "${response}" | sed -n 's/.*"info"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
  infocode="$(printf '%s' "${response}" | sed -n 's/.*"infocode"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
  warn "高德 Key 验证失败，已跳过 AMAP_KEY。${info_text:+ info=${info_text}}${infocode:+ infocode=${infocode}}"
  return 1
}

discover_compose_files() {
  local roots=()
  local root
  roots+=("${PWD}")
  for root in /data /opt /srv /home /root; do
    [[ -d "${root}" ]] && roots+=("${root}")
  done

  find "${roots[@]}" \
    -maxdepth 5 \
    -type f \
    \( -name 'docker-compose.yml' -o -name 'docker-compose.yaml' -o -name 'compose.yml' -o -name 'compose.yaml' \) \
    2>/dev/null \
    | awk '!seen[$0]++' \
    | while IFS= read -r file; do
        if grep -Eq 'teslamate/teslamate|^[[:space:]]*teslamate:' "${file}" \
          && grep -Eq 'postgres|database:|mosquitto|MQTT_HOST|env_file' "${file}"; then
          printf '%s\n' "${file}"
        fi
      done
}

choose_compose_file() {
  local candidates=()
  local candidate input index

  while IFS= read -r candidate; do
    candidates+=("${candidate}")
  done < <(discover_compose_files)

  if (( ${#candidates[@]} == 1 )); then
    printf '%s' "${candidates[0]}"
    return 0
  fi

  if (( ${#candidates[@]} > 1 )); then
    echo "检测到多个疑似 TeslaMate docker-compose 文件："
    for index in "${!candidates[@]}"; do
      printf '  %d) %s\n' "$((index + 1))" "${candidates[index]}"
    done
    echo
    read -r -p "请选择要接入的文件编号，或直接输入 compose 文件路径: " input
    input="$(trim "${input}")"
    [[ -z "${input}" ]] && fail "未选择 docker-compose 文件，已取消。"

    if [[ "${input}" =~ ^[0-9]+$ ]] && (( input >= 1 && input <= ${#candidates[@]} )); then
      printf '%s' "${candidates[input - 1]}"
      return 0
    fi

    [[ -f "${input}" ]] || fail "文件不存在：${input}"
    printf '%s' "${input}"
    return 0
  fi

  read -r -p "未自动找到 TeslaMate docker-compose 文件，请输入文件路径: " input
  input="$(trim "${input}")"
  [[ -z "${input}" ]] && fail "未输入 docker-compose 文件路径，已取消。"
  [[ -f "${input}" ]] || fail "文件不存在：${input}"
  printf '%s' "${input}"
}

extract_compose_value() {
  local key="$1"
  local file="$2"
  local line value

  line="$(grep -E "^[[:space:]]*(-[[:space:]]*)?${key}[[:space:]]*[:=]" "${file}" | head -n 1 || true)"
  [[ -n "${line}" ]] || return 1

  line="$(printf '%s' "${line}" | sed -E 's/^[[:space:]]*-[[:space:]]*//')"
  value="$(printf '%s' "${line}" | sed -E "s/^[[:space:]]*${key}[[:space:]]*[:=][[:space:]]*//")"
  value="$(normalize_compose_value "${value}")"
  printf '%s' "${value}"
}

read_dotenv_value() {
  local env_file="$1"
  local key="$2"
  local line value

  [[ -f "${env_file}" ]] || return 1
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "${env_file}" | tail -n 1 || true)"
  [[ -n "${line}" ]] || return 1

  value="$(printf '%s' "${line}" | sed -E "s/^[[:space:]]*(export[[:space:]]+)?${key}=//")"
  value="$(normalize_compose_value "${value}")"
  printf '%s' "${value}"
}

read_first_dotenv_value() {
  local env_file="$1"
  shift
  local key value

  for key in "$@"; do
    value="$(read_dotenv_value "${env_file}" "${key}" || true)"
    if [[ -n "${value}" ]]; then
      printf '%s' "${value}"
      return 0
    fi
  done

  return 1
}

resolve_compose_value() {
  local raw_value="$1"
  local compose_dir="$2"
  local variable_name default_value resolved

  if [[ "${raw_value}" =~ ^\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}$ ]]; then
    variable_name="${BASH_REMATCH[1]}"
    default_value="${BASH_REMATCH[3]:-}"
    resolved="${!variable_name:-}"
    if [[ -z "${resolved}" ]]; then
      resolved="$(read_dotenv_value "${compose_dir}/.env" "${variable_name}" || true)"
    fi
    printf '%s' "${resolved:-${default_value}}"
    return 0
  fi

  if [[ "${raw_value}" =~ ^\$([A-Za-z_][A-Za-z0-9_]*)$ ]]; then
    variable_name="${BASH_REMATCH[1]}"
    resolved="${!variable_name:-}"
    if [[ -z "${resolved}" ]]; then
      resolved="$(read_dotenv_value "${compose_dir}/.env" "${variable_name}" || true)"
    fi
    printf '%s' "${resolved}"
    return 0
  fi

  printf '%s' "${raw_value}"
}

extract_top_level_value() {
  local key="$1"
  local file="$2"
  local line value

  line="$(grep -E "^${key}[[:space:]]*:" "${file}" | head -n 1 || true)"
  [[ -n "${line}" ]] || return 1

  value="$(printf '%s' "${line}" | sed -E "s/^${key}[[:space:]]*:[[:space:]]*//")"
  normalize_compose_value "${value}"
}

detect_named_service() {
  local service="$1"
  local file="$2"
  if grep -Eq "^[[:space:]]+${service}:" "${file}"; then
    printf '%s' "${service}"
    return 0
  fi
  return 1
}

detect_service_name() {
  local file="$1"
  shift
  local service

  for service in "$@"; do
    if detect_named_service "${service}" "${file}" >/dev/null; then
      printf '%s' "${service}"
      return 0
    fi
  done

  return 1
}

detect_project_name() {
  local compose_file="$1"
  local compose_dir configured_name
  compose_dir="$(basename "$(dirname "${compose_file}")")"

  configured_name="${COMPOSE_PROJECT_NAME:-}"
  if [[ -z "${configured_name}" ]]; then
    configured_name="$(read_dotenv_value "$(dirname "${compose_file}")/.env" COMPOSE_PROJECT_NAME || true)"
  fi
  if [[ -n "${configured_name}" ]]; then
    printf '%s' "${configured_name//[^a-zA-Z0-9_-]/}"
    return 0
  fi

  configured_name="$(extract_top_level_value name "${compose_file}" || true)"
  if [[ -n "${configured_name}" ]]; then
    configured_name="$(resolve_compose_value "${configured_name}" "$(dirname "${compose_file}")")"
  fi
  if [[ -n "${configured_name}" ]]; then
    printf '%s' "${configured_name//[^a-zA-Z0-9_-]/}"
    return 0
  fi

  printf '%s' "${compose_dir//[^a-zA-Z0-9_-]/}"
}

detect_network_name() {
  local compose_file="$1"
  local project_name="$2"
  local network container_name container_id

  if command -v docker >/dev/null 2>&1; then
    for container_name in teslamate "${project_name}-teslamate-1" "${project_name}_teslamate_1"; do
      network="$(docker inspect "${container_name}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' 2>/dev/null | head -n 1 || true)"
      if [[ -n "${network}" ]]; then
        printf '%s' "${network}"
        return 0
      fi
    done

    container_id="$(docker ps -q \
      --filter "label=com.docker.compose.project=${project_name}" \
      --filter "label=com.docker.compose.service=teslamate" \
      | head -n 1 || true)"
    if [[ -n "${container_id}" ]]; then
      network="$(docker inspect "${container_id}" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' 2>/dev/null | head -n 1 || true)"
      if [[ -n "${network}" ]]; then
        printf '%s' "${network}"
        return 0
      fi
    fi

    for network in "${project_name}_default" "${project_name}-default"; do
      if docker network inspect "${network}" >/dev/null 2>&1; then
        printf '%s' "${network}"
        return 0
      fi
    done
  fi

  printf '%s_default' "${project_name}"
}

ensure_docker_network_exists() {
  local network_name="$1"

  if docker network inspect "${network_name}" >/dev/null 2>&1; then
    return 0
  fi

  fail "未找到 Docker 网络：${network_name}。请先在 TeslaMate 目录执行 docker compose up -d，或确认 compose 项目名是否正确。"
}

prompt_with_default() {
  local prompt="$1"
  local default_value="$2"
  local input

  if [[ -n "${default_value}" ]]; then
    read -r -p "${prompt} [${default_value}]: " input
    input="$(trim "${input}")"
    printf '%s' "${input:-${default_value}}"
  else
    read -r -p "${prompt}: " input
    trim "${input}"
  fi
}

write_compose_file() {
  local target_file="$1"
  local bark_key="$2"
  local amap_key="$3"
  local db_host="$4"
  local db_port="$5"
  local db_name="$6"
  local db_user="$7"
  local db_password="$8"
  local mqtt_host="$9"
  local mqtt_port="${10}"
  local mqtt_username="${11}"
  local mqtt_password="${12}"
  local network_name="${13}"
  local traffic_enabled="OFF"

  if [[ -n "${amap_key}" ]]; then
    traffic_enabled="ON"
  fi

  cat > "${target_file}" <<EOF
services:
  tesla-notifier:
    image: ${IMAGE_NAME}
    container_name: tesla-notifier
    restart: unless-stopped
    volumes:
      - ./data:/app/data
    environment:
      BARK_URL: $(yaml_quote "${OFFICIAL_BARK_URL}")
      BARK_KEY: $(yaml_quote_literal "${bark_key}")
      DB_HOST: $(yaml_quote_literal "${db_host}")
      DB_PORT: $(yaml_quote_literal "${db_port}")
      DB_NAME: $(yaml_quote_literal "${db_name}")
      DB_USER: $(yaml_quote_literal "${db_user}")
      DB_PASSWORD: $(yaml_quote_literal "${db_password}")
      ENABLE_MQTT: "true"
      MQTT_URL: $(yaml_quote_literal "mqtt://${mqtt_host}:${mqtt_port}")
EOF

  if [[ -n "${mqtt_username}" ]]; then
    cat >> "${target_file}" <<EOF
      MQTT_USERNAME: $(yaml_quote_literal "${mqtt_username}")
EOF
  fi

  if [[ -n "${mqtt_password}" ]]; then
    cat >> "${target_file}" <<EOF
      MQTT_PASSWORD: $(yaml_quote_literal "${mqtt_password}")
EOF
  fi

  cat >> "${target_file}" <<EOF
      ENABLE_CRON: "true"
      DAILY_CRON: "0 8 * * *"
      WEEKLY_CRON: "0 9 * * mon"
      MONTHLY_CRON: "0 9 1 * *"
      CAR_ID: "1"
      MIN_TRIP_DISTANCE: "1"
      TZ: "Asia/Shanghai"
      SYSTEM_HEALTH_NOTIFY_ENABLED: "ON"
      FAILURE_ALERT_NOTIFY_ENABLED: "ON"
      MQTT_FRESHNESS_MONITOR_ENABLED: "ON"
      MQTT_FRESHNESS_CHECK_INTERVAL: "300"
      MQTT_FRESHNESS_STALE_AFTER: "900"
      MQTT_FRESHNESS_DB_ACTIVE_WINDOW: "1800"
      SENTRY_NOTIFY_ENABLED: "ON"
      DEPARTURE_SAFETY_NOTIFY_ENABLED: "ON"
      TPMS_NOTIFY_ENABLED: "ON"
      CHARGING_ISSUE_NOTIFY_ENABLED: "ON"
      TRAFFIC_ANALYSIS_ENABLED: $(yaml_quote "${traffic_enabled}")
EOF

  if [[ -n "${amap_key}" ]]; then
    cat >> "${target_file}" <<EOF
      AMAP_KEY: $(yaml_quote_literal "${amap_key}")
EOF
  fi

  cat >> "${target_file}" <<EOF
    networks:
      - teslamate

networks:
  teslamate:
    external: true
    name: $(yaml_quote "${network_name}")
EOF
}

main() {
  need_command docker
  need_command find
  need_command grep
  need_command sed
  need_command awk

  local compose_cmd_text
  local -a compose_cmd
  compose_cmd_text="$(detect_compose_command)"
  read -r -a compose_cmd <<< "${compose_cmd_text}"

  echo "Tesla Notifier 一键安装脚本"
  echo
  echo "稳妥策略：不修改 TeslaMate 原 docker-compose 文件。"
  echo "脚本只读取 TeslaMate 配置，并在旁边创建独立的 tesla-notifier/docker-compose.yml。"
  echo "在最终确认前按 Ctrl+C 不会写入任何文件。"
  echo

  local bark_key
  read -r -p "请输入 Bark Key（默认使用官方 Bark: ${OFFICIAL_BARK_URL}，必填）: " bark_key
  bark_key="$(strip_bark_key "${bark_key}")"
  [[ -n "${bark_key}" ]] || fail "Bark Key 为空，已取消安装。"

  echo
  echo "可选：配置高德地图 API Key。"
  echo "申请地址：${AMAP_APPLY_URL}"
  echo "操作路径：高德开放平台控制台 -> 应用管理 -> 我的应用 -> 创建新应用 -> 添加 Key -> 服务平台 Web服务。"
  echo "高德 Web 服务 Key 免费申请；配置后可解锁中文地址、天气、行程交通路况分析等能力。"
  echo "如果直接按回车，将跳过高德 Key。"

  local amap_input amap_key=""
  read -r -p "请输入 AMAP_KEY（可选）: " amap_input
  amap_input="$(trim "${amap_input}")"
  if [[ -n "${amap_input}" ]]; then
    info "正在测试高德 Key..."
    if validate_amap_key "${amap_input}"; then
      amap_key="${amap_input}"
    fi
  else
    warn "已跳过 AMAP_KEY；天气将使用 Open-Meteo 备用源，地址和交通路况增强不可用。"
  fi

  echo
  local teslamate_compose
  teslamate_compose="$(choose_compose_file)"
  [[ -f "${teslamate_compose}" ]] || fail "compose 文件不存在：${teslamate_compose}"

  local teslamate_dir install_parent_dir project_name db_host db_port db_name db_user db_password
  local mqtt_host mqtt_port mqtt_username mqtt_password network_name
  teslamate_dir="$(cd "$(dirname "${teslamate_compose}")" && pwd)"
  install_parent_dir="$(cd "${teslamate_dir}/.." && pwd)"
  project_name="$(detect_project_name "${teslamate_compose}")"
  network_name="$(detect_network_name "${teslamate_compose}" "${project_name}")"

  db_host="$(extract_compose_value DATABASE_HOST "${teslamate_compose}" || true)"
  db_host="$(resolve_compose_value "${db_host}" "${teslamate_dir}")"
  db_host="${db_host:-$(read_first_dotenv_value "${teslamate_dir}/.env" DATABASE_HOST DB_HOST || true)}"
  db_host="${db_host:-$(detect_service_name "${teslamate_compose}" database db postgres postgresql || true)}"
  db_host="${db_host:-database}"
  db_port="$(extract_compose_value DATABASE_PORT "${teslamate_compose}" || extract_compose_value DB_PORT "${teslamate_compose}" || true)"
  db_port="$(resolve_compose_value "${db_port}" "${teslamate_dir}")"
  db_port="${db_port:-$(read_first_dotenv_value "${teslamate_dir}/.env" DATABASE_PORT DB_PORT || true)}"
  db_port="${db_port:-5432}"
  db_name="$(extract_compose_value DATABASE_NAME "${teslamate_compose}" || extract_compose_value POSTGRES_DB "${teslamate_compose}" || true)"
  db_name="$(resolve_compose_value "${db_name}" "${teslamate_dir}")"
  db_name="${db_name:-$(read_first_dotenv_value "${teslamate_dir}/.env" DATABASE_NAME POSTGRES_DB DB_NAME || true)}"
  db_name="${db_name:-teslamate}"
  db_user="$(extract_compose_value DATABASE_USER "${teslamate_compose}" || extract_compose_value POSTGRES_USER "${teslamate_compose}" || true)"
  db_user="$(resolve_compose_value "${db_user}" "${teslamate_dir}")"
  db_user="${db_user:-$(read_first_dotenv_value "${teslamate_dir}/.env" DATABASE_USER POSTGRES_USER DB_USER || true)}"
  db_user="${db_user:-teslamate}"
  db_password="$(extract_compose_value DATABASE_PASS "${teslamate_compose}" || extract_compose_value POSTGRES_PASSWORD "${teslamate_compose}" || true)"
  db_password="$(resolve_compose_value "${db_password}" "${teslamate_dir}")"
  db_password="${db_password:-$(read_first_dotenv_value "${teslamate_dir}/.env" DATABASE_PASS POSTGRES_PASSWORD DB_PASSWORD || true)}"
  mqtt_host="$(extract_compose_value MQTT_HOST "${teslamate_compose}" || true)"
  mqtt_host="$(resolve_compose_value "${mqtt_host}" "${teslamate_dir}")"
  mqtt_host="${mqtt_host:-$(read_first_dotenv_value "${teslamate_dir}/.env" MQTT_HOST || true)}"
  mqtt_host="${mqtt_host:-$(detect_service_name "${teslamate_compose}" mosquitto mqtt broker emqx || true)}"
  mqtt_host="${mqtt_host:-mosquitto}"
  mqtt_port="$(extract_compose_value MQTT_PORT "${teslamate_compose}" || true)"
  mqtt_port="$(resolve_compose_value "${mqtt_port}" "${teslamate_dir}")"
  mqtt_port="${mqtt_port:-$(read_first_dotenv_value "${teslamate_dir}/.env" MQTT_PORT || true)}"
  mqtt_port="${mqtt_port:-1883}"
  mqtt_username="$(extract_compose_value MQTT_USERNAME "${teslamate_compose}" || true)"
  mqtt_username="$(resolve_compose_value "${mqtt_username}" "${teslamate_dir}")"
  mqtt_username="${mqtt_username:-$(read_first_dotenv_value "${teslamate_dir}/.env" MQTT_USERNAME || true)}"
  mqtt_password="$(extract_compose_value MQTT_PASSWORD "${teslamate_compose}" || true)"
  mqtt_password="$(resolve_compose_value "${mqtt_password}" "${teslamate_dir}")"
  mqtt_password="${mqtt_password:-$(read_first_dotenv_value "${teslamate_dir}/.env" MQTT_PASSWORD || true)}"

  echo
  echo "已识别 TeslaMate 配置："
  echo "  compose 文件: ${teslamate_compose}"
  echo "  Docker 网络: ${network_name}"
  echo "  DB_HOST: ${db_host}"
  echo "  DB_NAME: ${db_name}"
  echo "  DB_USER: ${db_user}"
  echo "  MQTT_URL: mqtt://${mqtt_host}:${mqtt_port}"
  if [[ -n "${mqtt_username}" || -n "${mqtt_password}" ]]; then
    echo "  MQTT_AUTH: 已自动识别"
  fi

  if [[ -z "${db_password}" ]]; then
    echo
    db_password="$(prompt_with_default "未识别到数据库密码，请输入 TeslaMate PostgreSQL 密码" "")"
    [[ -n "${db_password}" ]] || fail "数据库密码为空，已取消安装。"
  else
    echo "  DB_PASSWORD: 已自动识别"
  fi

  ensure_docker_network_exists "${network_name}"

  INSTALL_DIR="${install_parent_dir}/${INSTALL_DIR_NAME}"
  local target_compose="${INSTALL_DIR}/docker-compose.yml"
  if [[ -e "${INSTALL_DIR}" && ! -d "${INSTALL_DIR}" ]]; then
    fail "安装路径已存在但不是目录：${INSTALL_DIR}"
  fi

  echo
  echo "即将创建独立部署目录：${INSTALL_DIR}"
  echo "TeslaMate 目录保持不变：${teslamate_dir}"
  echo "即将写入 tesla-notifier 配置："
  echo "  镜像: ${IMAGE_NAME}"
  echo "  Bark: 官方 Bark (${OFFICIAL_BARK_URL})"
  echo "  AMAP_KEY: $([[ -n "${amap_key}" ]] && echo "已配置" || echo "未配置")"
  echo "  MQTT 新鲜度监控: ON"
  echo "  实时提醒: 哨兵 / 离车安全 / 胎压 / 充电异常 默认开启"
  echo
  read -r -p "确认创建配置、拉取镜像并启动？[y/N]: " confirm
  confirm="$(trim "${confirm}")"
  if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
    echo "已取消，未修改任何文件。"
    exit 0
  fi

  WRITE_STARTED=1
  mkdir -p "${INSTALL_DIR}/data"
  if [[ -f "${target_compose}" ]]; then
    local backup_file
    backup_file="${target_compose}.bak.$(date +%Y%m%d-%H%M%S)"
    cp "${target_compose}" "${backup_file}"
    info "已备份现有 notifier compose：${backup_file}"
  fi

  local target_compose_tmp
  target_compose_tmp="${target_compose}.tmp.$$"
  write_compose_file \
    "${target_compose_tmp}" \
    "${bark_key}" \
    "${amap_key}" \
    "${db_host}" \
    "${db_port}" \
    "${db_name}" \
    "${db_user}" \
    "${db_password}" \
    "${mqtt_host}" \
    "${mqtt_port}" \
    "${mqtt_username}" \
    "${mqtt_password}" \
    "${network_name}"
  mv "${target_compose_tmp}" "${target_compose}"

  info "已写入配置：${target_compose}"

  info "正在拉取镜像：${IMAGE_NAME}"
  "${compose_cmd[@]}" --project-directory "${INSTALL_DIR}" -f "${target_compose}" pull tesla-notifier

  info "正在启动 tesla-notifier..."
  "${compose_cmd[@]}" --project-directory "${INSTALL_DIR}" -f "${target_compose}" up -d

  echo
  info "安装完成。TeslaMate 原 docker-compose 文件未被修改。"
  echo "查看日志："
  echo "  ${compose_cmd[*]} --project-directory ${INSTALL_DIR} -f ${target_compose} logs -f tesla-notifier"
  echo
  echo "卸载 notifier："
  echo "  ${compose_cmd[*]} --project-directory ${INSTALL_DIR} -f ${target_compose} down"
}

main "$@"
