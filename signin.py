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
import time
from typing import Any, Dict, List, Optional, Tuple, Union

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
    from feedback_hardware import FeedbackState, provide_feedback, clear_feedback, shutdown_hardware
    _HAS_HARDWARE_FEEDBACK = True
except ImportError:
    _HAS_HARDWARE_FEEDBACK = False

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
SIGNIN_RECORDTYPE_ID = os.getenv("SIGNIN_RECORDTYPE_ID", "")

WORKSHOP_SOBJECT = os.getenv("WORKSHOP_SOBJECT", "TAP_lab_Workshop__c")
WORKSHOP_NAME_FIELD = os.getenv("WORKSHOP_NAME_FIELD", "Name")
WORKSHOP_START_FIELD = os.getenv("WORKSHOP_START_FIELD", "Start_Time__c")
WORKSHOP_END_FIELD = os.getenv("WORKSHOP_END_FIELD", "End_Time__c")
WORKSHOP_WEEKDAY_FIELD = os.getenv("WORKSHOP_WEEKDAY_FIELD", "Weekday__c")
WORKSHOP_LOOKAHEAD_MINUTES = int(os.getenv("WORKSHOP_LOOKAHEAD_MINUTES", "30"))

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
) -> Optional["Salesforce"]:
    """Connect to Salesforce using provided credentials or environment.

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
    domain = domain or os.getenv("SF_DOMAIN")

    try:
        if username and password:
            sf = _SF(username=username, password=password, security_token=security_token or "", domain=domain)  # type: ignore[arg-type]
        else:
            # Allow unauthenticated creation (may work if local mocking is used)
            sf = _SF()  # type: ignore[misc]
        LOG.info("Connected to Salesforce")
        return sf
    except Exception as exc:  # pragma: no cover - runtime
        LOG.exception("Failed to connect to Salesforce: %s", exc)
        sf = None
        return None


def _get_sobject(sobject_name: str):
    """Get a SObject proxy from the global Salesforce client."""
    return getattr(sf, sobject_name, None) if sf else None


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
        return 404, "card_not_exists"

    rec = records[0]
    contact_id = rec.get(ACCESS_CARD_CONTACT_FIELD) if isinstance(rec, dict) else None
    return 200, {"contact_id": contact_id, "raw": rec}


def sf_get_open_signins_for_contact(
    contact_id: str,
) -> Tuple[int, Union[List[Any], str]]:
    if not contact_id:
        return 400, "invalid_contact_id"

    connected, err = _ensure_connected()
    if not connected:
        return err

    esc = _escape_soql(contact_id)
    query = f"SELECT Id FROM {SIGNIN_SOBJECT} WHERE {SIGNIN_CONTACT_FIELD} = '{esc}'"
    if SIGNIN_RECORDTYPE_ID:
        query += f" AND recordTypeId = '{_escape_soql(SIGNIN_RECORDTYPE_ID)}'"
    query += f" AND {SIGNIN_SIGNOUT_FIELD} = NULL"

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
            sobject.update(record_id, {SIGNIN_SIGNOUT_FIELD: time_val})
    except Exception as exc:
        LOG.exception("Failed to update sign-out: %s", exc)
        return 500, f"salesforce_error: {exc}"

    return 200, "all_records_signed_out"


def sf_get_current_workshop(now: Optional[datetime.datetime] = None) -> str:
    """Determine the workshop name based on current time and workshop schedule.

    Returns the name of the currently running or upcoming workshop,
    or "No Event" if none can be determined.
    """
    # Use OS local timezone (handles DST) for matching against workshop times
    now_local = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone()
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

    query = f"SELECT {WORKSHOP_NAME_FIELD}, {WORKSHOP_START_FIELD}, {WORKSHOP_END_FIELD} FROM {WORKSHOP_SOBJECT} WHERE {WORKSHOP_WEEKDAY_FIELD} = {weekday_sf}"

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
    contact_id: str, time_to_signin: Union[None, str, datetime.datetime] = None
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

    sobject = _get_sobject(SIGNIN_SOBJECT)
    if sobject is None:
        return 500, "sobject_sign_ins_not_available"

    payload = {
        SIGNIN_CONTACT_FIELD: contact_id,
        SIGNIN_SIGNIN_FIELD: time_val,
        SIGNIN_WORKSHOP_FIELD: workshop_name,
    }
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
    if not card_serial:
        return 400, "invalid_serial"

    now = now or datetime.datetime.now(datetime.UTC)

    status, card_info = sf_get_access_card_by_serial_number(card_serial)
    if status != 200:
        return status, card_info

    contact_id = card_info.get("contact_id") if isinstance(card_info, dict) else None
    if not contact_id:
        return 400, "card_has_no_contact"

    open_status, open_signins = sf_get_open_signins_for_contact(contact_id)
    if open_status == 200:
        ids = [r.get("Id") for r in open_signins if isinstance(r, dict) and r.get("Id")]
        if ids:
            signout_status, signout_msg = sf_sign_out_signins_by_id(ids, now)
            if signout_status == 200:
                return 200, "successfully_signed_out"
            return signout_status, signout_msg
    elif open_status != 404:
        # If error is not 404 (no records found), return the error
        return open_status, open_signins

    # No open sign-ins found, create a new one
    create_status, create_result = sf_create_signin_for_contact(contact_id, now)
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
        serial = str(_id)
    except Exception as exc:
        LOG.exception("RFID read failed: %s", exc)
        result = 500, f"rfid_read_error: {exc}"
        feedback(result, method="rfid")
        return result

    now_ts = time.monotonic()
    if serial == _LAST_CARD_SERIAL and now_ts - _LAST_CARD_SEEN < RFID_DEBOUNCE_SECONDS:
        _LAST_CARD_SEEN = now_ts
        return 204, "debounced"

    _LAST_CARD_SERIAL = serial
    _LAST_CARD_SEEN = now_ts

    # Show processing feedback
    if _HAS_HARDWARE_FEEDBACK:
        provide_feedback(FeedbackState.PROCESSING_SCAN)

    result = process_signin_from_card_serial(serial)
    feedback(result, method="rfid")
    return result


def feedback(result: Tuple[int, Any], method: str = "unknown") -> None:
    """Provide feedback for a signin/signout result using hardware and console.
    
    Maps status codes to appropriate feedback states:
    - 200/201: Success (signed in or signed out)
    - 404 with "card_not_exists": Card not registered
    - 500 with network/import errors: System unavailable
    - 500 with other errors: Scan/processing error
    - 204: Debounced (no feedback)
    """
    try:
        status, payload = result
    except Exception:
        print(f"[{method}] invalid result: {result}")
        return

    # Determine the message to display
    message = str(payload) if not isinstance(payload, dict) else str(payload.get("id", ""))

    # Map status codes to feedback states
    if status == 204:
        # Debounced - no feedback needed
        return
    elif status in (200, 201):
        # Success - determine if sign-in or sign-out
        if status == 201 or (isinstance(payload, str) and "successfully_signed_out" not in payload):
            # Sign-in (201 created)
            print(f"[{method}] Success: Signed In")
            if _HAS_HARDWARE_FEEDBACK:
                provide_feedback(FeedbackState.SIGNED_IN, message)
        else:
            # Sign-out (200 with sign-out message)
            print(f"[{method}] Success: Signed Out")
            if _HAS_HARDWARE_FEEDBACK:
                provide_feedback(FeedbackState.SIGNED_OUT, message)
    elif status == 404:
        # Card not found
        print(f"[{method}] Error {status}: Card not registered")
        if _HAS_HARDWARE_FEEDBACK:
            provide_feedback(FeedbackState.CARD_NOT_EXIST, message)
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

    waiting_logged = False

    LOG.info("Running. Press Ctrl+C to stop.")
    try:
        while True:
            if args.rfid:
                if not waiting_logged:
                    LOG.info("Waiting for RFID card...")
                    if _HAS_HARDWARE_FEEDBACK:
                        provide_feedback(FeedbackState.READY_TO_SCAN)
                    waiting_logged = True
                rfid_entry()
                waiting_logged = False
            elif args.terminal:
                if _HAS_HARDWARE_FEEDBACK and not waiting_logged:
                    provide_feedback(FeedbackState.READY_TO_SCAN)
                    waiting_logged = True
                terminal_entry()
                waiting_logged = False
    except KeyboardInterrupt:
        LOG.info("\nStopped by user.")
        if _HAS_HARDWARE_FEEDBACK:
            shutdown_hardware()
        raise SystemExit(0)
