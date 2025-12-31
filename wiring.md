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