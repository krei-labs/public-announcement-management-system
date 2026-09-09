
"""
Script to create required folder structure for TCC Announcement System
Run this once after installation to set up all necessary directories
"""

import os
from config import Config

def create_folders():
    """Create all required folders for the system"""
    
    folders = [
        
        Config.UPLOAD_FOLDER,
        
        
        Config.AUDIO_FOLDER,
        Config.VIDEO_FOLDER,
        Config.IMAGE_FOLDER,
        Config.IDLE_FOLDER,
        Config.EMERGENCY_FOLDER,
        Config.RECORDED_FOLDER,
        
        
        'static',
        'static/css',
        'static/js',
        
        
        'logs',
    ]
    
    print("Creating folder structure for TCC Announcement System...\n")
    
    for folder in folders:
        try:
            os.makedirs(folder, exist_ok=True)
            print(f"✓ Created: {folder}")
        except Exception as e:
            print(f"✗ Error creating {folder}: {e}")
    
    
    readme_content = {
        Config.AUDIO_FOLDER: """# Audio Files Folder

Place your MP3 and audio announcement files here.

Supported formats:
- MP3
- WAV

These files can be:
- Played manually from the web interface
- Scheduled for automatic playback
- Used for announcements with speaker selection
""",
        
        Config.VIDEO_FOLDER: """# Video Files Folder

Place your MP4 and video files here.

Supported formats:
- MP4
- AVI
- MKV

These files can be:
- Played manually from the web interface
- Scheduled for automatic playback
- Played with or without audio
""",
        
        Config.IMAGE_FOLDER: """# Images Folder

Place your school logo and other images here.

**IMPORTANT:** Place your school logo as 'logo.png' in this folder.

Supported formats:
- PNG (recommended for logo)
- JPG/JPEG
- GIF

The logo will be displayed on:
- Idle screens
- Web interface header
- Emergency displays
""",
        
        Config.IDLE_FOLDER: """# Idle Screen Images

Place images for the idle screen slideshow here.

When no announcements are active, the system will cycle through these images.

Supported formats:
- PNG
- JPG/JPEG
- GIF

Tips:
- Use 1920x1080 resolution for best display
- Images will be automatically resized to fit screen
- Multiple images will rotate every few seconds
""",
        
        Config.EMERGENCY_FOLDER: """# Emergency Audio Files

Place emergency alert audio files here.

Examples:
- fire_alarm.mp3
- earthquake_drill.mp3
- lockdown_alert.mp3
- evacuation_alarm.mp3

Format: MP3

These files are used for emergency announcements that override all other displays.
""",
        
        Config.RECORDED_FOLDER: """# Recorded Audio Files

This folder stores recordings made via USB microphone.

Files are automatically saved here when you:
- Record live announcements
- Save recordings for later playback

Format: MP3
"""
    }
    
    print("\nCreating README files in media folders...\n")
    
    for folder, content in readme_content.items():
        readme_path = os.path.join(folder, 'README.md')
        try:
            with open(readme_path, 'w') as f:
                f.write(content)
            print(f"✓ Created README: {readme_path}")
        except Exception as e:
            print(f"✗ Error creating README in {folder}: {e}")
    
    
    print("\n" + "="*60)
    print("IMPORTANT: PLACE YOUR SCHOOL LOGO")
    print("="*60)
    print(f"\nPlace your school logo at: {Config.LOGO_PATH}")
    print("Recommended: PNG format, transparent background")
    print("Dimensions: 200x200 pixels or larger")
    print("\nIf no logo is provided, the system will use text-only branding.")
    print("="*60)
    
    print("\n✓ Folder structure created successfully!")
    print("\nFolder structure:")
    print(f"""
    media/
    ├── audio/          ← Place MP3 announcement files here
    ├── video/          ← Place MP4 video files here
    ├── images/         ← Place logo.png here
    ├── idle/           ← Place idle screen images here
    ├── emergency/      ← Place emergency audio files here
    └── recorded/       ← USB mic recordings saved here (auto)
    """)

if __name__ == '__main__':
    create_folders()
