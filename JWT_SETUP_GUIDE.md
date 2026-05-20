# JWT Bearer Flow Setup Guide for Salesforce

## Overview
This guide walks you through setting up **JWT Bearer Flow** (OAuth 2.0) authentication for the sign-in system. This eliminates the need for SOAP Partner API and uses REST API exclusively.

**Key Benefits:**
- ✅ REST API only (no deprecated SOAP Partner API)
- ✅ Improved security (no password stored)
- ✅ Better for service accounts
- ✅ Future-proof (Salesforce is retiring SOAP)

---

## Prerequisites
- Access to a Salesforce organization (sandbox or production)
- Admin privileges in Salesforce
- `openssl` command-line tool (included on Mac/Linux; Windows can use Git Bash)

---

## Part 1: Generate Certificate & Private Key

### Step 1a: Generate Private Key
Open a terminal and run:

```bash
openssl genrsa -out server.key 2048
```

This creates `server.key` (2048-bit RSA private key).

### Step 1b: Generate Self-Signed Certificate
```bash
openssl req -new -x509 -key server.key -out server.crt -days 3650 \
  -subj "/CN=SignInSystem/O=TAP Lab/C=US"
```

This creates `server.crt` (self-signed certificate, valid 10 years).

### Step 1c: Verify the Certificate
```bash
openssl x509 -in server.crt -text -noout
```

Look for:
- **Subject**: `CN = SignInSystem`
- **Validity**: Shows expiration date
- **Public-Key**: `2048 bit`

### Step 1d: Secure Your Private Key
```bash
# Restrict permissions (important!)
chmod 600 server.key

# Store both files in a safe location
# Example: ~/.salesforce/
mkdir -p ~/.salesforce
mv server.key ~/.salesforce/
cp server.crt ~/.salesforce/
```

⚠️ **IMPORTANT**: `server.key` is your private key. Never share it or commit it to git.

---

## Part 2: Create External Client in Salesforce

### Step 2a: Navigate to App Manager
1. **Log in to your Salesforce organization** (as admin)
2. Click **Setup** (gear icon in top-right)
3. In the left sidebar, search for **"App Manager"**
4. Click the **"App Manager"** result (under "Build" → "Create" → "Apps")

### Step 2b: Create New Connected App
1. Click **"+ New"** button (top-right)
2. Select **"External Client"** from the dropdown
3. Fill in the basic information:

| Field | Value |
|-------|-------|
| **Name** | `SignInSystem` |
| **Contact Email** | Your email address |
| **Description** | `RFID sign-in system for TAP Lab MakerSpace` |

4. Click **"Create"** or continue

### Step 2c: Configure OAuth Settings
After creating the External Client, you should see configuration options:

1. ✅ Enable **"Require Proof Key for Public Clients"** (PKCE) — optional but recommended
2. Under **"Allowed OAuth Flows"** or **"Selected OAuth Scopes"**:
   - Select or add these scopes:
     - `api` — Access and manage your data
     - `refresh_token, offline_access` — Enable refresh token flow
3. ✅ Enable **"JWT Bearer Flow"** or **"JWT-based Server Flows"** (critical)
4. **Save** or **Continue**

### Step 2d: Upload Your Certificate
1. In the External Client settings, look for **"Certificates"** or **"Digital Certificates"** section
2. Click **"Add Certificate"** or **"Upload"**
3. Select your `server.crt` file (created in Part 1)
4. **Save**

You should see a message confirming the certificate was uploaded.

### Step 2e: Get Your Client ID (Consumer Key)
1. In the External Client settings page, find the **"Client ID"** field
2. This is your Consumer Key — copy it
3. Example format: `3MVG9AzZqMhK7r0r5.j8...` (very long)

⚠️ Do NOT share this key or commit it to git.

---

## Part 3: Create a Service Account (Optional but Recommended)

For production use, create a dedicated service account instead of using your personal admin account.

### Step 3a: Create New User
1. Go to **Setup** → Search **"Users"** → Click **"Users"** (under Administer → Users)
2. Click **"+ New User"** button
3. Fill in:

| Field | Value |
|-------|-------|
| **Last Name** | `SignInSystem` |
| **Email** | `signin-system@taplab.invalid` |
| **Username** | `signin-system@taplab.invalid` |
| **Profile** | `System Administrator` (for testing; use appropriate profile for production) |
| **User License** | `Salesforce` |

4. Click **"Save & Close"**
5. Salesforce sends a welcome email with a temporary password (you won't use this)

### Step 3b: Authorize Service Account with Connected App
To complete the JWT flow setup:

1. As the **admin**, go to **Setup** → **Users** → find your service account (e.g., `signin-system@taplab.invalid`)
2. Click on the service account username to open their profile
3. Click **"Reset Password"**
4. Click **"New Password"** button — Salesforce generates a temporary password
5. In an **incognito/private browser window**, log in with:
   - **Username**: `signin-system@taplab.invalid` (with sandbox suffix if in sandbox)
   - **Password**: The temporary password from step 4
6. Salesforce prompts you to change your password — set a new one
7. Log out

Now your service account is created and ready.

### Step 3c: Alternative — Use Your Admin Account
If you prefer not to create a service account:
- Use your own Salesforce username in the `.env` file
- The system will work the same way
- ⚠️ Sharing your admin account credentials (even via JWT) is less secure

---

## Part 4: Configure Environment Variables

### Step 4a: Create or Update `.env`
In your project directory, create/update `.env`:

```bash
# === SALESFORCE JWT AUTHENTICATION ===
SF_USERNAME=signin-system@taplab.invalid
SF_CONSUMER_KEY=3MVG9AzZqMhK7r0r5.j8...  # Copy from Connected App (Step 2e)
SF_PRIVATE_KEY_PATH=/home/pi/.salesforce/server.key  # Update path as needed
SF_DOMAIN=test  # "test" for sandbox, "login" for production

# Other configuration...
ACCESS_CARD_SOBJECT=Access_card__c
SIGNIN_SOBJECT=Sign_in__c
# ... etc
```

**Replace these values:**
- `SF_USERNAME`: Your service account email (or admin email)
- `SF_CONSUMER_KEY`: From Connected App settings
- `SF_PRIVATE_KEY_PATH`: Full path to `server.key` (e.g., `/home/pi/.salesforce/server.key`)
- `SF_DOMAIN`: 
  - Use `test` if connecting to a **sandbox**
  - Use `login` if connecting to **production**

### Step 4b: Set Correct Permissions
Protect your private key:

```bash
chmod 600 server.key
```

---

## Part 5: Test the Connection

### Step 5a: Test from Command Line
```bash
# Activate venv if needed
source .venv/bin/activate

# Test the connection
python3 -c "
from signin import connect_to_salesforce
import logging
logging.basicConfig(level=logging.INFO)
sf = connect_to_salesforce()
if sf:
    print('✓ Successfully connected!')
    print('Instance:', sf.session_id[:20] + '...')
else:
    print('✗ Connection failed')
"
```

**Expected output:**
```
Connected to Salesforce using JWT Bearer Flow (REST API)
✓ Successfully connected!
Instance: 00Dxx0000000000!AR...
```

### Step 5b: Troubleshoot Connection Issues

| Error | Solution |
|-------|----------|
| `Private key file not found` | Check `SF_PRIVATE_KEY_PATH` is correct and file exists |
| `invalid_client_id` | Verify `SF_CONSUMER_KEY` is correct |
| `invalid_grant` | Check service account username matches `SF_USERNAME` |
| `No such file or directory: /path/to/server.key` | Use absolute path, not relative |
| `JWT validation failed` | Certificate not uploaded to Connected App or expired |

### Step 5c: Enable Debug Logging
To see more details if having issues:

```bash
export SF_LOG_LEVEL=DEBUG
python3 -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from signin import connect_to_salesforce
connect_to_salesforce()
"
```

---

## Part 6: Run the Sign-In System

### Step 6a: Manual Test
```bash
# Terminal mode (manual entry)
python3 signin.py --terminal

# RFID mode (requires RFID reader)
python3 signin.py --rfid
```

You should see:
```
Connected to Salesforce using JWT Bearer Flow (REST API)
Running. Press Ctrl+C to stop.
```

### Step 6b: Install as Service
```bash
chmod +x install_service.sh
sudo ./install_service.sh

# Check status
sudo systemctl status signin

# View logs
journalctl -u signin -f
```

Logs should show:
```
Connected to Salesforce using JWT Bearer Flow (REST API)
Waiting for RFID card...
```

---

## Migration from Password Authentication

If you're currently using `SF_PASSWORD` and `SF_SECURITY_TOKEN`:

### Step 1: Complete JWT Setup
Follow Parts 1–5 above to generate keys and configure the Connected App.

### Step 2: Update `.env`
```bash
# REMOVE these lines:
# SF_PASSWORD=...
# SF_SECURITY_TOKEN=...

# ADD these lines:
SF_CONSUMER_KEY=3MVG9AzZqMhK7r0r5.j8...
SF_PRIVATE_KEY_PATH=/path/to/server.key
```

### Step 3: Stop and Restart Service
```bash
sudo systemctl stop signin
sudo systemctl start signin

# Verify
journalctl -u signin -f
```

**Expected log:**
```
Connected to Salesforce using JWT Bearer Flow (REST API)
```

### Step 4: Verify No SOAP Calls
In Salesforce:
1. Go to **Setup** → **System Overview** → **System Resources** → **API Usage**
2. Look for authentication entries — should show **REST API** or **Web Services** (not "SOAP Partner")

---

## Certificate Renewal

Certificates expire! Here's how to renew:

### When to Renew
- Your certificate is valid for 10 years, but Salesforce may expire it sooner
- Check expiration: `openssl x509 -in server.crt -text -noout | grep -A2 Validity`

### How to Renew
1. Generate new certificate (follow Part 1)
2. Upload to Connected App (follow Step 2d)
3. No code changes needed — just update `SF_PRIVATE_KEY_PATH` in `.env` if you move files

---

## Troubleshooting Checklist

- [ ] `server.key` file exists and is readable
- [ ] `SF_PRIVATE_KEY_PATH` is an absolute path
- [ ] `SF_CONSUMER_KEY` matches Connected App exactly
- [ ] `SF_USERNAME` is correct email (with sandbox suffix if applicable)
- [ ] Connected App has **"Enable JWT-based Server Flows"** ✅
- [ ] Certificate uploaded to Connected App successfully
- [ ] Service account created and authorized
- [ ] `SF_DOMAIN` is correct (`test` for sandbox, `login` for production)
- [ ] Test connection logs show "JWT Bearer Flow (REST API)"

---

## Additional Resources

- [Salesforce JWT Bearer Flow Documentation](https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/oauth2_jwt_bearer_flow.html)
- [Connected Apps Documentation](https://developer.salesforce.com/docs/atlas.en-us.creating_connected_apps.meta/creating_connected_apps/index.htm)
- [simple-salesforce JWT Documentation](https://simple-salesforce.readthedocs.io/en/latest/)

---

**Questions?** Check the logs: `journalctl -u signin -f`
