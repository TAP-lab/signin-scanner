#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME=signin
SERVICE_USER=${SERVICE_USER:-root}
SERVICE_GROUP=${SERVICE_GROUP:-root}
INSTALL_DIR=/usr/local/signin-reader

# Check if running as root
if [[ $EUID -ne 0 ]]; then
  echo "This script must be run as root (use: sudo bash install_service.sh)" >&2
  exit 1
fi

# Check if installation directory exists
if [[ ! -d "$INSTALL_DIR" ]]; then
  echo "Error: $INSTALL_DIR does not exist" >&2
  exit 1
fi

echo "Setting up systemd service for signin-reader..."
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
EnvironmentFile=/etc/default/$SERVICE_NAME
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/signin.py --rfid
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Enable and start the service
echo "Enabling service for boot..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME

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
echo "The service will automatically start on next boot."
echo ""
