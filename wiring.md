# Wiring Guide (Raspberry Pi)

**Pi references use BCM numbering. All grounds common.**

## MFRC522 RFID Reader (SPI)
- 3.3V → Pi 3.3V (Pin 1)
- GND  → Pi GND (Pin 6)
- SDA / NSS → GPIO8 / CE0 (Pin 24)
- SCK → GPIO11 / SCLK (Pin 23)
- MOSI → GPIO10 / MOSI (Pin 19)
- MISO → GPIO9 / MISO (Pin 21)
- RST → GPIO25 (Pin 22)
- IRQ → (optional) GPIO24 (Pin 18) or leave unconnected

**Enable SPI** on the Pi via `raspi-config` (Interface Options → SPI).

**Important:** Keep all devices at 3.3V logic. Do NOT feed 5V logic to the MFRC522.

## RGB LED (Common Cathode)
- Red → GPIO17 (Pin 11) via 220Ω resistor
- Green → GPIO27 (Pin 13) via 220Ω resistor
- Blue → GPIO22 (Pin 15) via 220Ω resistor
- Common Cathode → Pi GND

**Note:** For common anode RGB LEDs, you'll need to invert the logic in the code.

## Passive Piezo Buzzer
- Positive (+) → GPIO23 (Pin 16)
- Negative (-) → Pi GND

## 128x128 OLED Display (I2C, SSD1306)
- VCC → Pi 3.3V (Pin 1) or 5V (Pin 2, check your display specs)
- GND → Pi GND (Pin 6)
- SDA → GPIO2 / SDA (Pin 3)
- SCL → GPIO3 / SCL (Pin 5)

**Enable I2C** on the Pi via `raspi-config` (Interface Options → I2C).

**Note:** Default I2C address for SSD1306 is usually 0x3C or 0x3D. You can verify with `i2cdetect -y 1`.

## GPIO Pin Configuration

The default pins can be changed by modifying the constants in `feedback_hardware.py`:
- `RGB_LED_RED_PIN = 17`
- `RGB_LED_GREEN_PIN = 27`
- `RGB_LED_BLUE_PIN = 22`
- `PIEZO_PIN = 23`