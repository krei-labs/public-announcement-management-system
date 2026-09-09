"""Application configuration."""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Config:
    """Central application configuration."""

    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32)
    DATABASE = os.environ.get("DATABASE_PATH", "announcement_system.db")

    UPLOAD_FOLDER = "uploads"
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024
    ALLOWED_AUDIO_EXTENSIONS = {"mp3", "wav"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "avi", "mkv"}
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

    AUDIO_FOLDER = "media/audio"
    VIDEO_FOLDER = "media/video"
    IMAGE_FOLDER = "media/images"
    IDLE_FOLDER = "media/idle"
    EMERGENCY_FOLDER = "media/emergency"
    RECORDED_FOLDER = "media/recorded"
    LOGO_PATH = "media/images/logo.png"

    ARDUINO_PORT = os.environ.get("ARDUINO_PORT", "/dev/ttyACM0")
    ARDUINO_BAUD = int(os.environ.get("ARDUINO_BAUD", "9600"))

    DISPLAY_WIDTH = 1920
    DISPLAY_HEIGHT = 1080
    FULLSCREEN = True
    IDLE_MODE_DEFAULT = "welcome"
    IDLE_WELCOME_TEXT = "Welcome to Tanauan City College"
    IDLE_SLIDESHOW_INTERVAL = 5
    IDLE_VIDEO_LOOP = True
    IDLE_SPEAKERS_ENABLED = False

    AUDIO_OUTPUT = "default"
    DEFAULT_VOLUME = 80
    PROGRAMS = ["BSCPE", "BSE", "BPA", "BTVTED", "BSMA"]
    YEARS = ["1", "2", "3", "4"]

    COLORS = {
        "primary": "#800000", "secondary": "#FFD700", "text": "#FFFFFF",
        "success": "#28a745", "warning": "#ffc107", "danger": "#dc3545",
    }

    SMS_ENABLED = False
    EMAIL_ENABLED = False

    SCHEDULER_TIMEZONE = "Asia/Manila"
    LOG_LEVEL = "INFO"
    LOG_FILE = "system.log"
