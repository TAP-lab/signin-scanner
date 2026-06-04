"""Hardware feedback module for sign-in scanner.

This module provides feedback using RGB LED, passive piezo buzzer, and OLED display.
All hardware interfaces gracefully degrade when hardware is not available.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from enum import Enum
from typing import Any, List, Optional, Tuple

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

# Hardware availability flags
_HAS_GPIO = False
_HAS_OLED = False

# Hardware initialization flag
_hardware_initialized = False

# LED auto-clear timer
_led_clear_timer: Optional[threading.Timer] = None

# Hardware instances
_rgb_led: Optional[Any] = None
_piezo: Optional[Any] = None
_facilitator_button: Optional[Any] = None
_oled: Optional[Any] = None
_draw: Optional[Any] = None
_font: Optional[Any] = None
_image: Optional[Any] = None
_facilitator_button_callback: Optional[Any] = None
_on_idle_callback: Optional[Any] = None

# Try to import GPIO libraries (gpiozero for RGB LED and piezo)
try:
    from gpiozero import Button, RGBLED, TonalBuzzer
    from gpiozero.tones import Tone

    _HAS_GPIO = True
except ImportError:
    LOG.debug("gpiozero not available - GPIO feedback disabled")

# Try to import OLED libraries
try:
    import board
    import busio
    from PIL import Image, ImageDraw, ImageFont
    import adafruit_ssd1306

    _HAS_OLED = True
except ImportError:
    LOG.debug("OLED libraries not available - display feedback disabled")


class FeedbackState(Enum):
    """Enumeration of feedback states."""

    SIGNED_IN = "signed_in"
    SIGNED_OUT = "signed_out"
    CARD_NOT_EXIST = "card_not_exist"
    SCAN_ERROR = "scan_error"
    SYSTEM_UNAVAILABLE = "system_unavailable"
    NETWORK_ERROR = "network_error"
    READY_TO_SCAN = "ready_to_scan"
    FACILITATOR_SIGNIN = "facilitator_signin"
    PROCESSING_SCAN = "processing_scan"
    DEBOUNCED = "debounced"


# Default GPIO pins (BCM numbering)
RGB_LED_RED_PIN = 17
RGB_LED_GREEN_PIN = 27
RGB_LED_BLUE_PIN = 22
PIEZO_PIN = 23
FACILITATOR_BUTTON_PIN = int(os.getenv("FACILITATOR_BUTTON_PIN", "24"))

# OLED display dimensions
OLED_WIDTH = 128
OLED_HEIGHT = 128


def _initialize_hardware() -> None:
    """Initialize hardware components if available (only runs once)."""
    global _rgb_led, _piezo, _oled, _draw, _font, _image, _hardware_initialized

    # Skip if already initialized
    if _hardware_initialized:
        return

    _hardware_initialized = True

    # Initialize RGB LED
    if _HAS_GPIO and _rgb_led is None:
        try:
            _rgb_led = RGBLED(
                red=RGB_LED_RED_PIN, green=RGB_LED_GREEN_PIN, blue=RGB_LED_BLUE_PIN
            )
            LOG.info(
                "RGB LED initialized on pins R=%d, G=%d, B=%d",
                RGB_LED_RED_PIN,
                RGB_LED_GREEN_PIN,
                RGB_LED_BLUE_PIN,
            )
        except Exception as exc:
            LOG.warning("Failed to initialize RGB LED: %s", exc)
            _rgb_led = None

    # Initialize piezo buzzer
    if _HAS_GPIO and _piezo is None:
        try:
            _piezo = TonalBuzzer(PIEZO_PIN)
            LOG.info("Piezo buzzer initialized on pin %d", PIEZO_PIN)
        except Exception as exc:
            LOG.warning("Failed to initialize piezo buzzer: %s", exc)
            _piezo = None

    # Initialize OLED display
    if _HAS_OLED and _oled is None:
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            _oled = adafruit_ssd1306.SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c)
            _oled.fill(0)
            _oled.show()

            # Use 8-bit greyscale so Pillow can anti-alias TrueType glyphs.
            # The image is thresholded to 1-bit just before sending to the display.
            _image = Image.new("L", (_oled.width, _oled.height))
            _draw = ImageDraw.Draw(_image)

            # Try to load a TrueType font from common locations
            _font = None
            font_paths = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            ]
            for font_path in font_paths:
                try:
                    _font = ImageFont.truetype(font_path, 14)
                    break
                except Exception:
                    continue

            if _font is None:
                _font = ImageFont.load_default()

            LOG.info("OLED display initialized (%dx%d)", OLED_WIDTH, OLED_HEIGHT)
        except Exception as exc:
            LOG.warning("Failed to initialize OLED display: %s", exc)
            _oled = None


def _set_rgb_color(red: float, green: float, blue: float) -> None:
    """Set RGB LED color (values 0.0-1.0)."""
    if _rgb_led is not None:
        try:
            _rgb_led.color = (red, green, blue)
        except Exception as exc:
            LOG.warning("Failed to set RGB color: %s", exc)


def _play_tone(frequency: float, duration: float) -> None:
    """Play a tone on the piezo buzzer.

    Args:
        frequency: Tone frequency in Hz (mapped to device's working range).
        duration: How long to play the tone in seconds.
    """
    if _piezo is not None:
        try:
            # Map input frequency to working frequencies (60-850 Hz)
            # Based on device testing: [60, 80, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 800, 850]
            if frequency < 600:
                output_freq = 350  # Low tone
            elif frequency < 900:
                output_freq = 600  # Medium tone
            else:
                output_freq = 800  # High tone

            _piezo.play(Tone(output_freq))
            time.sleep(duration)
            _piezo.stop()
        except Exception as exc:
            LOG.debug("Failed to play tone at %.1f Hz: %s", frequency, exc)


def _play_beep_pattern(pattern: List[Tuple[float, float]]) -> None:
    """Play a pattern of beeps (frequency, duration) tuples."""
    for frequency, duration in pattern:
        _play_tone(frequency, duration)
        time.sleep(0.05)  # Short pause between beeps


def _handle_facilitator_button_pressed() -> None:
    """Handle facilitator button presses."""
    if _facilitator_button_callback is not None:
        try:
            _facilitator_button_callback()
        except Exception as exc:
            LOG.exception("Facilitator button callback failed: %s", exc)


def _measure_text(text: str) -> int:
    """Return the pixel width of text rendered in the current font."""
    if _draw is None:
        return len(text) * 8
    try:
        return int(_draw.textbbox((0, 0), text, font=_font)[2])
    except Exception:
        try:
            w, _ = _draw.textsize(text, font=_font)  # type: ignore[attr-defined]
            return w
        except Exception:
            return len(text) * 8


def _wrap_and_clip(lines: List[str], max_width: int, max_lines: int) -> List[str]:
    """Word-wrap lines to fit max_width pixels, then cap at max_lines.

    Lines that cannot be word-wrapped (e.g. a single long word) are truncated
    with an ellipsis.
    """
    result: List[str] = []
    for raw in lines:
        if len(result) >= max_lines:
            break
        if _measure_text(raw) <= max_width:
            result.append(raw)
            continue
        # Word-wrap
        words = raw.split()
        current = ""
        for word in words:
            if len(result) >= max_lines:
                break
            candidate = (current + " " + word).strip() if current else word
            if _measure_text(candidate) <= max_width:
                current = candidate
            else:
                if current:
                    result.append(current)
                current = word
        if current and len(result) < max_lines:
            result.append(current)

    # Truncate any line that still overflows (e.g. a single word wider than the display)
    clipped: List[str] = []
    for line in result:
        if _measure_text(line) <= max_width:
            clipped.append(line)
        else:
            while line and _measure_text(line + "…") > max_width:
                line = line[:-1]
            clipped.append(line + "…")
    return clipped


def _display_text(lines: List[str], clear: bool = True) -> None:
    """Display text on OLED screen.

    Lines are word-wrapped to fit within the horizontal margins and clipped
    to the number of lines that fit vertically below y_offset.
    """
    if _oled is None or _draw is None or _image is None:
        return

    try:
        if clear:
            _draw.rectangle((0, 0, _oled.width, _oled.height), outline=0, fill=0)

        x_margin = 10
        y_offset = 74
        line_height = 18
        max_width = _oled.width - 2 * x_margin
        max_lines = max(1, (_oled.height - y_offset) // line_height)

        for line in _wrap_and_clip(lines, max_width, max_lines):
            _draw.text((x_margin, y_offset), line, font=_font, fill=255)
            y_offset += line_height

        _oled.image(_image.convert("1"))
        _oled.show()
    except Exception as exc:
        LOG.warning("Failed to display text on OLED: %s", exc)


def _schedule_led_clear(delay_seconds: float = 3.0) -> None:
    """Schedule LED to turn off after a delay using a background timer.

    Note: This only clears the LED, not the OLED display.

    Args:
        delay_seconds: Seconds to wait before turning off LED (default 3.0)
    """
    global _led_clear_timer

    # Cancel any existing timer
    if _led_clear_timer is not None:
        _led_clear_timer.cancel()

    # Define the clear function - only clears LED, not OLED
    def _clear_led():
        if _rgb_led is not None:
            try:
                _rgb_led.off()
                LOG.debug("LED auto-cleared after timeout")
            except Exception as exc:
                LOG.warning("Failed to auto-clear LED: %s", exc)

    # Schedule new timer
    _led_clear_timer = threading.Timer(delay_seconds, _clear_led)
    _led_clear_timer.daemon = True
    _led_clear_timer.start()


def _schedule_led_and_oled_clear(delay_seconds: float = 3.0) -> None:
    """Schedule LED and OLED to turn off after a delay, then show ready state.

    Args:
        delay_seconds: Seconds to wait before returning to ready state (default 3.0)
    """
    global _led_clear_timer

    # Cancel any existing timer
    if _led_clear_timer is not None:
        _led_clear_timer.cancel()

    # Define the clear function - returns to idle state via registered callback
    def _clear_and_ready():
        if _on_idle_callback is not None:
            try:
                _on_idle_callback()
            except Exception as exc:
                LOG.exception("Idle callback failed: %s", exc)
        else:
            provide_feedback(FeedbackState.READY_TO_SCAN)

    # Schedule new timer
    _led_clear_timer = threading.Timer(delay_seconds, _clear_and_ready)
    _led_clear_timer.daemon = True
    _led_clear_timer.start()


def provide_feedback(
    state: FeedbackState,
    message: str = "",
    workshop: str = "",
) -> None:
    """Provide hardware feedback for a given state.

    Args:
        state: The feedback state to display
        message: Optional additional message text

    Note: For result states (signed in/out, errors), the LED automatically
    turns off after 3 seconds. For persistent states (ready, processing),
    the LED stays on until the next feedback is shown.
    """
    _initialize_hardware()

    # Cancel any pending LED clear timer when showing new feedback
    global _led_clear_timer
    if _led_clear_timer is not None:
        _led_clear_timer.cancel()
        _led_clear_timer = None

    if state == FeedbackState.SIGNED_IN:
        # Green LED, success beep, display "Signed In"
        _set_rgb_color(0, 1, 0)  # Green
        _play_beep_pattern([(1200, 0.1), (1500, 0.2)])  # Rising beep
        _display_text(["Welcome!", "You have been", "signed in"])
        LOG.info("Feedback: Signed In")
        _schedule_led_clear(6.0)  # Auto-clear LED after 6 seconds

    elif state == FeedbackState.SIGNED_OUT:
        # Blue LED, double beep, display "Signed Out"
        _set_rgb_color(0, 0, 1)  # Blue
        _play_beep_pattern([(1500, 0.1), (1200, 0.2)])  # Falling beep
        _display_text(["Goodbye!", "You have been", "signed out"])
        LOG.info("Feedback: Signed Out")
        _schedule_led_clear(6.0)  # Auto-clear LED after 6 seconds

    elif state == FeedbackState.CARD_NOT_EXIST:
        # Red LED, error beeps, display error
        _set_rgb_color(1, 0, 0)  # Red
        _play_beep_pattern([(800, 0.15), (800, 0.15), (800, 0.15)])  # Triple low beep
        display_lines = ["Unknown Card"]
        if message:
            display_lines.append("Serial:")
            display_lines.append(message)
        else:
            display_lines.extend(["Card not", "registered"])
        _display_text(display_lines)
        LOG.warning(
            "Feedback: Card Not Exist - %s", message if message else "no serial"
        )
        _schedule_led_and_oled_clear(8.0)  # Auto-clear LED and OLED after 8 seconds

    elif state == FeedbackState.SCAN_ERROR:
        # Red LED, error tone, display error
        _set_rgb_color(1, 0, 0)  # Red
        _play_beep_pattern([(600, 0.3)])  # Low error tone
        _display_text(["✗ Scan Error", message or "Try again"])
        LOG.error("Feedback: Scan Error - %s", message)
        _schedule_led_and_oled_clear(8.0)  # Auto-clear LED and OLED after 8 seconds

    elif state == FeedbackState.SYSTEM_UNAVAILABLE:
        # Red LED, warning beeps, display system error
        _set_rgb_color(1, 0, 0)  # Red
        _play_beep_pattern([(900, 0.2), (900, 0.2)])  # Double warning beep
        _display_text(["✗ System", "  Unavailable"])
        LOG.error("Feedback: System Unavailable - %s", message)
        _schedule_led_and_oled_clear(8.0)  # Auto-clear LED and OLED after 8 seconds

    elif state == FeedbackState.NETWORK_ERROR:
        # Red LED (solid), no beep, display network error
        # This state persists until network is restored
        _set_rgb_color(1, 0, 0)  # Red
        # No beep - silent warning
        _display_text(["✗ Network Error", "Checking", "connection..."])
        LOG.error("Feedback: Network Error - %s", message)
        # Don't auto-clear for network errors - they persist until resolved

    elif state == FeedbackState.READY_TO_SCAN:
        # Cyan LED (waiting state), no beep, display ready message
        # Turn off LED first to clear any previous state
        if _rgb_led is not None:
            try:
                _rgb_led.off()
            except Exception as exc:
                LOG.warning("Failed to turn off LED: %s", exc)
        _set_rgb_color(0, 1, 1)  # Cyan
        if workshop:
            _display_text(["Sign in to", workshop])
        else:
            _display_text(["Welcome!", "Scan your card", "to sign in or out"])
        LOG.info("Feedback: Ready to Scan")

    elif state == FeedbackState.FACILITATOR_SIGNIN:
        # Orange LED, no beep, display facilitator mode
        if _rgb_led is not None:
            try:
                _rgb_led.off()
            except Exception as exc:
                LOG.warning("Failed to turn off LED: %s", exc)
        _set_rgb_color(1, 0.5, 0)
        _display_text(["Facilitator", "sign in", "Scan your card"])
        LOG.info("Feedback: Facilitator Sign In")

    elif state == FeedbackState.PROCESSING_SCAN:
        # Yellow LED (processing), brief beep, display processing
        _set_rgb_color(1, 1, 0)  # Yellow
        _play_beep_pattern([(600, 0.1)])  # Quick acknowledgment beep
        _display_text(["Processing...", "Please wait"])
        LOG.info("Feedback: Processing Scan")

    elif state == FeedbackState.DEBOUNCED:
        # Brief yellow LED blink to acknowledge card detected but debounced
        _set_rgb_color(1, 1, 0)  # Yellow
        LOG.debug("Feedback: Card Debounced")
        time.sleep(0.1)  # Brief display
        # Turn off LED after debounce acknowledgment
        if _rgb_led is not None:
            try:
                _rgb_led.off()
            except Exception as exc:
                LOG.warning("Failed to turn off LED after debounce: %s", exc)


def clear_feedback() -> None:
    """Clear all feedback (turn off LED, clear display)."""
    _initialize_hardware()

    if _rgb_led is not None:
        try:
            _rgb_led.off()
        except Exception as exc:
            LOG.warning("Failed to turn off RGB LED: %s", exc)

    if _oled is not None:
        try:
            _oled.fill(0)
            _oled.show()
        except Exception as exc:
            LOG.warning("Failed to clear OLED: %s", exc)


def register_facilitator_button(callback: Any) -> bool:
    """Register a callback for the facilitator button.

    Returns True when the button could be configured, False otherwise.
    """
    global _facilitator_button_callback, _facilitator_button

    _facilitator_button_callback = callback
    _initialize_hardware()

    if not _HAS_GPIO:
        return False

    if _facilitator_button is None:
        try:
            _facilitator_button = Button(
                FACILITATOR_BUTTON_PIN, pull_up=False, bounce_time=0.2
            )
            LOG.info("Facilitator button initialized on pin %d", FACILITATOR_BUTTON_PIN)
        except Exception as exc:
            LOG.warning("Failed to initialize facilitator button: %s", exc)
            _facilitator_button = None
            return False

    _facilitator_button.when_pressed = _handle_facilitator_button_pressed
    return True


def register_idle_callback(callback: Any) -> None:
    """Register a callback invoked whenever the display returns to the idle/ready state.

    The callback is called by the auto-clear timer after error states (CARD_NOT_EXIST,
    SCAN_ERROR, SYSTEM_UNAVAILABLE) expire.  Registering from signin.py lets the timer
    show the correct idle screen (normal or facilitator) without feedback_hardware.py
    needing to know about signin state.
    """
    global _on_idle_callback
    _on_idle_callback = callback


def shutdown_hardware() -> None:
    """Shutdown and cleanup hardware resources."""
    global _rgb_led, _piezo, _facilitator_button, _oled, _led_clear_timer

    # Cancel any pending LED clear timer
    if _led_clear_timer is not None:
        _led_clear_timer.cancel()
        _led_clear_timer = None

    if _facilitator_button is not None:
        try:
            _facilitator_button.close()
        except Exception as exc:
            LOG.warning("Failed to close facilitator button: %s", exc)
        _facilitator_button = None

    clear_feedback()

    if _rgb_led is not None:
        try:
            _rgb_led.close()
        except Exception as exc:
            LOG.warning("Failed to close RGB LED: %s", exc)
        _rgb_led = None

    if _piezo is not None:
        try:
            _piezo.close()
        except Exception as exc:
            LOG.warning("Failed to close piezo buzzer: %s", exc)
        _piezo = None

    _oled = None

    LOG.info("Hardware feedback shutdown complete")
