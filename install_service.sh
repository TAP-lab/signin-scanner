#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME=signin
SERVICE_USER=${SERVICE_USER:-$(id -un)}
SERVICE_GROUP=${SERVICE_GROUP:-$(id -gn)}
WORKDIR=$(cd "$(dirname "$0")" && pwd)
ENVFILE="$WORKDIR/.env"
PYTHON_BIN="$WORKDIR/.venv/bin/python"
PIP_BIN="$WORKDIR/.venv/bin/pip"

if [[ ! -f "$ENVFILE" ]]; then
  echo "Missing $ENVFILE; please create it before installing." >&2
  exit 1
fi

# Create venv and install deps
if [[ ! -x "$PYTHON_BIN" ]]; then
  /usr/bin/python3 -m venv "$WORKDIR/.venv"
fi
"$PIP_BIN" install --upgrade pip
"$PIP_BIN" install -r "$WORKDIR/requirements.txt"

# Copy env to a system-safe location (avoids spaces in paths)
sudo install -m 640 "$ENVFILE" /etc/default/$SERVICE_NAME

# Write systemd unit
sudo tee /etc/systemd/system/$SERVICE_NAME.service > /dev/null <<EOF
[Unit]
Description=Salesforce Sign-in Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
EnvironmentFile=/etc/default/$SERVICE_NAME
WorkingDirectory=$WORKDIR
ExecStart=/bin/bash -lc '. "$WORKDIR/.venv/bin/activate" && python "$WORKDIR/signin.py" --rfid'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE_NAME

echo "Service installed and started as $SERVICE_NAME (user=$SERVICE_USER)."
