#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="autotrader"
SYSTEM_USER="autotrader"
INSTALL_DIR="/opt/tastytrade_autotrader"
SYSTEM_USER_HOME="/var/lib/autotrader"

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

step() {
  printf "%b\n" "${COLOR_BLUE}$1${COLOR_RESET}"
}

if [[ "${EUID}" -ne 0 ]]; then
  error "Error: uninstall.sh must be run as root. Use: sudo bash uninstall.sh"
  exit 1
fi

info "Starting TastyTrade AutoTrader uninstall..."

step "Stopping and disabling systemd service..."
if systemctl list-unit-files | grep -q "^${SERVICE_NAME}\.service"; then
  systemctl stop "${SERVICE_NAME}" || true
  systemctl disable "${SERVICE_NAME}" || true
  success "Service ${SERVICE_NAME} stopped and disabled."
else
  warn "Service ${SERVICE_NAME} was not installed."
fi

step "Removing systemd service file..."
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
success "systemd reloaded."

read -r -p "Remove ${INSTALL_DIR} as well? [y/N]: " remove_project
if [[ "${remove_project}" =~ ^[Yy]$ ]]; then
  rm -rf "${INSTALL_DIR}"
  success "Removed ${INSTALL_DIR}."
else
  warn "Left ${INSTALL_DIR} in place."
fi

read -r -p "Remove the '${SYSTEM_USER}' system user too? [y/N]: " remove_user
if [[ "${remove_user}" =~ ^[Yy]$ ]]; then
  if id -u "${SYSTEM_USER}" >/dev/null 2>&1; then
    userdel "${SYSTEM_USER}" 2>/dev/null || true
    rm -rf "${SYSTEM_USER_HOME}"
    success "Removed system user ${SYSTEM_USER} and cleaned ${SYSTEM_USER_HOME}."
  else
    warn "System user ${SYSTEM_USER} does not exist."
  fi
else
  warn "Left system user ${SYSTEM_USER} in place."
fi

cat <<EOF

${COLOR_GREEN}Uninstall complete.${COLOR_RESET}

If you want a totally fresh reinstall next:
  git clone <your-repo-url>
  cd tastytrade_autotrader
  sudo bash install.sh

EOF
