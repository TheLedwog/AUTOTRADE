#!/usr/bin/env bash

set -euo pipefail

DEFAULT_ENV_FILE="/opt/tastytrade_autotrader/.env"
LOCAL_ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.env"

if [[ -t 1 ]]; then
  COLOR_RESET="\033[0m"
  COLOR_CYAN="\033[1;36m"
  COLOR_GREEN="\033[1;32m"
  COLOR_YELLOW="\033[1;33m"
  COLOR_RED="\033[1;31m"
else
  COLOR_RESET=""
  COLOR_CYAN=""
  COLOR_GREEN=""
  COLOR_YELLOW=""
  COLOR_RED=""
fi

info() {
  printf "%b\n" "${COLOR_CYAN}$1${COLOR_RESET}"
}

success() {
  printf "%b\n" "${COLOR_GREEN}$1${COLOR_RESET}"
}

warn() {
  printf "%b\n" "${COLOR_YELLOW}$1${COLOR_RESET}"
}

error() {
  printf "%b\n" "${COLOR_RED}$1${COLOR_RESET}"
}

read_env_value() {
  local env_file="$1"
  local key="$2"
  sed -n "s/^${key}=//p" "${env_file}" | tail -n 1
}

pretty_print_json() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -m json.tool 2>/dev/null || cat
  else
    cat
  fi
}

if [[ -f "${DEFAULT_ENV_FILE}" ]]; then
  ENV_FILE="${DEFAULT_ENV_FILE}"
elif [[ -f "${LOCAL_ENV_FILE}" ]]; then
  ENV_FILE="${LOCAL_ENV_FILE}"
else
  error "Could not find .env. Expected ${DEFAULT_ENV_FILE} or ${LOCAL_ENV_FILE}"
  exit 1
fi

API_KEY="$(read_env_value "${ENV_FILE}" "API_KEY")"
FLASK_PORT="$(read_env_value "${ENV_FILE}" "FLASK_PORT")"
HOST="${1:-127.0.0.1}"
PORT="${FLASK_PORT:-5000}"
BASE_URL="http://${HOST}:${PORT}"

if [[ -z "${API_KEY}" || "${API_KEY}" == "auto_generate_me" ]]; then
  error "API_KEY is missing or still set to auto_generate_me in ${ENV_FILE}"
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  error "curl is required to run this test script."
  exit 1
fi

STATUS_OUTPUT="$(mktemp)"
SIGNAL_OUTPUT="$(mktemp)"
trap 'rm -f "${STATUS_OUTPUT}" "${SIGNAL_OUTPUT}"' EXIT

TEST_PAYLOAD='{
  "symbol": "AAPL",
  "direction": "BUY",
  "signal_type": "STOCK",
  "allocation": 0.10
}'

info "Using env file: ${ENV_FILE}"
info "Testing backend at: ${BASE_URL}"
info "Step 1/2: Checking /status"

STATUS_CODE="$(
  curl -sS \
    -o "${STATUS_OUTPUT}" \
    -w "%{http_code}" \
    -H "X-API-Key: ${API_KEY}" \
    "${BASE_URL}/status"
)"

cat "${STATUS_OUTPUT}" | pretty_print_json

if [[ "${STATUS_CODE}" != "200" ]]; then
  error "/status returned HTTP ${STATUS_CODE}"
  exit 1
fi

success "/status returned HTTP 200"
info "Step 2/2: Sending sample signal to /signal"
warn "This script respects your backend settings. If DRY_RUN=False, it may place a real order."
info "Sample payload:"
printf "%s\n" "${TEST_PAYLOAD}" | pretty_print_json

SIGNAL_CODE="$(
  curl -sS \
    -o "${SIGNAL_OUTPUT}" \
    -w "%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${API_KEY}" \
    -d "${TEST_PAYLOAD}" \
    "${BASE_URL}/signal"
)"

cat "${SIGNAL_OUTPUT}" | pretty_print_json

if [[ "${SIGNAL_CODE}" != "200" ]]; then
  error "/signal returned HTTP ${SIGNAL_CODE}"
  exit 1
fi

success "Backend test completed successfully."
warn "If you are testing for the first time, keep DRY_RUN=True in your .env."

