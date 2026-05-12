# Salesforce Sign-In System

A Raspberry Pi sign-in processor that reads RFID cards or terminal input to create/update Salesforce sign-in records.

## Features
- RFID or terminal sign-in/out flow with automatic workshop name tagging
- Salesforce-backed lookups and record creation
- Continuous operation mode with RFID debounce
- Systemd service install script for autonomous operation
- **User feedback loop with RGB LED, passive piezo buzzer, and OLED display**
  - Visual feedback via RGB LED (different colors for different states)
  - Audio feedback via passive piezo buzzer (different beep patterns)
  - Display feedback via 128x128 monochrome OLED (status messages)
  - Graceful fallback when hardware is not available

## Prerequisites
- Raspberry Pi (with SPI and I2C enabled)
- Python 3.9+
- Salesforce credentials with API access
- Network connectivity to Salesforce
- **TrueType fonts** (for OLED text rendering):
  - DejaVu, Liberation, or FreeFonts packages (see Setup section)
  - Without fonts, OLED display falls back to limited default font
- Hardware (optional, system works without hardware feedback):
  - MFRC522 RFID reader (for card scanning)
  - RGB LED (common cathode) with resistors
  - Passive piezo buzzer
  - 128x128 monochrome OLED display (I2C, SSD1306)

## Setup
1) Clone/copy this folder to the Pi
2) Create and fill `.env` (template present in repo)
3) Install system dependencies:
   ```bash
   chmod +x install_dependencies.sh
   ./install_dependencies.sh
   ```
   This installs Python tools, I2C/SPI utilities, and **required fonts for OLED display**.
   
4) Install Python dependencies:
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
- **MFRC522 RFID (SPI)**: 3.3V, GND, SDA→GPIO8 (CE0), SCK→GPIO11, MOSI→GPIO10, MISO→GPIO9, RST→GPIO25
- **RGB LED**: Red→GPIO17, Green→GPIO27, Blue→GPIO22, Common Cathode→GND (use 220Ω resistors)
- **Piezo Buzzer**: Positive→GPIO23, Negative→GND
- **OLED Display (I2C)**: VCC→3.3V/5V, GND, SDA→GPIO2, SCL→GPIO3
- Enable **SPI and I2C** on the Pi via `raspi-config` (Interface Options)
- See `wiring.md` for detailed pin mappings and configuration

## Environment variables (see .env)
- Salesforce: `SF_USERNAME`, `SF_PASSWORD`, `SF_SECURITY_TOKEN`, `SF_DOMAIN`
- Objects/fields: `ACCESS_CARD_*`, `SIGNIN_*`, `WORKSHOP_*`
- RFID: `RFID_DEBOUNCE_SECONDS` (default: 1.0)

## User Feedback States
The system provides visual (LED), audio (buzzer), and display (OLED) feedback for the following states:

| State | LED Color | Audio | Display Message |
|-------|-----------|-------|-----------------|
| **Ready to Scan** | Cyan | None | "Please scan your card" |
| **Processing Scan** | Yellow | Quick beep | "Processing..." |
| **Signed In** | Green (3s) | Rising beep | "✓ Signed In - Welcome!" |
| **Signed Out** | Blue (3s) | Falling beep | "✓ Signed Out - Goodbye!" |
| **Card Not Found** | Red (3s) | Triple low beep | "✗ Card Unknown" |
| **Scan/Processing Error** | Red (3s) | Low error tone | "✗ Scan Error" |
| **System Unavailable** | Red (3s) | Double warning beep | "✗ System Unavailable" |
| **Network Error** | Red (persistent) | Double warning beep | "✗ Network Error" |
| **Debounced** | Yellow (0.1s) | None | (No display) |

> **Note:** Display messages shown above are simplified for readability. On the actual OLED, messages are rendered across multiple lines. For example, **Signed In** displays as `"✓ Signed In"` on line 1, a blank line, then `"Welcome!"` on line 3.

The feedback system gracefully degrades when hardware is not available (e.g., on non-Pi systems or during development). For result states (signed in/out, errors), the LED automatically turns off after 3 seconds to avoid confusion. The debounced state provides a brief 0.1s yellow blink to acknowledge card detection without disrupting the user flow.

## Network Monitoring and Recovery
The system includes automatic network monitoring to ensure reliable operation:

- **Connectivity Checks**: Every 10 minutes, the system checks internet connectivity by pinging Google DNS (8.8.8.8)
- **Network Error Display**: When connection is lost, the system displays "Network Error" on the OLED with a persistent red LED (silent, no beep)
- **Automatic Recovery**: After 3 consecutive failed connectivity checks, the system attempts to restart network services
- **Recovery Methods**: Tries bringing wlan0 interface down/up, restarting wpa_supplicant, networking service, and dhcpcd
- **Restart Cooldown**: Network restart attempts are rate-limited to once every 5 minutes to prevent excessive restarts
- **System Reboot**: After 6 failed restart attempts (approximately 1 hour of failures), the system will reboot as a last resort
- **Connection Restoration**: When connectivity is restored, the system automatically returns to normal operation

> **Note:** For automatic network service restart to work, the service user needs sudo permissions. Add the following to `/etc/sudoers.d/signin-scanner`:
> ```
> signin ALL=(ALL) NOPASSWD: /sbin/ip
> signin ALL=(ALL) NOPASSWD: /bin/systemctl restart wpa_supplicant
> signin ALL=(ALL) NOPASSWD: /bin/systemctl restart networking
> signin ALL=(ALL) NOPASSWD: /bin/systemctl restart dhcpcd
> signin ALL=(ALL) NOPASSWD: /sbin/reboot
> ```
> Replace `signin` with your actual service user if different.

### Nightly Reboot
To maintain system reliability, a scheduled reboot occurs at 1:00 AM daily via cron:
- Helps clear memory leaks and reset system state
- Implemented as a simple cron job for minimal overhead
- Can be disabled by editing root's crontab: `sudo crontab -e`

## Notes
- Uses OS local timezone (with DST) for workshop matching
- RFIDs that match open sign-ins trigger sign-out; otherwise a new sign-in is created
- Debounce prevents repeated scans when a card is held over the reader
