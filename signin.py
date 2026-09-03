"""Signin processing helpers for Salesforce-backed sign-in system.

This module provides small, well-documented functions to:
- connect to Salesforce using environment variables or explicit credentials
- look up access cards by serial number
- create sign-in records and close open sign-ins

Configuration is read from environment variables. The module is intentionally
small and dependency-light so it can be used as a library or run as a CLI.
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    # Load environment variables from a local .env file when available.
    # This must happen before module-level os.getenv() reads below.
    load_dotenv()
except Exception:
    # `python-dotenv` may not be installed in all environments; continue.
    pass

# Import hardware feedback module
try:
    from feedback_hardware import (
        FeedbackState,
        provide_feedback,
        register_facilitator_button,
        register_idle_callback,
        shutdown_hardware,
    )

    _HAS_HARDWARE_FEEDBACK = True
except ImportError:
    _HAS_HARDWARE_FEEDBACK = False

# Import network monitor module
try:
    from network_monitor import NetworkMonitor

    _HAS_NETWORK_MONITOR = True
except ImportError:
    _HAS_NETWORK_MONITOR = False

# Import workshop calendar sync module
try:
    from workshop_calendar_sync import WorkshopCalendarSync

    _HAS_CALENDAR_SYNC = True
except ImportError:
    _HAS_CALENDAR_SYNC = False

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

# Configuration (can be overridden via environment variables)
ACCESS_CARD_SOBJECT = os.getenv("ACCESS_CARD_SOBJECT", "Access_card__c")
ACCESS_CARD_SERIAL_FIELD = os.getenv("ACCESS_CARD_SERIAL_FIELD", "serial_number__c")
ACCESS_CARD_CONTACT_FIELD = os.getenv("ACCESS_CARD_CONTACT_FIELD", "contact__c")

SIGNIN_SOBJECT = os.getenv("SIGNIN_SOBJECT", "Sign_ins__c")
SIGNIN_CONTACT_FIELD = os.getenv("SIGNIN_CONTACT_FIELD", "contact_id__c")
SIGNIN_SIGNIN_FIELD = os.getenv("SIGNIN_SIGNIN_FIELD", "sign_in_time__c")
SIGNIN_SIGNOUT_FIELD = os.getenv("SIGNIN_SIGNOUT_FIELD", "sign_out_time__c")
SIGNIN_WORKSHOP_FIELD = os.getenv("SIGNIN_WORKSHOP_FIELD", "Workshop_Name__c")
SIGNIN_NAME_FIELD = os.getenv("SIGNIN_NAME_FIELD", "Name__c")
SIGNIN_RECORDTYPE_ID = os.getenv("SIGNIN_RECORDTYPE_ID", "")
SIGNIN_FACILITATOR_FIELD = os.getenv(
    "SIGNIN_FACILITATOR_FIELD", "signin_is_facilitator__c"
)
SIGNIN_PERSON_WAS_SESSION_FACILITATOR_FIELD = os.getenv(
    "SIGNIN_PERSON_WAS_SESSION_FACILITATOR_FIELD", "Person_was_session_facilitator__c"
)

WORKSHOP_SOBJECT = os.getenv("WORKSHOP_SOBJECT", "TAP_lab_Workshop__c")
WORKSHOP_NAME_FIELD = os.getenv("WORKSHOP_NAME_FIELD", "Name")
WORKSHOP_START_FIELD = os.getenv("WORKSHOP_START_FIELD", "Start_Time__c")
WORKSHOP_END_FIELD = os.getenv("WORKSHOP_END_FIELD", "End_Time__c")
WORKSHOP_WEEKDAY_FIELD = os.getenv("WORKSHOP_WEEKDAY_FIELD", "Weekday__c")
WORKSHOP_DATE_FIELD = os.getenv("WORKSHOP_DATE_FIELD", "Workshop_Date__c")
WORKSHOP_LOOKAHEAD_MINUTES = int(os.getenv("WORKSHOP_LOOKAHEAD_MINUTES", "30"))

# IANA timezone used for all workshop time-of-day matching, independent of the
# host OS clock's timezone (Raspberry Pi OS defaults to UTC unless explicitly
# reconfigured, which previously caused workshop times to be off by the local
# UTC offset). Leave blank to fall back to the OS's local timezone.
WORKSHOP_TIMEZONE = os.getenv("WORKSHOP_TIMEZONE", "Pacific/Auckland")

# Workshop calendar sync (one-off events pulled from an ICS feed; see
# workshop_calendar_sync.py). Sync is disabled when WORKSHOP_ICS_URL is unset.
WORKSHOP_ICS_URL = os.getenv("WORKSHOP_ICS_URL", "")
WORKSHOP_ONEOFF_WEEKDAY = int(os.getenv("WORKSHOP_ONEOFF_WEEKDAY", "9"))
WORKSHOP_SYNC_HOUR = int(os.getenv("WORKSHOP_SYNC_HOUR", "4"))

PENDING_CARD_SOBJECT = os.getenv("PENDING_CARD_SOBJECT", "Pending_card_registration__c")
PENDING_CARD_SERIAL_FIELD = os.getenv("PENDING_CARD_SERIAL_FIELD", "Card_Serial__c")

# RFID debounce configuration
RFID_DEBOUNCE_SECONDS = float(os.getenv("RFID_DEBOUNCE_SECONDS", "1.0"))

# Error classification keywords for system unavailable feedback
SYSTEM_UNAVAILABLE_KEYWORDS = [
    "salesforce_not_connected",
    "salesforce_error",
    "network error",
    "network_unavailable",
    "importerror",
    "import error",
    "module_not_found",
    "module error",
]

# Salesforce connection singleton
sf: Optional["Salesforce"] = None

# Debounce tracking for RFID reads
_LAST_CARD_SERIAL: Optional[str] = None
_LAST_CARD_SEEN: float = 0.0

class SigninMode(Enum):
    """One-shot mode for the next card scan, cycled by the facilitator button."""

    NORMAL = "normal"
    FACILITATOR = "facilitator"
    SIGN_OUT_ALL = "sign_out_all"


# Order the button cycles through on each press.
_SIGNIN_MODE_CYCLE = [SigninMode.NORMAL, SigninMode.FACILITATOR, SigninMode.SIGN_OUT_ALL]

# One-shot mode for the next created/processed sign-in.
# Accessed from both the main thread and the gpiozero button-press thread;
# always read or write under _FACILITATOR_LOCK.
_NEXT_SIGNIN_MODE: SigninMode = SigninMode.NORMAL
_FACILITATOR_LOCK = threading.Lock()

# If a non-normal mode is armed but no card is scanned within this many
# seconds, it auto-reverts to normal mode. Guarded by _FACILITATOR_LOCK.
_FACILITATOR_TIMEOUT_SECONDS = float(os.getenv("FACILITATOR_TIMEOUT_SECONDS", "45"))
_facilitator_timeout_timer: Optional[threading.Timer] = None

# Network monitor instance
_network_monitor: Optional["NetworkMonitor"] = None
_network_error_displayed: bool = False

# Workshop calendar sync instance
_calendar_sync: Optional["WorkshopCalendarSync"] = None


def _should_show_ready_feedback() -> bool:
    """Check if ready feedback should be shown.

    Returns False if network error is currently displayed.
    """
    return _HAS_HARDWARE_FEEDBACK and not _network_error_displayed


def _on_network_connection_lost() -> None:
    """Callback when network connection is lost."""
    global _network_error_displayed
    LOG.warning("Network connection lost - displaying error")
    if _HAS_HARDWARE_FEEDBACK:
        provide_feedback(FeedbackState.NETWORK_ERROR)
    _network_error_displayed = True


def _on_network_connection_restored() -> None:
    """Callback when network connection is restored."""
    global _network_error_displayed
    LOG.info("Network connection restored - returning to ready state")
    if _HAS_HARDWARE_FEEDBACK:
        _show_idle_feedback()
    _network_error_displayed = False


def _show_idle_feedback() -> None:
    """Show the appropriate idle screen for the current sign-in mode."""
    if not _HAS_HARDWARE_FEEDBACK:
        return

    with _FACILITATOR_LOCK:
        mode = _NEXT_SIGNIN_MODE

    if mode == SigninMode.FACILITATOR:
        provide_feedback(FeedbackState.FACILITATOR_SIGNIN)
    elif mode == SigninMode.SIGN_OUT_ALL:
        provide_feedback(FeedbackState.SIGN_OUT_ALL_MODE)
    else:
        workshop = sf_get_current_workshop() if sf is not None else "No Event"
        provide_feedback(FeedbackState.READY_TO_SCAN, workshop=workshop)


def _facilitator_timeout_expired() -> None:
    """Timer callback: auto-revert to normal mode if still armed and unused.

    Runs on the threading.Timer's own thread, so the flag mutation is
    protected by _FACILITATOR_LOCK like every other access.
    """
    global _NEXT_SIGNIN_MODE, _facilitator_timeout_timer

    with _FACILITATOR_LOCK:
        _facilitator_timeout_timer = None
        if _NEXT_SIGNIN_MODE == SigninMode.NORMAL:
            return
        _NEXT_SIGNIN_MODE = SigninMode.NORMAL

    LOG.info(
        "Sign-in mode timed out after %.0fs with no scan; reverting to normal mode",
        _FACILITATOR_TIMEOUT_SECONDS,
    )
    if _HAS_HARDWARE_FEEDBACK:
        _show_idle_feedback()


def cycle_next_signin_mode() -> None:
    """Cycle the mode for the next card scan: Normal -> Facilitator -> Sign out all -> Normal.

    Called from the gpiozero button-press thread, so the mode mutation is
    protected by _FACILITATOR_LOCK. Hardware feedback is triggered outside
    the lock to keep the critical section short.

    Arming a non-normal mode starts a _FACILITATOR_TIMEOUT_SECONDS timer that
    auto-reverts to normal mode if no card is scanned in time.
    """
    global _NEXT_SIGNIN_MODE, _facilitator_timeout_timer

    with _FACILITATOR_LOCK:
        current_index = _SIGNIN_MODE_CYCLE.index(_NEXT_SIGNIN_MODE)
        new_mode = _SIGNIN_MODE_CYCLE[(current_index + 1) % len(_SIGNIN_MODE_CYCLE)]
        _NEXT_SIGNIN_MODE = new_mode

        if _facilitator_timeout_timer is not None:
            _facilitator_timeout_timer.cancel()
            _facilitator_timeout_timer = None

        if new_mode != SigninMode.NORMAL:
            _facilitator_timeout_timer = threading.Timer(
                _FACILITATOR_TIMEOUT_SECONDS, _facilitator_timeout_expired
            )
            _facilitator_timeout_timer.daemon = True
            _facilitator_timeout_timer.start()

    if new_mode == SigninMode.FACILITATOR:
        LOG.info(
            "Next sign-in armed as facilitator; auto-reverts in %.0fs if unused",
            _FACILITATOR_TIMEOUT_SECONDS,
        )
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(FeedbackState.FACILITATOR_SIGNIN)
    elif new_mode == SigninMode.SIGN_OUT_ALL:
        LOG.info(
            "Sign out all mode armed; auto-reverts in %.0fs if unused",
            _FACILITATOR_TIMEOUT_SECONDS,
        )
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(FeedbackState.SIGN_OUT_ALL_MODE)
    else:
        LOG.info("Sign-in mode reverted to normal")
        if _HAS_HARDWARE_FEEDBACK:
            _show_idle_feedback()


_workshop_tzinfo_cache: Dict[str, Optional[datetime.tzinfo]] = {}


def _get_workshop_tzinfo() -> Optional[datetime.tzinfo]:
    """Resolve WORKSHOP_TIMEZONE to a tzinfo, or None to fall back to OS local time.

    Cached since ZoneInfo lookups touch the system/tzdata database.
    """
    if WORKSHOP_TIMEZONE in _workshop_tzinfo_cache:
        return _workshop_tzinfo_cache[WORKSHOP_TIMEZONE]

    tzinfo: Optional[datetime.tzinfo] = None
    if WORKSHOP_TIMEZONE:
        try:
            tzinfo = ZoneInfo(WORKSHOP_TIMEZONE)
        except Exception as exc:
            LOG.warning(
                "Invalid WORKSHOP_TIMEZONE %r (%s); falling back to OS local time",
                WORKSHOP_TIMEZONE,
                exc,
            )
    _workshop_tzinfo_cache[WORKSHOP_TIMEZONE] = tzinfo
    return tzinfo


def _escape_soql(value: str) -> str:
    """Escape single quotes for SOQL string literals.

    This doubles backslashes before single quotes so the resulting literal
    is safe in the SOQL query string.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _ensure_connected() -> Tuple[bool, Optional[Tuple[int, str]]]:
    """Ensure the module-level Salesforce client is available.

    Returns (True, None) when connected; otherwise (False, (code, message)).
    """
    if sf is None and connect_to_salesforce() is None:
        return False, (500, "salesforce_not_connected")
    return True, None


def _get_connected_sf() -> Optional["Salesforce"]:
    """Return the connected Salesforce client, or None if unavailable.

    Passed to WorkshopCalendarSync so it can (re)connect on its own schedule
    without importing this module.
    """
    connected, _ = _ensure_connected()
    return sf if connected else None


def _format_time(value: Union[None, str, datetime.datetime]) -> str:
    """Normalize time inputs to an ISO-format string.

    If `value` is None, uses current UTC time.
    """
    if value is None:
        return datetime.datetime.now(datetime.UTC).isoformat()
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


def connect_to_salesforce(
    username: Optional[str] = None,
    password: Optional[str] = None,
    security_token: Optional[str] = None,
    domain: Optional[str] = None,
    consumer_key: Optional[str] = None,
    private_key_path: Optional[str] = None,
) -> Optional["Salesforce"]:
    """Connect to Salesforce using JWT Bearer Flow (OAuth 2.0) or legacy password auth.

    Prefers JWT if consumer_key and private_key_path are provided (recommended - REST API only).
    Falls back to username/password (uses SOAP Partner API for login - deprecated).

    Returns a `simple_salesforce.Salesforce` instance or `None` on failure.
    """
    global sf
    try:
        from simple_salesforce import Salesforce as _SF
    except Exception:
        LOG.error("simple_salesforce is not installed or import failed")
        return None

    if sf is not None:
        return sf

    username = username or os.getenv("SF_USERNAME")
    password = password or os.getenv("SF_PASSWORD")
    security_token = security_token or os.getenv("SF_SECURITY_TOKEN")

    try:
        if username and password:
            sf = _SF(username=username, password=password, security_token=security_token or "", domain=domain)  # type: ignore[arg-type]
            LOG.info(
                "Connected to Salesforce using password (legacy - SOAP Partner API)"
            )
            return sf
    except Exception as exc:  # pragma: no cover - runtime
        LOG.exception("Failed to connect to Salesforce: %s", exc)
        sf = None
        return None


def _get_sobject(sobject_name: str):
    """Get a SObject proxy from the global Salesforce client."""
    return getattr(sf, sobject_name, None) if sf else None


def sf_check_pending_card_exists(card_serial: str) -> bool:
    """Check if a card serial number already exists in pending registrations.

    Returns True if the serial exists, False otherwise.
    """
    if not card_serial:
        return False

    connected, err = _ensure_connected()
    if not connected:
        return False

    esc = _escape_soql(card_serial)
    query = f"SELECT Id FROM {PENDING_CARD_SOBJECT} WHERE {PENDING_CARD_SERIAL_FIELD} = '{esc}' LIMIT 1"
    try:
        res = sf.query(query)  # type: ignore[attr-defined]
    except Exception as exc:
        LOG.exception("Failed to check pending card: %s", exc)
        return False

    records = res.get("records", []) if isinstance(res, dict) else []
    return len(records) > 0


def sf_create_pending_card_registration(card_serial: str) -> bool:
    """Create a pending card registration record for an unknown card serial.

    Returns True if successful, False otherwise.
    """
    if not card_serial:
        return False

    connected, err = _ensure_connected()
    if not connected:
        return False

    # Check if it already exists
    if sf_check_pending_card_exists(card_serial):
        LOG.debug("Card serial %s already in pending registrations", card_serial)
        return False

    sobject = _get_sobject(PENDING_CARD_SOBJECT)
    if sobject is None:
        LOG.warning("Pending card registration object not available")
        return False

    payload = {PENDING_CARD_SERIAL_FIELD: card_serial}

    try:
        result = sobject.create(payload)
        LOG.info("Created pending card registration for serial: %s", card_serial)
        return True
    except Exception as exc:
        LOG.exception("Failed to create pending card registration: %s", exc)
        return False


def sf_get_access_card_by_serial_number(
    card_serial: str,
) -> Tuple[int, Union[Dict[str, Any], str]]:
    """Look up an access card record by serial number and return contact.

    Returns (status_code, payload). On success payload is a dict containing
    at least `contact_id` and `raw` (the SF record).
    """
    if not card_serial:
        return 400, "invalid_serial"

    connected, err = _ensure_connected()
    if not connected:
        return err

    esc = _escape_soql(card_serial)
    query = f"SELECT Id, {ACCESS_CARD_CONTACT_FIELD}, {ACCESS_CARD_SERIAL_FIELD} FROM {ACCESS_CARD_SOBJECT} WHERE {ACCESS_CARD_SERIAL_FIELD} = '{esc}' LIMIT 1"
    try:
        res = sf.query(query)  # type: ignore[attr-defined]
    except Exception as exc:
        LOG.exception("Salesforce query failed: %s", exc)
        return 500, f"salesforce_error: {exc}"

    records = res.get("records", []) if isinstance(res, dict) else []
    if not records:
        # Card not found - log to pending registrations in the background
        sf_create_pending_card_registration(card_serial)
        return 404, "card_not_exists"

    rec = records[0]
    contact_id = rec.get(ACCESS_CARD_CONTACT_FIELD) if isinstance(rec, dict) else None
    return 200, {"contact_id": contact_id, "raw": rec}


def sf_get_contact_name(contact_id: str) -> Tuple[int, str]:
    """Fetch the contact's full name from Salesforce.

    Returns (status_code, full_name). On success, full_name is "FirstName LastName".
    Returns empty string if contact not found or on error.
    """
    if not contact_id:
        return 400, ""

    connected, err = _ensure_connected()
    if not connected:
        return err[0], ""

    esc = _escape_soql(contact_id)
    query = f"SELECT FirstName, LastName FROM Contact WHERE Id = '{esc}' LIMIT 1"

    try:
        res = sf.query(query)  # type: ignore[attr-defined]
    except Exception as exc:
        LOG.exception("Failed to query contact: %s", exc)
        return 500, ""

    records = res.get("records", []) if isinstance(res, dict) else []
    if not records:
        LOG.warning("Contact not found: %s", contact_id)
        return 404, ""

    rec = records[0]
    first_name = rec.get("FirstName", "") or ""
    last_name = rec.get("LastName", "") or ""

    # Combine first and last name with a space, strip extra whitespace
    full_name = f"{first_name} {last_name}".strip()
    return 200, full_name


def sf_get_open_signins_for_contact(
    contact_id: str,
) -> Tuple[int, Union[List[Any], str]]:
    if not contact_id:
        return 400, "invalid_contact_id"

    connected, err = _ensure_connected()
    if not connected:
        return err

    esc = _escape_soql(contact_id)
    query = f"SELECT Id FROM {SIGNIN_SOBJECT} WHERE {SIGNIN_CONTACT_FIELD} = '{esc}' AND {SIGNIN_SIGNOUT_FIELD} = NULL"

    try:
        res = sf.query(query)  # type: ignore[attr-defined]
    except Exception as exc:
        LOG.exception("Salesforce query failed: %s", exc)
        return 500, f"salesforce_error: {exc}"

    records = res.get("records", []) if isinstance(res, dict) else []
    if not records:
        return 404, "open_signins_not_exists"

    return 200, records


def sf_sign_out_signins_by_id(
    ids: Union[List[str], str],
    time_to_signout: Union[None, str, datetime.datetime] = None,
) -> Tuple[int, str]:
    if not ids:
        return 400, "no_ids_provided"

    connected, err = _ensure_connected()
    if not connected:
        return err

    if isinstance(ids, str):
        ids_list = [i.strip() for i in ids.split(",") if i.strip()]
    else:
        ids_list = ids

    time_val = _format_time(time_to_signout)

    sobject = _get_sobject(SIGNIN_SOBJECT)
    if sobject is None:
        return 500, "sobject_sign_ins_not_available"

    try:
        for record_id in ids_list:
            # When signing out, ensure the facilitator flag is cleared (falsey)
            payload = {SIGNIN_SIGNOUT_FIELD: time_val, SIGNIN_FACILITATOR_FIELD: False}
            sobject.update(record_id, payload)
    except Exception as exc:
        LOG.exception("Failed to update sign-out: %s", exc)
        return 500, f"salesforce_error: {exc}"

    return 200, "all_records_signed_out"


def sf_contact_is_facilitator(contact_id: str) -> bool:
    """Return True if any sign-in record for this contact marks them a facilitator."""
    if not contact_id:
        return False

    connected, err = _ensure_connected()
    if not connected:
        return False

    esc = _escape_soql(contact_id)
    query = (
        f"SELECT Id FROM {SIGNIN_SOBJECT} "
        f"WHERE {SIGNIN_CONTACT_FIELD} = '{esc}' AND {SIGNIN_FACILITATOR_FIELD} = true "
        f"LIMIT 1"
    )
    try:
        res = sf.query(query)  # type: ignore[attr-defined]
    except Exception as exc:
        LOG.exception("Failed to check facilitator status: %s", exc)
        return False

    records = res.get("records", []) if isinstance(res, dict) else []
    return bool(records)


def sf_get_all_open_signins() -> Tuple[int, Union[List[Any], str]]:
    connected, err = _ensure_connected()
    if not connected:
        return err

    query = f"SELECT Id FROM {SIGNIN_SOBJECT} WHERE {SIGNIN_SIGNOUT_FIELD} = NULL"

    try:
        res = sf.query(query)  # type: ignore[attr-defined]
    except Exception as exc:
        LOG.exception("Salesforce query failed: %s", exc)
        return 500, f"salesforce_error: {exc}"

    records = res.get("records", []) if isinstance(res, dict) else []
    if not records:
        return 404, "open_signins_not_exists"

    return 200, records


def sf_sign_out_all_active_signins(
    time_to_signout: Union[None, str, datetime.datetime] = None,
) -> Tuple[int, str]:
    """Sign out every currently open sign-in, regardless of contact."""
    open_status, open_signins = sf_get_all_open_signins()
    if open_status == 404:
        return 200, "no_active_signins"
    if open_status != 200:
        return open_status, str(open_signins)

    ids = [r.get("Id") for r in open_signins if isinstance(r, dict) and r.get("Id")]
    if not ids:
        return 200, "no_active_signins"

    return sf_sign_out_signins_by_id(ids, time_to_signout)


def sf_get_current_workshop(now: Optional[datetime.datetime] = None) -> str:
    """Determine the workshop name based on current time and workshop schedule.

    Returns the name of the currently running or upcoming workshop,
    or "No Event" if none can be determined.
    """
    # Use WORKSHOP_TIMEZONE (handles DST) for matching against workshop times,
    # not the host OS clock's timezone - see _get_workshop_tzinfo.
    now_local = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(
        _get_workshop_tzinfo()
    )
    # Convert to 1=Sunday weekday format (Python's weekday() is 0=Monday)
    # Python: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    # Salesforce: Sun=1, Mon=2, Tue=3, Wed=4, Thu=5, Fri=6, Sat=7
    weekday_sf = (now_local.weekday() + 2) % 7
    if weekday_sf == 0:
        weekday_sf = 7

    connected, err = _ensure_connected()
    if not connected:
        LOG.warning("Cannot determine workshop: not connected to Salesforce")
        return "No Event"

    # Match recurring weekly workshops (by weekday) as well as one-off
    # workshops synced from the ICS calendar feed for today's date - see
    # workshop_calendar_sync.py.
    today_str = now_local.date().isoformat()
    query = (
        f"SELECT {WORKSHOP_NAME_FIELD}, {WORKSHOP_START_FIELD}, {WORKSHOP_END_FIELD} "
        f"FROM {WORKSHOP_SOBJECT} "
        f"WHERE {WORKSHOP_WEEKDAY_FIELD} = {weekday_sf} OR {WORKSHOP_DATE_FIELD} = {today_str}"
    )

    try:
        res = sf.query(query)  # type: ignore[attr-defined]
    except Exception as exc:
        LOG.exception("Failed to query workshops: %s", exc)
        return "No Event"

    records = res.get("records", []) if isinstance(res, dict) else []
    if not records:
        LOG.info(f"No workshops found for weekday {weekday_sf}")
        return "No Event"

    # Parse workshop times and find the best match using local time
    current_time = now_local.time()
    lookahead = datetime.timedelta(minutes=WORKSHOP_LOOKAHEAD_MINUTES)

    best_match = None
    best_score = float("inf")

    for record in records:
        if not isinstance(record, dict):
            continue

        start_str = record.get(WORKSHOP_START_FIELD)
        end_str = record.get(WORKSHOP_END_FIELD)
        name = record.get(WORKSHOP_NAME_FIELD, "No Event")

        if not start_str or not end_str:
            continue

        try:
            # Parse time strings (assuming HH:MM or HH:MM:SS format)
            start_time = datetime.datetime.strptime(
                str(start_str).split(".")[0], "%H:%M:%S"
            ).time()
            end_time = datetime.datetime.strptime(
                str(end_str).split(".")[0], "%H:%M:%S"
            ).time()
        except ValueError:
            try:
                start_time = datetime.datetime.strptime(str(start_str), "%H:%M").time()
                end_time = datetime.datetime.strptime(str(end_str), "%H:%M").time()
            except ValueError:
                LOG.warning(f"Could not parse workshop times: {start_str}, {end_str}")
                continue

        # Check if event is currently running (local time)
        if start_time <= current_time <= end_time:
            return name

        # Check if event is starting soon (local time)
        start_datetime = datetime.datetime.combine(
            now_local.date(), start_time, tzinfo=now_local.tzinfo
        )
        time_until_start = (start_datetime - now_local).total_seconds()

        if 0 <= time_until_start <= lookahead.total_seconds():
            if time_until_start < best_score:
                best_score = time_until_start
                best_match = name

    result = best_match if best_match else "No Event"
    return result


def sf_create_signin_for_contact(
    contact_id: str,
    time_to_signin: Union[None, str, datetime.datetime] = None,
    is_facilitator: bool = False,
) -> Tuple[int, Any]:
    if not contact_id:
        return 400, "invalid_contact_id"

    connected, err = _ensure_connected()
    if not connected:
        return err

    time_val = _format_time(time_to_signin)

    # Determine workshop/event
    signin_time = (
        time_to_signin if isinstance(time_to_signin, datetime.datetime) else None
    )
    workshop_name = sf_get_current_workshop(signin_time)

    # Fetch contact name
    name_status, contact_name = sf_get_contact_name(contact_id)
    if name_status != 200:
        LOG.warning(
            "Could not fetch contact name for %s, proceeding without name", contact_id
        )
        contact_name = ""

    sobject = _get_sobject(SIGNIN_SOBJECT)
    if sobject is None:
        return 500, "sobject_sign_ins_not_available"

    payload = {
        SIGNIN_CONTACT_FIELD: contact_id,
        SIGNIN_SIGNIN_FIELD: time_val,
        SIGNIN_WORKSHOP_FIELD: workshop_name,
        SIGNIN_NAME_FIELD: contact_name or "",
    }
    if is_facilitator:
        payload[SIGNIN_FACILITATOR_FIELD] = True
        payload[SIGNIN_PERSON_WAS_SESSION_FACILITATOR_FIELD] = True
    if SIGNIN_RECORDTYPE_ID:
        payload["RecordTypeId"] = SIGNIN_RECORDTYPE_ID

    try:
        result = sobject.create(payload)
    except Exception as exc:
        LOG.exception("Failed to create signin: %s", exc)
        return 500, f"salesforce_error: {exc}"

    return 201, result


def process_signin_from_card_serial(
    card_serial: str, now: Optional[datetime.datetime] = None
) -> Tuple[int, Any]:
    """Main processing flow for a card serial: sign out open signins or create a new signin."""
    global _NEXT_SIGNIN_MODE, _facilitator_timeout_timer

    # Atomically capture and clear the armed mode so no error path can leave
    # it armed for a future scan by a different person.
    with _FACILITATOR_LOCK:
        mode = _NEXT_SIGNIN_MODE
        _NEXT_SIGNIN_MODE = SigninMode.NORMAL
        timeout_timer = _facilitator_timeout_timer
        _facilitator_timeout_timer = None

    if timeout_timer is not None:
        timeout_timer.cancel()

    if not card_serial:
        return 400, "invalid_serial"

    now = now or datetime.datetime.now(datetime.UTC)

    status, card_info = sf_get_access_card_by_serial_number(card_serial)
    if status != 200:
        return status, card_info

    contact_id = card_info.get("contact_id") if isinstance(card_info, dict) else None
    if not contact_id:
        return 400, "card_has_no_contact"

    if mode == SigninMode.SIGN_OUT_ALL:
        if not sf_contact_is_facilitator(contact_id):
            LOG.warning(
                "Sign out all requested but card is not a facilitator's card: %s",
                contact_id,
            )
            return 403, "not_facilitator_card"

        signout_status, signout_msg = sf_sign_out_all_active_signins(now)
        if signout_status != 200:
            return signout_status, signout_msg
        return 200, "all_active_signins_signed_out"

    facilitator_mode = mode == SigninMode.FACILITATOR

    open_status, open_signins = sf_get_open_signins_for_contact(contact_id)
    if open_status == 200:
        ids = [r.get("Id") for r in open_signins if isinstance(r, dict) and r.get("Id")]
        if ids:
            signout_status, signout_msg = sf_sign_out_signins_by_id(ids, now)
            if signout_status == 200:
                if not facilitator_mode:
                    return 200, "successfully_signed_out"
            else:
                return signout_status, signout_msg
    elif open_status != 404:
        # If error is not 404 (no records found), return the error
        return open_status, open_signins

    # No open sign-ins found (or signed out in facilitator mode), create a new one
    create_status, create_result = sf_create_signin_for_contact(
        contact_id, now, is_facilitator=facilitator_mode
    )
    return create_status, create_result


def terminal_entry() -> Tuple[int, Any]:
    try:
        serial = input("Enter card serial: ").strip()
    except Exception as exc:
        result = 500, f"input_error: {exc}"
        feedback(result, method="terminal")
        return result

    # Show processing feedback
    if _HAS_HARDWARE_FEEDBACK:
        provide_feedback(FeedbackState.PROCESSING_SCAN)

    result = process_signin_from_card_serial(serial)
    feedback(result, method="terminal")
    return result


def rfid_entry() -> Tuple[int, Any]:
    global _LAST_CARD_SERIAL, _LAST_CARD_SEEN

    try:
        from mfrc522 import SimpleMFRC522

        reader = SimpleMFRC522()
    except Exception as exc:
        result = 500, f"rfid_module_error: {exc}"
        feedback(result, method="rfid")
        return result

    try:
        _id, _text = reader.read()

        # Extract the 4-byte UID (strip off checksum byte if present)
        # Convert to hex and format as XX:XX:XX:XX to match phone NFC apps
        if isinstance(_id, int):
            # Get hex string without '0x' prefix, pad to at least 8 hex chars (4 bytes)
            hex_full = format(_id, "x").upper()
            # Take only the first 8 hex characters (4 bytes)
            hex_4byte = hex_full[:8].zfill(8)
            # Format as XX:XX:XX:XX
            serial = ":".join([hex_4byte[i : i + 2] for i in range(0, 8, 2)])
        else:
            serial = str(_id)

    except Exception as exc:
        # AUTH ERROR from mfrc522 - typically means card read failed
        # Don't process invalid reads
        if "AUTH ERROR" in str(exc) or "status2reg" in str(exc):
            LOG.debug(
                "RFID authentication failed - card may be too far or incompatible"
            )
            return 204, "rfid_auth_failed"
        LOG.exception("RFID read failed: %s", exc)
        result = 500, f"rfid_read_error: {exc}"
        feedback(result, method="rfid")
        return result

    # Validate the card serial is reasonable (not 0 or empty)
    if not serial or serial == "0" or len(serial) < 3:
        LOG.debug(f"Invalid card serial read: {serial}")
        return 204, "invalid_card_serial"

    now_ts = time.monotonic()
    if serial == _LAST_CARD_SERIAL and now_ts - _LAST_CARD_SEEN < RFID_DEBOUNCE_SECONDS:
        _LAST_CARD_SEEN = now_ts
        LOG.debug(f"Debounced duplicate read of {serial}")
        return 204, "debounced"

    _LAST_CARD_SERIAL = serial
    _LAST_CARD_SEEN = now_ts

    # Show processing feedback
    if _HAS_HARDWARE_FEEDBACK:
        provide_feedback(FeedbackState.PROCESSING_SCAN)

    result = process_signin_from_card_serial(serial)
    feedback(result, method="rfid", card_serial=serial)
    return result


def feedback(
    result: Tuple[int, Any], method: str = "unknown", card_serial: str = ""
) -> None:
    """Provide feedback for a signin/signout result using hardware and console.

    Maps status codes to appropriate feedback states:
    - 200/201: Success (signed in or signed out)
    - 404: Card not registered
    - 500 with network/import errors: System unavailable
    - 500 with other errors: Scan/processing error
    - 204: Debounced (no feedback)
    """
    try:
        status, payload = result
    except Exception:
        print(f"[{method}] invalid result: {result}")
        return

    # Determine the message to display - use generic messages instead of IDs
    if isinstance(payload, dict):
        # For dict payloads, don't show the record ID - use empty message
        message = ""
    else:
        message = str(payload)

    # Map status codes to feedback states
    if status == 204:
        # Debounced - provide subtle feedback to acknowledge card was detected
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(FeedbackState.DEBOUNCED)
        return
    elif status == 201:
        # Success - sign-in (201 Created)
        print(f"[{method}] Success: Signed In")
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(FeedbackState.SIGNED_IN, message)
    elif status == 200 and payload == "all_active_signins_signed_out":
        # Success - facilitator-authorized mass sign-out
        print(f"[{method}] Success: Signed Out All")
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(FeedbackState.SIGN_OUT_ALL_SUCCESS)
    elif status == 200:
        # Success - sign-out (200 OK)
        print(f"[{method}] Success: Signed Out")
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(FeedbackState.SIGNED_OUT, message)
    elif status == 403:
        # Sign out all was requested with a non-facilitator card
        print(f"[{method}] Error {status}: Not authorized for sign out all")
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(FeedbackState.NOT_AUTHORIZED, message)
    elif status == 404:
        # Card not found - pass the card serial instead of the error message
        print(f"[{method}] Error {status}: Card not registered")
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(
                FeedbackState.CARD_NOT_EXIST, card_serial if card_serial else message
            )
    elif status == 500:
        # Server error - differentiate between system unavailable and scan error
        payload_str = str(payload).lower()
        if any(keyword in payload_str for keyword in SYSTEM_UNAVAILABLE_KEYWORDS):
            # System unavailable (network/import issues)
            print(f"[{method}] Error {status}: System unavailable - {payload}")
            if _HAS_HARDWARE_FEEDBACK:
                provide_feedback(FeedbackState.SYSTEM_UNAVAILABLE, message)
        else:
            # General scan/processing error
            print(f"[{method}] Error {status}: Scan error - {payload}")
            if _HAS_HARDWARE_FEEDBACK:
                provide_feedback(FeedbackState.SCAN_ERROR, message)
    else:
        # Other errors
        print(f"[{method}] Error {status}: {payload}")
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(FeedbackState.SCAN_ERROR, message)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Process sign-ins by card serial in continuous mode."
    )
    parser.add_argument("--rfid", action="store_true", help="Use RFID reader")
    parser.add_argument("--terminal", action="store_true", help="Use terminal input")
    args = parser.parse_args()

    if not args.rfid and not args.terminal:
        parser.error("Must specify either --rfid or --terminal")

    connect_to_salesforce()

    if _HAS_HARDWARE_FEEDBACK:
        register_facilitator_button(cycle_next_signin_mode)
        register_idle_callback(_show_idle_feedback)

    # Start network monitor
    if _HAS_NETWORK_MONITOR:
        _network_monitor = NetworkMonitor(
            on_connection_lost=_on_network_connection_lost,
            on_connection_restored=_on_network_connection_restored,
        )
        _network_monitor.start()
        LOG.info("Network monitoring enabled")
    else:
        LOG.warning("Network monitoring not available")

    # Start workshop calendar sync (pulls one-off events from an ICS feed)
    if _HAS_CALENDAR_SYNC and WORKSHOP_ICS_URL:
        _calendar_sync = WorkshopCalendarSync(
            ics_url=WORKSHOP_ICS_URL,
            get_sf=_get_connected_sf,
            sobject_name=WORKSHOP_SOBJECT,
            name_field=WORKSHOP_NAME_FIELD,
            start_field=WORKSHOP_START_FIELD,
            end_field=WORKSHOP_END_FIELD,
            weekday_field=WORKSHOP_WEEKDAY_FIELD,
            date_field=WORKSHOP_DATE_FIELD,
            oneoff_weekday=WORKSHOP_ONEOFF_WEEKDAY,
            sync_hour=WORKSHOP_SYNC_HOUR,
            tz=_get_workshop_tzinfo(),
        )
        _calendar_sync.start()
        LOG.info("Workshop calendar sync enabled")
    elif not _HAS_CALENDAR_SYNC:
        LOG.warning("Workshop calendar sync not available (missing dependencies)")
    else:
        LOG.info("WORKSHOP_ICS_URL not set; workshop calendar sync disabled")

    waiting_logged = False

    LOG.info("Running. Press Ctrl+C to stop.")
    try:
        while True:
            if args.rfid:
                if not waiting_logged:
                    LOG.info("Waiting for RFID card...")
                    if _should_show_ready_feedback():
                        _show_idle_feedback()
                    waiting_logged = True
                status, _ = rfid_entry()
                if status in (200, 201):
                    # Wait 6 seconds before showing ready screen again
                    # This allows the success message to display fully
                    time.sleep(6.0)
                    waiting_logged = False
                # Add small delay to prevent excessive polling
                time.sleep(0.1)
            elif args.terminal:
                if not waiting_logged:
                    if _should_show_ready_feedback():
                        _show_idle_feedback()
                    waiting_logged = True
                terminal_entry()
                time.sleep(6.0)  # Wait before showing ready screen again
                waiting_logged = False
    except KeyboardInterrupt:
        LOG.info("\nStopped by user.")
        if _network_monitor is not None:
            _network_monitor.stop()
        if _calendar_sync is not None:
            _calendar_sync.stop()
        if _HAS_HARDWARE_FEEDBACK:
            shutdown_hardware()
        raise SystemExit(0)
