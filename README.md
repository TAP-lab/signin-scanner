# Salesforce Sign-In System

A Raspberry Pi sign-in processor that reads RFID cards or terminal input to create/update Salesforce sign-in records.

## Features
- RFID or terminal sign-in/out flow with automatic workshop name tagging
- Salesforce-backed lookups and record creation
- Continuous operation mode with RFID debounce
- Systemd service install script for autonomous operation

## Prerequisites
- Raspberry Pi (with SPI enabled for MFRC522 RFID reader)
- Python 3.9+
- Salesforce credentials with API access
- Network connectivity to Salesforce
- Hardware: MFRC522 RFID reader (optional, can use terminal input)

## Setup
1) Clone/copy this folder to the Pi
2) Create and fill `.env` (template present in repo)
3) Install system deps on Pi (if missing):
   ```bash
   sudo apt-get update
   sudo apt-get install -y python3-venv python3-pip
   ```
4) Install Python deps:
   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

## Run (manual)
The script runs in continuous mode. You must specify either `--rfid` or `--terminal`:

- **RFID mode**: `python signin.py --rfid`
- **Terminal mode**: `python signin.py --terminal`

Press Ctrl+C to stop.

## Run as a service (systemd)
On the Pi:
```bash
chmod +x install_service.sh
./install_service.sh
```
- Override service user/group: `SERVICE_USER=pi SERVICE_GROUP=pi ./install_service.sh`
- Service commands: `sudo systemctl status|restart|stop signin`
- Logs: `journalctl -u signin -f`

## Wiring (quick reference)
- MFRC522 (SPI): 3.3V, GND, SDA→GPIO8 (CE0), SCK→GPIO11, MOSI→GPIO10, MISO→GPIO9, RST→GPIO25
- Enable SPI on the Pi via `raspi-config` (Interface Options → SPI)
- See `wiring.md` for detailed pin mappings

## Environment variables (see .env)
- Salesforce: `SF_USERNAME`, `SF_PASSWORD`, `SF_SECURITY_TOKEN`, `SF_DOMAIN`
- Objects/fields: `ACCESS_CARD_*`, `SIGNIN_*`, `WORKSHOP_*`
- RFID: `RFID_DEBOUNCE_SECONDS` (default: 1.0)

## Notes
- Uses OS local timezone (with DST) for workshop matching
- RFIDs that match open sign-ins trigger sign-out; otherwise a new sign-in is created
- Debounce prevents repeated scans when a card is held over the reader
