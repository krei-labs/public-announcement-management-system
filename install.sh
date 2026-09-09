#!/bin/bash

# TCC Display System - Installation Script for Raspberry Pi
# This script sets up the complete system including dependencies and database

set -e  # Exit on error

echo "========================================"
echo "TCC Display System - Installation"
echo "========================================"
echo ""

# Check if running on Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "Warning: This doesn't appear to be a Raspberry Pi"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Update system
echo "Step 1: Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

# Install Python 3 and pip
echo "Step 2: Installing Python and dependencies..."
sudo apt-get install -y python3 python3-pip python3-venv
sudo apt-get install -y python3-tk  # Tkinter for display
sudo apt-get install -y sqlite3     # SQLite database

# Install audio dependencies
echo "Step 3: Installing audio dependencies..."
sudo apt-get install -y pulseaudio pulseaudio-utils
sudo apt-get install -y mpg321 mpv vlc  # Audio/video players
sudo apt-get install -y ffmpeg          # Audio processing
sudo apt-get install -y alsa-utils      # Audio utilities

# Install system monitoring tools
echo "Step 4: Installing system monitoring tools..."
sudo apt-get install -y lm-sensors

# Create virtual environment
echo "Step 5: Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python packages
echo "Step 6: Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# Create necessary directories
echo "Step 7: Creating directory structure..."
mkdir -p static/audio
mkdir -p static/video
mkdir -p static/emergency
mkdir -p static/images
mkdir -p static/uploads
mkdir -p logs
mkdir -p data

# Set permissions
echo "Step 8: Setting permissions..."
chmod -R 755 static
chmod -R 755 logs
chmod -R 755 data

# Initialize database
echo "Step 9: Initializing database..."
python3 -c "from database import init_db; init_db()"

# Arduino setup instructions
echo ""
echo "========================================"
echo "Arduino Setup Required:"
echo "========================================"
echo "1. Connect Arduino Uno to Raspberry Pi via USB"
echo "2. Upload the sketch from arduino/relay_control.ino using Arduino IDE"
echo "3. Connect the 6-channel relay module to Arduino:"
echo "   - Channel 1 (Pin 2) -> Speaker 1 Relay"
echo "   - Channel 2 (Pin 3) -> Speaker 2 Relay"
echo "   - Channel 3 (Pin 4) -> Speaker 3 Relay"
echo "   - Channel 4 (Pin 5) -> Speaker 4 Relay"
echo "   - Channel 5 (Pin 6) -> Red LED Relay"
echo "   - Channel 6 (Pin 7) -> Green LED Relay"
echo "4. Connect relay outputs to speakers (AC switching)"
echo ""

# Display configuration
echo "========================================"
echo "Display Configuration:"
echo "========================================"
echo "For automatic fullscreen on boot:"
echo "1. Edit /etc/xdg/lxsession/LXDE-pi/autostart"
echo "2. Add this line:"
echo "   @/home/pi/tcc-display/start_display.sh"
echo ""

# Create folder structure
echo "Creating media folders..."
python3 create_folders.py
echo ""

# Create startup script
echo "Creating startup scripts..."
cat > start_display.sh << 'EOF'
#!/bin/bash
cd /home/pi/tcc-display
source venv/bin/activate
python3 display.py &
EOF

cat > start_server.sh << 'EOF'
#!/bin/bash
cd /home/pi/tcc-display
source venv/bin/activate
python3 app.py
EOF

chmod +x start_display.sh
chmod +x start_server.sh

# Create systemd service for web server
echo "Creating systemd service..."
sudo tee /etc/systemd/system/tcc-display.service > /dev/null << EOF
[Unit]
Description=TCC Display System Web Server
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python3 $(pwd)/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable service
sudo systemctl daemon-reload
sudo systemctl enable tcc-display.service

echo ""
echo "========================================"
echo "Installation Complete!"
echo "========================================"
echo ""
echo "Default login credentials:"
echo "  Username: admin"
echo "  Password: configured through .env"
echo ""
echo "To start the system:"
echo "  1. Web Server: sudo systemctl start tcc-display"
echo "     Access at: http://$(hostname -I | awk '{print $1}'):5000"
echo ""
echo "  2. Display (manual): ./start_display.sh"
echo ""
echo "IMPORTANT: Change the default password after first login!"
echo ""
echo "For auto-start on boot, add to LXDE autostart:"
echo "  @$(pwd)/start_display.sh"
echo ""
echo "Check Arduino connection with:"
echo "  ls /dev/ttyUSB* or ls /dev/ttyACM*"
echo ""
