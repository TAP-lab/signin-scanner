#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME=signin
SERVICE_USER=${SERVICE_USER:-root}
SERVICE_GROUP=${SERVICE_GROUP:-root}

INSTALL_DIR=/usr/local/signin-scanner
ENVFILE="$INSTALL_DIR/.env"
WORKDIR="$INSTALL_DIR"
PYTHON_BIN="$INSTALL_DIR/.venv/bin/python"
PIP_BIN="$INSTALL_DIR/.venv/bin/pip"

if [[ ! -f "$ENVFILE" ]]; then
  echo "Missing $ENVFILE; please create it before installing." >&2

  exit 1
fi

# Check if installation directory exists
if [[ ! -d "$INSTALL_DIR" ]]; then
  echo "Error: $INSTALL_DIR does not exist" >&2
  exit 1
fi


echo "Setting up systemd service for signin-scanner..."

echo "  Installation directory: $INSTALL_DIR"
echo "  Service user: $SERVICE_USER"
echo "  Service group: $SERVICE_GROUP"

# Write systemd unit file
echo "Creating systemd service..."
tee /etc/systemd/system/$SERVICE_NAME.service > /dev/null <<EOF
[Unit]
Description=Salesforce Sign-in Reader Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/signin.py --rfid
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Install nightly reboot timer
echo "Installing nightly reboot timer..."
if [[ -f "$INSTALL_DIR/signin-nightly-reboot.timer" ]] && [[ -f "$INSTALL_DIR/signin-nightly-reboot.service" ]]; then
  cp "$INSTALL_DIR/signin-nightly-reboot.timer" /etc/systemd/system/
  cp "$INSTALL_DIR/signin-nightly-reboot.service" /etc/systemd/system/
  echo "  ✓ Nightly reboot timer installed (runs at 1:00 AM daily)"
else
  echo "  ⚠ Nightly reboot timer files not found in $INSTALL_DIR"
fi

# Enable and start the service
echo "Enabling service for boot..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME

# Enable nightly reboot timer
if [[ -f /etc/systemd/system/signin-nightly-reboot.timer ]]; then
  systemctl enable signin-nightly-reboot.timer
  echo "  ✓ Nightly reboot timer enabled"
fi

echo ""
echo "✓ Service configured successfully!"
echo ""
echo "Service commands:"
echo "  sudo systemctl start $SERVICE_NAME      # Start the service now"
echo "  sudo systemctl stop $SERVICE_NAME       # Stop the service"
echo "  sudo systemctl restart $SERVICE_NAME    # Restart the service"
echo "  sudo systemctl status $SERVICE_NAME     # View service status"
echo "  sudo journalctl -u $SERVICE_NAME -f     # View live logs"
echo ""
echo "Nightly reboot timer commands:"
echo "  sudo systemctl start signin-nightly-reboot.timer   # Enable nightly reboot"
echo "  sudo systemctl stop signin-nightly-reboot.timer    # Disable nightly reboot"
echo "  sudo systemctl status signin-nightly-reboot.timer  # Check timer status"
echo "  sudo systemctl list-timers                         # List all timers"
echo ""
echo "The service will automatically start on next boot."
echo "The system will reboot daily at 1:00 AM for maintenance."
echo ""
