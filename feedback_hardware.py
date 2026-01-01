"""Hardware feedback module for sign-in scanner.

This module provides feedback using RGB LED, passive piezo buzzer, and OLED display.
All hardware interfaces gracefully degrade when hardware is not available.
"""

from __future__ import annotations

import logging
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

# Hardware instances
_rgb_led: Optional[Any] = None
_piezo: Optional[Any] = None
_oled: Optional[Any] = None
_draw: Optional[Any] = None
_font: Optional[Any] = None
_image: Optional[Any] = None

# Try to import GPIO libraries (gpiozero for RGB LED and piezo)
try:
    from gpiozero import RGBLED, TonalBuzzer
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
    READY_TO_SCAN = "ready_to_scan"
    PROCESSING_SCAN = "processing_scan"


# Default GPIO pins (BCM numbering)
RGB_LED_RED_PIN = 17
RGB_LED_GREEN_PIN = 27
RGB_LED_BLUE_PIN = 22
PIEZO_PIN = 23

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
            _rgb_led = RGBLED(red=RGB_LED_RED_PIN, green=RGB_LED_GREEN_PIN, blue=RGB_LED_BLUE_PIN)
            LOG.info("RGB LED initialized on pins R=%d, G=%d, B=%d", RGB_LED_RED_PIN, RGB_LED_GREEN_PIN, RGB_LED_BLUE_PIN)
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
            
            # Create blank image for drawing
            _image = Image.new("1", (_oled.width, _oled.height))
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
                    _font = ImageFont.truetype(font_path, 12)
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
    """Play a tone on the piezo buzzer."""
    if _piezo is not None:
        try:
            _piezo.play(Tone(frequency))
            time.sleep(duration)
            _piezo.stop()
        except Exception as exc:
            LOG.warning("Failed to play tone: %s", exc)


def _play_beep_pattern(pattern: List[Tuple[float, float]]) -> None:
    """Play a pattern of beeps (frequency, duration) tuples."""
    for frequency, duration in pattern:
        _play_tone(frequency, duration)
        time.sleep(0.05)  # Short pause between beeps


def _display_text(lines: List[str], clear: bool = True) -> None:
    """Display text on OLED screen."""
    if _oled is None or _draw is None or _image is None:
        return
    
    try:
        if clear:
            _draw.rectangle((0, 0, _oled.width, _oled.height), outline=0, fill=0)
        
        y_offset = 10
        line_height = 18
        
        for line in lines:
            _draw.text((5, y_offset), line, font=_font, fill=255)
            y_offset += line_height
        
        _oled.image(_image)
        _oled.show()
    except Exception as exc:
        LOG.warning("Failed to display text on OLED: %s", exc)


def provide_feedback(state: FeedbackState, message: str = "") -> None:
    """Provide hardware feedback for a given state.
    
    Args:
        state: The feedback state to display
        message: Optional additional message text
    
    Note: The LED color persists until the next feedback state is shown.
    For persistent states (ready to scan), call this to update the LED.
    """
    _initialize_hardware()
    
    if state == FeedbackState.SIGNED_IN:
        # Green LED, success beep, display "Signed In"
        _set_rgb_color(0, 1, 0)  # Green
        _play_beep_pattern([(800, 0.1), (1000, 0.2)])  # Rising beep
        _display_text(["✓ Signed In", "", message or "Welcome!"])
        LOG.info("Feedback: Signed In")
    
    elif state == FeedbackState.SIGNED_OUT:
        # Blue LED, double beep, display "Signed Out"
        _set_rgb_color(0, 0, 1)  # Blue
        _play_beep_pattern([(1000, 0.1), (800, 0.2)])  # Falling beep
        _display_text(["✓ Signed Out", "", message or "Goodbye!"])
        LOG.info("Feedback: Signed Out")
    
    elif state == FeedbackState.CARD_NOT_EXIST:
        # Red LED, error beeps, display error
        _set_rgb_color(1, 0, 0)  # Red
        _play_beep_pattern([(400, 0.15), (400, 0.15), (400, 0.15)])  # Triple low beep
        _display_text(["✗ Card Unknown", "", "Card not", "registered"])
        LOG.warning("Feedback: Card Not Exist")
    
    elif state == FeedbackState.SCAN_ERROR:
        # Red LED, error tone, display error
        _set_rgb_color(1, 0, 0)  # Red
        _play_beep_pattern([(300, 0.3)])  # Low error tone
        _display_text(["✗ Scan Error", "", message or "Try again"])
        LOG.error("Feedback: Scan Error - %s", message)
    
    elif state == FeedbackState.SYSTEM_UNAVAILABLE:
        # Red LED, warning beeps, display system error
        _set_rgb_color(1, 0, 0)  # Red
        _play_beep_pattern([(500, 0.2), (500, 0.2)])  # Double warning beep
        _display_text(["✗ System", "  Unavailable", "", "Network or", "import issue"])
        LOG.error("Feedback: System Unavailable - %s", message)
    
    elif state == FeedbackState.READY_TO_SCAN:
        # Cyan LED (waiting state), no beep, display ready message
        # Turn off LED first to clear any previous state
        if _rgb_led is not None:
            try:
                _rgb_led.off()
            except Exception as exc:
                LOG.warning("Failed to turn off LED: %s", exc)
        _set_rgb_color(0, 1, 1)  # Cyan
        _display_text(["Ready", "", "Please scan", "your card"])
        LOG.info("Feedback: Ready to Scan")
    
    elif state == FeedbackState.PROCESSING_SCAN:
        # Yellow LED (processing), brief beep, display processing
        _set_rgb_color(1, 1, 0)  # Yellow
        _play_beep_pattern([(600, 0.1)])  # Quick acknowledgment beep
        _display_text(["Processing...", "", "Please wait"])
        LOG.info("Feedback: Processing Scan")


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


def shutdown_hardware() -> None:
    """Shutdown and cleanup hardware resources."""
    global _rgb_led, _piezo, _oled
    
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
