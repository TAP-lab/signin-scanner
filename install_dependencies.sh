#!/usr/bin/env bash
# Install system dependencies for signin-scanner on Raspberry Pi
# This includes fonts required for OLED display feedback

set -euo pipefail

echo "Installing system dependencies for signin-scanner..."
echo ""

# Update package list
echo "Updating package list..."
sudo apt-get update

# Install Python development tools
echo "Installing Python development tools..."
sudo apt-get install -y python3-venv python3-pip python3-dev

# Install I2C and SPI tools (for hardware communication)
echo "Installing I2C and SPI tools..."
sudo apt-get install -y i2c-tools python3-smbus

# Install font packages (required for OLED display text rendering)
echo "Installing font packages..."
sudo apt-get install -y fonts-dejavu fonts-liberation fonts-freefont-ttf

# Verify fonts are installed
echo ""
echo "Verifying font installation..."
FONTS_FOUND=0
FONT_PATHS=(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
)

for font_path in "${FONT_PATHS[@]}"; do
    if [[ -f "$font_path" ]]; then
        echo "  ✓ Found: $font_path"
        ((FONTS_FOUND++))
    else
        echo "  ✗ Missing: $font_path"
    fi
done

echo ""
if [[ $FONTS_FOUND -gt 0 ]]; then
    echo "✓ System dependencies installed successfully!"
    echo "  Found $FONTS_FOUND font(s)"
else
    echo "⚠ Warning: No fonts found in expected locations"
    echo "  The system will fall back to default font, which may be limited"
fi

echo ""
echo "Next steps:"
echo "1. Create .env file with your Salesforce credentials"
echo "2. Run: python3 -m venv .venv"
echo "3. Run: . .venv/bin/activate"
echo "4. Run: pip install -r requirements.txt"
echo "5. Test with: python signin.py --terminal"
echo ""
