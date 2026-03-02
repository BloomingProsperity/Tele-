#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as a normal user with sudo privileges, not as root."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SERVICE_NAME="${1:-tele-ai-bot}"
PYTHON_VERSION="${PYTHON_VERSION:-3.13}"
ENV_PATH="${REPO_ROOT}/.env"

if [[ ! -f "${ENV_PATH}" ]]; then
  cp "${REPO_ROOT}/.env.example" "${ENV_PATH}"
  echo "Created ${ENV_PATH} from .env.example"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not available in PATH after installation."
  echo "Please add ~/.local/bin to PATH and rerun."
  exit 1
fi

echo "Syncing dependencies with Python ${PYTHON_VERSION}..."
cd "${REPO_ROOT}"
uv python install "${PYTHON_VERSION}"
uv sync --python "${PYTHON_VERSION}"

echo "Validating .env..."
if ! grep -q '^RUN_MODE=bot$' "${ENV_PATH}"; then
  echo "RUN_MODE is not set to bot in ${ENV_PATH}. Set RUN_MODE=bot before starting."
  exit 1
fi

if ! grep -q '^BOT_TOKEN=' "${ENV_PATH}"; then
  echo "BOT_TOKEN is missing in ${ENV_PATH}."
  exit 1
fi

if grep -q '^BOT_TOKEN=$' "${ENV_PATH}"; then
  echo "BOT_TOKEN is empty in ${ENV_PATH}."
  exit 1
fi

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
echo "Writing ${SERVICE_FILE}..."
sudo tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=Tele AI Translator Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${REPO_ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart=${REPO_ROOT}/.venv/bin/python -m tele_ai.main
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo "Service status:"
sudo systemctl status "${SERVICE_NAME}" --no-pager || true

echo
echo "Logs command:"
echo "sudo journalctl -u ${SERVICE_NAME} -f"
