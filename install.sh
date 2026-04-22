#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="autotrader"
SYSTEM_USER="autotrader"
INSTALL_DIR="/opt/tastytrade_autotrader"
VENV_DIR="${INSTALL_DIR}/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${INSTALL_DIR}/.env"
LOCAL_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

if [[ -t 1 ]]; then
  COLOR_RESET=$'\033[0m'
  COLOR_BLUE=$'\033[1;34m'
  COLOR_CYAN=$'\033[1;36m'
  COLOR_GREEN=$'\033[1;32m'
  COLOR_YELLOW=$'\033[1;33m'
  COLOR_RED=$'\033[1;31m'
else
  COLOR_RESET=""
  COLOR_BLUE=""
  COLOR_CYAN=""
  COLOR_GREEN=""
  COLOR_YELLOW=""
  COLOR_RED=""
fi

print_banner() {
  cat <<EOF
${COLOR_CYAN}
============================================================
      _   _   _ _____ ___ _____ ____      _    ____  _____ ____
     / \ | | | |_   _/ _ \_   _|  _ \    / \  |  _ \| ____|  _ \\
    / _ \| | | | | || | | || | | |_) |  / _ \ | | | |  _| | |_) |
   / ___ \ |_| | | || |_| || | |  _ <  / ___ \| |_| | |___|  _ <
  /_/   \_\___/  |_| \___/ |_| |_| \_\/_/   \_\____/|_____|_| \_\\

              Developed and Created by Lewis Talbot
============================================================
${COLOR_RESET}
EOF
}

step() {
  printf "%b\n" "${COLOR_BLUE}[$1/9]${COLOR_RESET} $2"
}

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

if [[ "${EUID}" -ne 0 ]]; then
  error "Error: install.sh must be run as root. Use: sudo bash install.sh"
  exit 1
fi

print_banner
info "Preparing a native Python install for Raspberry Pi OS Lite."

step 1 "Updating package index..."
apt update

step 2 "Installing system dependencies..."
apt install -y --no-install-recommends python3-pip python3-venv git curl

step 3 "Creating dedicated system user..."
if ! id -u "${SYSTEM_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/autotrader --shell /usr/sbin/nologin "${SYSTEM_USER}"
  success "Created system user: ${SYSTEM_USER}"
else
  warn "System user ${SYSTEM_USER} already exists; keeping it."
fi

step 4 "Copying project files into ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
tar \
  --exclude=".git" \
  --exclude="venv" \
  --exclude=".venv" \
  --exclude="__pycache__" \
  --exclude=".pytest_cache" \
  --exclude=".test_deps" \
  --exclude="test_deps" \
  -C "${SCRIPT_DIR}" \
  -cf - . | tar -C "${INSTALL_DIR}" -xf -

step 5 "Creating Python virtual environment..."
python3 -m venv "${VENV_DIR}"

step 6 "Installing Python requirements..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

step 7 "Preparing environment file and logs..."
if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${INSTALL_DIR}/.env.example" "${ENV_FILE}"
  success "Created ${ENV_FILE} from .env.example"
else
  warn "Existing .env found; leaving it unchanged."
fi

if grep -q '^API_KEY=auto_generate_me$' "${ENV_FILE}"; then
  GENERATED_API_KEY="$(openssl rand -hex 32 2>/dev/null || python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  sed -i "s/^API_KEY=auto_generate_me$/API_KEY=${GENERATED_API_KEY}/" "${ENV_FILE}"
  success "Generated a new API key."
fi

API_KEY_VALUE="$(sed -n 's/^API_KEY=//p' "${ENV_FILE}" | tail -n 1)"
install -d -o "${SYSTEM_USER}" -g "${SYSTEM_USER}" -m 775 "${INSTALL_DIR}/logs"
touch "${INSTALL_DIR}/logs/autotrader.log"
chown "${SYSTEM_USER}:${SYSTEM_USER}" "${INSTALL_DIR}/logs/autotrader.log"
chown -R "${SYSTEM_USER}:${SYSTEM_USER}" "${INSTALL_DIR}"
chown root:${SYSTEM_USER} "${ENV_FILE}"
chmod 640 "${ENV_FILE}"

step 8 "Installing systemd service..."
cp "${INSTALL_DIR}/systemd/autotrader.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

step 9 "Enabling and starting autotrader..."
systemctl enable "${SERVICE_NAME}"
systemctl start "${SERVICE_NAME}"

cat <<EOF

${COLOR_GREEN}============================================================${COLOR_RESET}
${COLOR_GREEN}  TastyTrade AutoTrader Installed Successfully${COLOR_RESET}
${COLOR_GREEN}============================================================${COLOR_RESET}

This Raspberry Pi is now configured to run the autotrader service
automatically on boot with systemd.

${COLOR_CYAN}What To Do Next${COLOR_RESET}
  1. Open the environment file:
     nano ${INSTALL_DIR}/.env

  2. Replace these three values with your Tastytrade credentials:
     TASTYTRADE_USERNAME
     TASTYTRADE_PASSWORD
     TASTYTRADE_ACCOUNT_NUMBER

  3. Keep these safe defaults for your first test:
     TASTYTRADE_BASE_URL=https://api.cert.tastyworks.com
     ALLOCATION_BASE=net_liquidation_value
     DRY_RUN=True

  If you want real sandbox order submission even when cert quotes fail:
     DRY_RUN=False
     ALLOW_SANDBOX_QUOTE_FALLBACK=True

  Optional safety feature:
     TELEGRAM_CONFIRMATION_ENABLED=True
     TELEGRAM_BOT_TOKEN=...
     TELEGRAM_CHAT_ID=...
     TELEGRAM_REQUEST_TIMEOUT_SECONDS=60

  4. Restart the service after saving:
     systemctl restart ${SERVICE_NAME}

  5. Check the service:
     systemctl status ${SERVICE_NAME}
     journalctl -u ${SERVICE_NAME} -f

${COLOR_CYAN}Signal Producer Setup${COLOR_RESET}
  Use this API key in the X-API-Key header for any trusted signal
  source posting to this backend:
     ${API_KEY_VALUE}

EOF

if [[ -n "${LOCAL_IP}" ]]; then
  cat <<EOF
  Signal endpoint:
     http://${LOCAL_IP}:5000/signal
  Status endpoint:
     http://${LOCAL_IP}:5000/status

EOF
fi

cat <<EOF
${COLOR_CYAN}Important Notes${COLOR_RESET}
  - ALLOCATION_BASE controls what 0.10 means in a signal:
      net_liquidation_value = 10% of account equity / portfolio value
      buying_power = 10% of margin buying power
      cash_balance = 10% of cash balance
  - The default is ALLOCATION_BASE=net_liquidation_value because it is
    usually the safest and most intuitive sizing base.
  - DRY_RUN=True means signals are processed and logged, but no real
    trades are submitted to Tastytrade.
  - ALLOW_SANDBOX_QUOTE_FALLBACK=True allows fallback pricing only in
    the cert sandbox, even when DRY_RUN=False, so you can test real
    sandbox order submission when quote endpoints are unreliable.
  - If Telegram confirmation is enabled, each trade must be approved
    using the Telegram Yes / No buttons before any order is submitted.
  - In Telegram, send /orders to the bot to view recent local order
    history, including successful, failed, and cancelled trades.
  - Change DRY_RUN=False only when you are fully ready to place real
    sandbox or live orders.
  - Keep your API key private. Any client that can send requests to
    this Pi can control the API if it knows that key.

${COLOR_CYAN}Useful Commands${COLOR_RESET}
  systemctl status ${SERVICE_NAME}
  journalctl -u ${SERVICE_NAME} -f
  nano ${INSTALL_DIR}/.env
  bash ${INSTALL_DIR}/test_signal.sh
  bash ${INSTALL_DIR}/test_signal.sh 127.0.0.1

EOF
