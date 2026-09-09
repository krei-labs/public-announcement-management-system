"""
Database initialization and management
"""

import os
import sqlite3
from flask import g
from werkzeug.security import generate_password_hash
from config import Config

def get_db():
    """Get database connection"""
    if 'db' not in g:
        g.db = sqlite3.connect(Config.DATABASE, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
        g.db.execute('PRAGMA busy_timeout=5000')
    return g.db

def close_db(e=None):
    """Close database connection"""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """Initialize database with schema"""
    db = sqlite3.connect(Config.DATABASE)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=5000')
    
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff',
            created_at TEXT NOT NULL
        )
    ''')
    
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            year TEXT NOT NULL,
            program TEXT NOT NULL,
            section TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            file_path TEXT,
            speakers TEXT,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            FOREIGN KEY (created_by) REFERENCES users (id)
        )
    ''')
    
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_time TEXT NOT NULL,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            speakers TEXT,
            repeat_days TEXT,
            is_active INTEGER DEFAULT 1,
            duration_seconds INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS emergency_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            type TEXT NOT NULL,
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            description TEXT,
            user_id INTEGER,
            target TEXT,
            status TEXT DEFAULT 'success',
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS current_display (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mode TEXT NOT NULL DEFAULT 'idle',
            content TEXT,
            font_size INTEGER,
            bg_color TEXT,
            with_audio INTEGER DEFAULT 0,
            emergency_text TEXT,
            emergency_audio TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS activity_reset (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            reset_at    TEXT NOT NULL,
            reset_by    TEXT DEFAULT 'system'
        )
    ''')
    
    db.execute('''
        INSERT OR IGNORE INTO activity_reset (id, reset_at, reset_by)
        VALUES (1, datetime('now', '-24 hours'), 'system')
    ''')

    db.execute('''
        INSERT OR IGNORE INTO current_display (id, mode) VALUES (1, 'idle')
    ''')
    
    
    default_settings = {
        'display_mode': 'idle',
        'audio_output': 'default',
        'volume': '80',
    }
    
    for key, value in default_settings.items():
        db.execute('''
            INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)
        ''', (key, value))
    
    admin_username = os.environ.get("ADMIN_USERNAME", "admin").strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if admin_username and admin_password:
        try:
            db.execute('''
                INSERT INTO users (username, password, role, created_at)
                VALUES (?, ?, ?, datetime('now'))
            ''', (admin_username, generate_password_hash(admin_password), 'admin'))
        except sqlite3.IntegrityError:
            pass
    
    db.commit()
    db.close()
    
    print("Database initialized successfully!")

def run_migrations():
    """
    Safe ALTER TABLE migrations for columns added after initial release.
    Each migration is idempotent — skipped if the column already exists.
    """
    db = sqlite3.connect(Config.DATABASE)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=5000')

    migrations = [
        
        ('schedules',       'duration_seconds', 'INTEGER DEFAULT 0'),
        ('students',        'section',          "TEXT DEFAULT ''"),
        ('current_display', 'idle_mode',        "TEXT DEFAULT 'welcome'"),
        ('current_display', 'speakers',         "TEXT DEFAULT ''"),
        
        ('users',           'permissions',      "TEXT DEFAULT '{}'"),
    ]

    for table, column, definition in migrations:
        cols = [row[1] for row in db.execute(f'PRAGMA table_info({table})').fetchall()]
        if column not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            print(f'Migration applied: {table}.{column} ({definition})')
        else:
            print(f'Migration skipped: {table}.{column} already exists')

    db.commit()
    db.close()


if __name__ == '__main__':
    init_db()
