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
- Hardware (optional, system works without hardware feedback):
  - MFRC522 RFID reader (for card scanning)
  - RGB LED (common cathode) with resistors
  - Passive piezo buzzer
  - 128x128 monochrome OLED display (I2C, SSD1306)

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
- **MFRC522 RFID (SPI)**: 3.3V, GND, SDA→GPIO8 (CE0), SCK→GPIO11, MOSI→GPIO10, MISO→GPIO9, RST→GPIO25
- **RGB LED**: Red→GPIO17, Green→GPIO27, Blue→GPIO22, Common Cathode→GND (use 220Ω resistors)
- **Piezo Buzzer**: Positive→GPIO23, Negative→GND
- **OLED Display (I2C)**: VCC→3.3V/5V, GND, SDA→GPIO2, SCL→GPIO3
- Enable **SPI and I2C** on the Pi via `raspi-config` (Interface Options)
- See `wiring.md` for detailed pin mappings and configuration

## Salesforce Setup

### Authentication: JWT Bearer Flow (Recommended)
This system uses **JWT Bearer Flow** (OAuth 2.0) to authenticate with Salesforce via REST API only—no SOAP Partner API.

#### Step 1: Create a Self-Signed Certificate
Generate a certificate and private key on your local machine:

```bash
# Generate private key (2048-bit RSA)
openssl genrsa -out server.key 2048

# Generate self-signed certificate (valid for 10 years)
openssl req -new -x509 -key server.key -out server.crt -days 3650 \
  -subj "/CN=SignInSystem/O=TAP Lab/C=US"

# View the certificate details to verify
openssl x509 -in server.crt -text -noout
```

Store `server.key` securely (this is your private key). You'll upload `server.crt` to Salesforce.

#### Step 2: Create a Connected App in Salesforce
1. **Log in to Salesforce** (as an admin)
2. Go to **Setup** → Search for **"App Manager"** → Click **"+ New"** → Select **"External Client"**
3. Fill in the form:
   - **Name**: `SignInSystem`
   - **Contact Email**: Your email
   - **Description**: `RFID sign-in system for TAP Lab MakerSpace`
4. Continue and configure OAuth:
   - Add OAuth Scopes:
     - `api` (Access and manage your data)
     - `refresh_token, offline_access` (Enable refresh token flow)
   - ✅ **Enable JWT-based Server Flows** (this is critical)
5. Click **Save**

#### Step 3: Upload Your Certificate
1. Open your External Client from App Manager
2. Look for **"Certificates"** or **"Digital Certificates"** section
3. Click **"Add Certificate"** and upload your `server.crt` file
4. **Save**

#### Step 4: Get Your Client ID
1. In the External Client page, find the **"Client ID"** field
2. Copy this value (you'll need it for `.env`)
3. **Do NOT share this key**

#### Step 5: Create a Service Account User (Optional but Recommended)
For production, create a dedicated service account instead of using your personal admin account:

1. **Setup** → **Users** → **New User**
2. Fill in:
   - **Last Name**: `SignInSystem`
   - **Email**: `signin-system@taplab.invalid` (or similar)
   - **Username**: `signin-system@taplab.invalid`
   - **Profile**: Choose a profile with API access (e.g., "System Administrator" for testing)
3. **Save**
4. The system will email a temporary password—ignore it (you won't use password auth)

#### Step 6: Authorize the Connected App
1. Go to **Setup** → Search **OAuth Token Flow** or **Connected Apps** → **Manage Connected Apps**
2. Find your connected app and click **Edit**
3. Under **OAuth Scopes**, ensure you have:
   - `api`
   - `refresh_token, offline_access`
4. **Save**

Now you need to authorize it with your service account:
1. As an admin, go to **Setup** → **Users** → find your service account
2. Click **Reset Password** (generates a temporary password)
3. In a private/incognito browser window, log in with that temporary password
4. Change password as prompted
5. You can now log out

The Connected App is now authorized for the service account.

#### Step 7: Configure .env
1. Copy your **Client ID** from the External Client settings
2. Specify the path to your **private key file** (`server.key`)
3. Set the **username** to your service account email (or your admin email if testing)

Create/update `.env`:
```bash
# Salesforce JWT Authentication
SF_USERNAME=signin-system@taplab.invalid
SF_CONSUMER_KEY=your-client-id-here
SF_PRIVATE_KEY_PATH=/path/to/server.key
SF_DOMAIN=test   # "test" for sandbox, remove or use "login" for production
```

#### Step 8: Verify It Works
```bash
python3 -c "from signin import connect_to_salesforce; connect_to_salesforce(); print('Connected!')"
```

You should see: `Connected to Salesforce using JWT Bearer Flow (REST API)`

✅ **Success!** Your system is now using REST API only (no SOAP).

### Migrating from Password Auth (Legacy)
If you're currently using `SF_PASSWORD` and `SF_SECURITY_TOKEN`:

1. **Stop the sign-in service**: `sudo systemctl stop signin`
2. Follow the JWT setup above (Steps 1–8)
3. Update your `.env` to remove `SF_PASSWORD` and `SF_SECURITY_TOKEN`
4. **Restart the service**: `sudo systemctl start signin`
5. **Verify logs**: `journalctl -u signin -f`

The service will log: `Connected to Salesforce using JWT Bearer Flow (REST API)`

---

## Environment variables (see .env)
- **Salesforce (JWT - Recommended)**: `SF_USERNAME`, `SF_CONSUMER_KEY`, `SF_PRIVATE_KEY_PATH`, `SF_DOMAIN`
- **Salesforce (Legacy Password)**: `SF_PASSWORD`, `SF_SECURITY_TOKEN` (uses SOAP Partner API - being retired)
- **Objects/fields**: `ACCESS_CARD_*`, `SIGNIN_*`, `WORKSHOP_*`
- **RFID**: `RFID_DEBOUNCE_SECONDS` (default: 1.0)

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
