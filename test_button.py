"""Standalone button diagnostic script.

Run this directly on the Pi to test the facilitator button independently
of the main application:

    python test_button.py              # uses FACILITATOR_BUTTON_PIN env var or pin 24
    python test_button.py --pin 25     # test a specific pin
    python test_button.py --scan       # scan all free GPIO pins for button activity

The script tries all four pull/active combinations and tells you which one
actually fires when you press the button.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    from gpiozero import Button
    from gpiozero.exc import GPIOPinInUse
except ImportError:
    print("ERROR: gpiozero is not installed.")
    sys.exit(1)

# Combinations to probe: (pull_up, active_state, description, wiring_hint)
CONFIGS = [
    (True,  None,  "pull_up=True  (internal pull-up)",   "wire button to GND"),
    (False, None,  "pull_up=False (internal pull-down)",  "wire button to 3.3V"),
    (None,  False, "pull_up=None, active_state=False",    "external pull-up, button to GND"),
    (None,  True,  "pull_up=None, active_state=True",     "external pull-down, button to 3.3V"),
]

PRESS_TIMEOUT = 10  # seconds to wait for a press in each config


def test_pin_config(pin: int, pull_up, active_state, description: str, wiring_hint: str) -> bool:
    """Test one pull/active combination. Returns True if a press was detected."""
    print(f"\n  Config : {description}")
    print(f"  Wiring : {wiring_hint}")
    print(f"  Waiting {PRESS_TIMEOUT}s for a button press on GPIO{pin}...")

    try:
        btn = Button(pin, pull_up=pull_up, active_state=active_state, bounce_time=0.05)
    except GPIOPinInUse:
        print("  SKIP: pin is already in use by another process (e.g. RFID IRQ).")
        return False
    except Exception as exc:
        print(f"  SKIP: could not open pin — {exc}")
        return False

    pressed = False
    btn.when_pressed = lambda: None  # keep reference to suppress gc

    deadline = time.monotonic() + PRESS_TIMEOUT
    try:
        while time.monotonic() < deadline:
            if btn.is_pressed:
                pressed = True
                break
            time.sleep(0.05)
    finally:
        btn.close()

    if pressed:
        print(f"  ✓ PRESS DETECTED with this config!")
    else:
        print("  ✗ No press detected.")

    return pressed


def probe_pin(pin: int) -> None:
    print(f"\n{'='*55}")
    print(f" Testing GPIO{pin}")
    print(f"{'='*55}")

    working: list[tuple] = []
    for pull_up, active_state, description, wiring_hint in CONFIGS:
        if test_pin_config(pin, pull_up, active_state, description, wiring_hint):
            working.append((pull_up, active_state, description, wiring_hint))

    print(f"\n{'─'*55}")
    if working:
        print(f"GPIO{pin} — working configuration(s):")
        for pull_up, active_state, description, wiring_hint in working:
            print(f"  • {description}  →  {wiring_hint}")
        print()
        # Show the gpiozero Button() call to use
        pu, ast, desc, hint = working[0]
        args = f"pull_up={pu!r}"
        if ast is not None:
            args += f", active_state={ast!r}"
        print(f"  Suggested code:  Button({pin}, {args}, bounce_time=0.2)")
    else:
        print(f"GPIO{pin} — no press detected in any configuration.")
        print("  Check: correct physical pin, no pin conflict, button continuity.")


def scan_free_pins() -> None:
    """Try a quick pull_up=True test on all commonly-free GPIO pins."""
    candidates = [4, 5, 6, 12, 13, 16, 19, 20, 21, 24, 25, 26]
    print("\nScanning pins (pull_up=True, button to GND). Press button when prompted.")
    for pin in candidates:
        print(f"\n  GPIO{pin} — press now (3s)...")
        try:
            btn = Button(pin, pull_up=True, bounce_time=0.05)
        except Exception as exc:
            print(f"  SKIP ({exc})")
            continue
        found = False
        deadline = time.monotonic() + 3
        try:
            while time.monotonic() < deadline:
                if btn.is_pressed:
                    found = True
                    break
                time.sleep(0.05)
        finally:
            btn.close()
        if found:
            print(f"  ✓ GPIO{pin} detected a press!")


if __name__ == "__main__":
    import os

    parser = argparse.ArgumentParser(description="Facilitator button diagnostic")
    parser.add_argument(
        "--pin",
        type=int,
        default=int(os.getenv("FACILITATOR_BUTTON_PIN", "24")),
        help="BCM GPIO pin to test (default: FACILITATOR_BUTTON_PIN env var or 24)",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan all common free GPIO pins for button activity",
    )
    args = parser.parse_args()

    if args.scan:
        scan_free_pins()
    else:
        probe_pin(args.pin)
