
"""
Tanauan City College - Public Announcement System
Main Flask Application
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
import os
import sqlite3
import json
import threading
from datetime import datetime, timedelta
import csv
import io

import pytz as _pytz
from config import Config
from database import init_db, get_db, run_migrations

_PH_TZ = _pytz.timezone(Config.SCHEDULER_TIMEZONE)

def ph_now():
    """Return current datetime in Philippine Time (Asia/Manila)."""
    return datetime.now(_PH_TZ)
from arduino_comm import ArduinoController
from scheduler import AnnouncementScheduler
from sms_handler import SMSHandler
import importlib, email_handler as _eh_mod
importlib.reload(_eh_mod)
from email_handler import EmailHandler
from audio_handler import AudioHandler
from system_monitor import SystemMonitor
from security_utils import audit_log

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY


arduino = ArduinoController(Config.ARDUINO_PORT)
scheduler = AnnouncementScheduler()
sms_handler = SMSHandler()
email_handler = EmailHandler()
audio_handler = AudioHandler()
monitor = SystemMonitor()


scheduler.set_audio_handler(audio_handler)
scheduler.set_arduino(arduino)


arduino.all_off()

def _speaker_watcher():
    """
    Background thread that watches current_display.mode in the DB.
    When mode transitions FROM 'video' TO anything else (idle/text/emergency),
    it turns off all speakers automatically.
    This is the ONLY reliable way to turn speakers off when:
      - The video ends naturally (display.py writes mode='idle' to DB)
      - The user clicks Stop Announcement (trigger_idle route writes mode='idle')
    """
    import sqlite3 as _sq
    last_mode = None
    while True:
        try:
            _db = _sq.connect(Config.DATABASE, timeout=5)
            _db.row_factory = _sq.Row
            row = _db.execute(
                "SELECT mode, speakers FROM current_display WHERE id = 1"
            ).fetchone()
            _db.close()
            if row:
                mode = row['mode']
                
                if last_mode == 'video' and mode != 'video':
                    arduino.set_speakers([])
                    print(f"[watcher] mode {last_mode!r} -> {mode!r}: speakers OFF")
                
                elif last_mode != 'video' and mode == 'video':
                    try:
                        spk = json.loads(row['speakers'] or '[]')
                    except Exception:
                        spk = []
                    if spk:
                        arduino.set_speakers(spk)
                        print(f"[watcher] mode -> video: speakers ON {spk}")
                last_mode = mode
        except Exception as _e:
            print(f"[watcher] error: {_e}")
        import time as _t; _t.sleep(1)

threading.Thread(target=_speaker_watcher, daemon=True, name='SpeakerWatcher').start()


for folder in ['audio', 'video', 'images', 'emergency', 'recordings']:
    os.makedirs(os.path.join(Config.UPLOAD_FOLDER, folder), exist_ok=True)


for _mdir in [Config.AUDIO_FOLDER, Config.VIDEO_FOLDER, Config.IMAGE_FOLDER,
               Config.IDLE_FOLDER, Config.EMERGENCY_FOLDER, Config.RECORDED_FOLDER]:
    os.makedirs(_mdir, exist_ok=True)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Restrict route to admin role only.
    - API routes (Accept: application/json or /api/ prefix) get a 403 JSON response.
    - Page routes redirect staff back to the dashboard with a flash message.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            is_api = (request.path.startswith('/api/') or
                      'application/json' in request.headers.get('Accept', '') or
                      request.is_json)
            if is_api:
                return jsonify({'success': False, 'error': 'Admin access required'}), 403
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

import json as _json

@app.context_processor
def inject_helpers():
    """Make has_permission() available in every Jinja template."""
    return dict(has_permission=has_permission)

@app.before_request
def inject_role():
    """Inject g.role and g.permissions into every request context."""
    from flask import g as _g
    _g.role = session.get('role', 'staff')
    
    if 'user_id' in session and _g.role == 'staff':
        try:
            db  = get_db()
            row = db.execute('SELECT permissions FROM users WHERE id=?',
                             (session['user_id'],)).fetchone()
            raw = (row['permissions'] if row and row['permissions'] else '{}')
            _g.permissions = _json.loads(raw) if raw else {}
        except Exception:
            _g.permissions = {}
    else:
        
        _g.permissions = {}

def _format_log_target(raw):
    """Convert the raw `target` column value into a human-readable string.

    Stored formats:
      - JSON object  : {"programs":["BSIT"],"years":["1"],"sections":["A"]}
      - Plain string : "All", "3 recipients", etc.
      - None / empty : treated as "All"
    """
    if not raw:
        return 'All'
    try:
        t = json.loads(raw)
        if not isinstance(t, dict):
            return str(raw)
        parts = []
        programs = t.get('programs') or []
        years    = t.get('years')    or []
        sections = t.get('sections') or []
        total    = t.get('total')         # e.g. from sms_history tgt
        prog_str = t.get('program', '')   

        if programs:
            parts.append(', '.join(programs))
        elif prog_str:
            parts.append(prog_str)

        if years:
            yr_labels = [f'Year {y}' for y in years]
            parts.append(', '.join(yr_labels))

        if sections:
            sec_labels = [f'Sec {s}' for s in sections]
            parts.append(', '.join(sec_labels))

        if total:
            parts.append(f'{total} recipients')

        return ' | '.join(parts) if parts else 'All'
    except (ValueError, TypeError):
        
        return str(raw)


def has_permission(key):
    """Return True if the current user can perform `key`.
    Admins always return True. Staff check g.permissions[key].
    """
    from flask import g as _g
    if _g.role == 'admin':
        return True
    return bool(_g.permissions.get(key, False))

def permission_required(key):
    """Decorator: allow access only if has_permission(key) is True."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if not has_permission(key):
                is_api = (request.path.startswith('/api/') or
                          'application/json' in request.headers.get('Accept', '') or
                          request.is_json)
                if is_api:
                    return jsonify({'success': False,
                                    'error': 'You do not have permission for this action'}), 403
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']

            
            db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                         VALUES (?, ?, ?, ?)''',
                      (datetime.now().isoformat(), 'LOGIN', f'User {username} logged in', user['id']))
            db.commit()

            
            audit_log('LOGIN', f'User logged in: {username}',
                      user=username,
                      extra={'ip': request.remote_addr})

            return redirect(url_for('dashboard'))

        
        audit_log('LOGIN_FAILED', f'Failed login attempt for: {username}',
                  user=username or 'unknown', status='failure',
                  extra={'ip': request.remote_addr})
        return render_template('login.html', error='Invalid credentials')

    return render_template('login.html')


@app.route('/logout')
def logout():
    username = session.get('username', 'unknown')
    audit_log('LOGOUT', f'User logged out: {username}', user=username)
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()

    
    system_status = monitor.get_status()

    
    ar = db.execute('SELECT reset_at FROM activity_reset WHERE id=1').fetchone()
    window_start = ar['reset_at'] if ar else (
        datetime.now() - timedelta(hours=24)).isoformat()

    
    recent_logs = db.execute(
        '''SELECT * FROM logs
           WHERE timestamp >= ?
           ORDER BY timestamp DESC LIMIT 20''',
        (window_start,)
    ).fetchall()

    display_mode = db.execute(
        'SELECT value FROM settings WHERE key=?', ('display_mode',)
    ).fetchone()

    
    speaker_status = arduino.get_speaker_status()

    return render_template('dashboard.html',
                           system_status=system_status,
                           recent_logs=recent_logs,
                           window_start=window_start,
                           display_mode=display_mode['value'] if display_mode else 'idle',
                           speaker_status=speaker_status)


@app.route('/api/activity/status')
@login_required
def api_activity_status():
    """Return the current activity window start time and recent log count."""
    db = get_db()
    ar = db.execute('SELECT reset_at, reset_by FROM activity_reset WHERE id=1').fetchone()
    window_start = ar['reset_at'] if ar else (
        datetime.now() - timedelta(hours=24)).isoformat()
    count = db.execute(
        'SELECT COUNT(*) FROM logs WHERE timestamp >= ?', (window_start,)
    ).fetchone()[0]
    
    try:
        ws   = datetime.fromisoformat(window_start)
        next_reset = (ws + timedelta(hours=24)).isoformat()
    except Exception:
        next_reset = None
    return jsonify({
        'window_start': window_start,
        'reset_by':     ar['reset_by'] if ar else 'system',
        'next_reset':   next_reset,
        'count':        count,
    })


@app.route('/api/activity/reset', methods=['POST'])
@admin_required
def api_activity_reset():
    """Manually reset the 24-h activity window. Logs the action."""
    db  = get_db()
    now = datetime.now().isoformat()
    username = session.get('username', 'admin')
    db.execute(
        'UPDATE activity_reset SET reset_at=?, reset_by=? WHERE id=1',
        (now, username)
    )
    db.execute(
        '''INSERT INTO logs (timestamp, event_type, description, user_id, status)
           VALUES (?, ?, ?, ?, ?)''',
        (now, 'ACTIVITY_RESET',
         f'Recent Activity window manually reset by {username}',
         session.get('user_id'), 'success')
    )
    db.commit()
    return jsonify({'success': True, 'reset_at': now})


@app.route('/api/activity/logs')
@login_required
def api_activity_logs():
    """Return up to 20 logs since the current window start (for live refresh)."""
    db = get_db()
    ar = db.execute('SELECT reset_at FROM activity_reset WHERE id=1').fetchone()
    window_start = ar['reset_at'] if ar else (
        datetime.now() - timedelta(hours=24)).isoformat()
    rows = db.execute(
        '''SELECT l.*, u.username FROM logs l
           LEFT JOIN users u ON l.user_id = u.id
           WHERE l.timestamp >= ?
           ORDER BY l.timestamp DESC LIMIT 20''',
        (window_start,)
    ).fetchall()
    logs = [{
        'timestamp':   r['timestamp'],
        'event_type':  r['event_type'],
        'description': r['description'],
        'status':      r['status'] or 'success',
        'username':    r['username'] or 'System',
    } for r in rows]
    return jsonify({'logs': logs, 'window_start': window_start})

@app.route('/students')
@permission_required('students_view')
def students():
    db = get_db()
    
    
    program = request.args.get('program', '')
    year    = request.args.get('year', '')
    section = request.args.get('section', '')
    search  = request.args.get('search', '')

    query  = 'SELECT * FROM students WHERE 1=1'
    params = []

    if program:
        query += ' AND program = ?'
        params.append(program)
    if year:
        query += ' AND year = ?'
        params.append(year)
    if section:
        query += ' AND section = ?'
        params.append(section)
    if search:
        query += ' AND (name LIKE ? OR student_id LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%'])
    
    query += ' ORDER BY name'
    
    students = db.execute(query, params).fetchall()
    
    return render_template('students.html', students=students)

@app.route('/students/add', methods=['POST'])
@login_required
def add_student():
    data = request.form
    
    db = get_db()
    try:
        db.execute('''INSERT INTO students (student_id, name, email, phone, year, program, section)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (data['student_id'], data['name'], data.get('email', ''),
                   data.get('phone', ''), data['year'], data['program'],
                   data.get('section', '')))
        db.commit()
        
        
        db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                     VALUES (?, ?, ?, ?)''',
                  (datetime.now().isoformat(), 'STUDENT_ADD', 
                   f'Added student: {data["name"]}', session['user_id']))
        db.commit()
        
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Student ID already exists'}), 400

@app.route('/students/upload', methods=['POST'])
@login_required
def upload_students():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
    
    if not file.filename.endswith('.csv'):
        return jsonify({'success': False, 'error': 'Only CSV files allowed'}), 400
    
    try:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_reader = csv.DictReader(stream)
        
        db = get_db()
        added = 0
        errors = []
        
        for row in csv_reader:
            try:
                
                if row['program'] not in Config.PROGRAMS:
                    errors.append(f"Invalid program for {row['student_id']}: {row['program']}")
                    continue
                
                
                if row['year'] not in ['1', '2', '3', '4']:
                    errors.append(f"Invalid year for {row['student_id']}: {row['year']}")
                    continue
                
                db.execute('''INSERT INTO students (student_id, name, email, phone, year, program)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (row['student_id'], row['name'], row['email'], 
                           row['phone'], row['year'], row['program']))
                added += 1
            except sqlite3.IntegrityError:
                errors.append(f"Duplicate student_id: {row['student_id']}")
            except KeyError as e:
                errors.append(f"Missing column: {e}")
        
        db.commit()
        
        
        db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                     VALUES (?, ?, ?, ?)''',
                  (datetime.now().isoformat(), 'STUDENT_UPLOAD', 
                   f'Uploaded {added} students', session['user_id']))
        db.commit()
        
        return jsonify({'success': True, 'added': added, 'errors': errors})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/announcements')
@login_required
def announcements():
    return render_template('announcements.html')


@app.route('/api/display/state')
@login_required
def api_display_state():
    """Return the current display state from the database for polling."""
    db  = get_db()
    row = db.execute('SELECT * FROM current_display WHERE id = 1').fetchone()
    if not row:
        return jsonify({'mode': 'idle', 'content': '', 'font_size': 48,
                        'bg_color': '#000000', 'updated_at': ''})
    return jsonify({
        'mode':       row['mode'],
        'content':    row['content'] or '',
        'font_size':  row['font_size'] if 'font_size' in row.keys() else 48,
        'bg_color':   row['bg_color']  if 'bg_color'  in row.keys() else '#000000',
        'updated_at': row['updated_at'] if 'updated_at' in row.keys() else '',
    })

@app.route('/announce/text', methods=['POST'])
@login_required
def announce_text():
    data = request.json
    text = data.get('text')
    font_size = data.get('font_size', 48)
    bg_color = data.get('bg_color', '#000000')
    speakers = data.get('speakers', [])
    
    db = get_db()
    db.execute('''UPDATE current_display 
                  SET mode = ?, content = ?, font_size = ?, bg_color = ?, 
                      with_audio = 0, updated_at = ?
                  WHERE id = 1''',
              ('text', text, font_size, bg_color, ph_now().isoformat()))
    db.commit()
    
    
    if speakers:
        arduino.set_speakers(speakers)
    
    
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                 VALUES (?, ?, ?, ?)''',
              (datetime.now().isoformat(), 'TEXT_DISPLAY', 
               f'Displayed text: {text[:50]}...', session['user_id']))
    db.commit()
    
    return jsonify({'success': True})

@app.route('/announce/audio', methods=['POST'])
@login_required
def announce_audio():
    file = request.files.get('file')
    speakers = request.form.getlist('speakers[]')
    
    if file:
        filename = secure_filename(file.filename)
        os.makedirs(Config.AUDIO_FOLDER, exist_ok=True)
        filepath = os.path.join(Config.AUDIO_FOLDER, filename)
        file.save(filepath)
        print(f"[v0] announce_audio saved: {filepath}")
        
        spk_list = [int(s) for s in speakers] if speakers else []

        
        if spk_list:
            arduino.set_speakers(spk_list)
            print(f"[Audio] Speakers ON: {spk_list}")

        def _audio_done():
            arduino.set_speakers([])
            print("[Audio] Playback done — speakers OFF")

        audio_handler.play(filepath, spk_list, on_done=_audio_done if spk_list else None)
        
        
        db = get_db()
        db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                     VALUES (?, ?, ?, ?)''',
                  (datetime.now().isoformat(), 'AUDIO_PLAY', 
                   f'Played audio: {filename}', session['user_id']))
        db.commit()
        
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'error': 'No file provided'}), 400

@app.route('/announce/tts', methods=['POST'])
@login_required
def announce_tts():
    """Text-to-Speech announcement"""
    data = request.json
    text = data.get('text')
    speakers = data.get('speakers', [])
    
    if not text:
        return jsonify({'success': False, 'error': 'No text provided'}), 400
    
    try:
        
        audio_file = audio_handler.text_to_speech(text)
        
        if audio_file:
            spk_list = [int(s) for s in speakers] if speakers else []

            
            if spk_list:
                arduino.set_speakers(spk_list)
                print(f"[TTS] Speakers ON: {spk_list}")

            
            def _tts_done():
                arduino.set_speakers([])
                print("[TTS] Playback done — speakers OFF")

            audio_handler.play(audio_file, spk_list, on_done=_tts_done if spk_list else None)
            
            
            db = get_db()
            db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                         VALUES (?, ?, ?, ?)''',
                      (datetime.now().isoformat(), 'TTS_PLAY', 
                       f'TTS: {text[:50]}...', session['user_id']))
            db.commit()
            
            
            filename = os.path.basename(audio_file)
            
            return jsonify({
                'success': True, 
                'audio_file': audio_file,
                'download_url': f'/media/audio/{filename}',
                'filename': filename
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to generate speech'}), 500
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/announce/video', methods=['POST'])
@login_required
def announce_video():
    file = request.files.get('file')
    
    _with_audio_raw = request.form.get('with_audio', 'false').strip().lower()
    with_audio = _with_audio_raw in ('true', '1', 'on', 'yes')

    
    speakers = (request.form.getlist('video_speakers[]') or
                request.form.getlist('speakers[]'))

    if file:
        filename = secure_filename(file.filename)
        os.makedirs(Config.VIDEO_FOLDER, exist_ok=True)
        filepath = os.path.join(Config.VIDEO_FOLDER, filename)
        file.save(filepath)
        print(f"[v0] announce_video saved: {filepath}")

        spk_list = [int(s) for s in speakers if s]
        spk_json = json.dumps(spk_list)

        db = get_db()
        db.execute('''UPDATE current_display
                      SET mode = ?, content = ?, with_audio = ?,
                          speakers = ?,
                          font_size = NULL, bg_color = NULL, updated_at = ?
                      WHERE id = 1''',
                  ('video', filepath, 1 if with_audio else 0,
                   spk_json, ph_now().isoformat()))
        db.commit()

        
        
        if with_audio and spk_list:
            arduino.set_speakers(spk_list)
            print(f"[Video] Speakers ON: {spk_list}")
        else:
            arduino.set_speakers([])
            print(f"[Video] Speakers OFF (with_audio={with_audio} spk_list={spk_list})")

        db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                     VALUES (?, ?, ?, ?)''',
                  (ph_now().isoformat(), 'VIDEO_PLAY',
                   f'Played video: {filename} speakers={spk_list}',
                   session['user_id']))
        db.commit()

        return jsonify({'success': True})

    return jsonify({'success': False, 'error': 'No file provided'}), 400

@app.route('/idle/set-mode', methods=['POST'])
@login_required
def set_idle_mode():
    """Set idle display mode: welcome, slideshow, or video."""
    data = request.json or {}
    mode = data.get('mode', 'welcome')

    if mode not in ('welcome', 'slideshow', 'video'):
        return jsonify({'success': False, 'error': 'Invalid mode'}), 400

    db = get_db()

    
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('idle_mode', ?)", (mode,))

    db.execute(
        '''UPDATE current_display
           SET mode = 'idle', idle_mode = ?, updated_at = ?
           WHERE id = 1''',
        (mode, ph_now().isoformat())
    )
    db.commit()

    arduino.set_speakers([])

    db.execute(
        '''INSERT INTO logs (timestamp, event_type, description, user_id)
           VALUES (?, ?, ?, ?)''',
        (ph_now().isoformat(), 'IDLE_MODE_CHANGE',
         f'Idle mode set to: {mode}', session['user_id'])
    )
    db.commit()

    return jsonify({'success': True, 'mode': mode})

@app.route('/idle/upload-image', methods=['POST'])
@login_required
def idle_upload_image():
    """Upload an image to the idle slideshow folder."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'})
    f = request.files['file']
    if not f.filename:
        return jsonify({'success': False, 'error': 'No file selected'})
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in Config.ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({'success': False, 'error': f'Unsupported type: {ext}'})
    os.makedirs(Config.IDLE_FOLDER, exist_ok=True)
    safe_name = f"{ph_now().strftime('%Y%m%d_%H%M%S')}_{f.filename}"
    dest = os.path.join(Config.IDLE_FOLDER, safe_name)
    f.save(dest)
    db = get_db()
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?,?,?,?)''',
               (ph_now().isoformat(), 'IDLE_IMAGE_UPLOAD', safe_name, session['user_id']))
    db.commit()
    return jsonify({'success': True, 'filename': safe_name})


@app.route('/idle/upload-video', methods=['POST'])
@login_required
def idle_upload_video():
    """Upload a video to the idle loop folder."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'})
    f = request.files['file']
    if not f.filename:
        return jsonify({'success': False, 'error': 'No file selected'})
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in Config.ALLOWED_VIDEO_EXTENSIONS:
        return jsonify({'success': False, 'error': f'Unsupported type: {ext}'})
    os.makedirs(Config.IDLE_FOLDER, exist_ok=True)
    safe_name = f"{ph_now().strftime('%Y%m%d_%H%M%S')}_{f.filename}"
    dest = os.path.join(Config.IDLE_FOLDER, safe_name)
    f.save(dest)
    db = get_db()
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?,?,?,?)''',
               (ph_now().isoformat(), 'IDLE_VIDEO_UPLOAD', safe_name, session['user_id']))
    db.commit()
    return jsonify({'success': True, 'filename': safe_name})


@app.route('/idle/files')
@login_required
def idle_files():
    """List all idle images and videos."""
    folder = Config.IDLE_FOLDER
    images, videos = [], []
    if os.path.exists(folder):
        for name in sorted(os.listdir(folder)):
            ext = name.rsplit('.', 1)[-1].lower()
            url = f'/media/idle/{name}'
            entry = {'name': name, 'url': url,
                     'size': f'{os.path.getsize(os.path.join(folder, name)) // 1024} KB'}
            if ext in Config.ALLOWED_IMAGE_EXTENSIONS:
                images.append(entry)
            elif ext in Config.ALLOWED_VIDEO_EXTENSIONS:
                videos.append(entry)
    return jsonify({'images': images, 'videos': videos})


@app.route('/idle/delete', methods=['POST'])
@login_required
def idle_delete_file():
    """Delete an idle image or video."""
    data = request.json or {}
    name = data.get('filename', '')
    if not name or '/' in name or '..' in name:
        return jsonify({'success': False, 'error': 'Invalid filename'})
    path = os.path.join(Config.IDLE_FOLDER, name)
    if not os.path.exists(path):
        return jsonify({'success': False, 'error': 'File not found'})
    os.remove(path)
    return jsonify({'success': True})


@app.route('/media/idle/<filename>')
@login_required
def serve_idle_file(filename):
    """Serve idle images/videos to the browser."""
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), Config.IDLE_FOLDER)
    return send_from_directory(folder, filename)


@app.route('/idle/toggle-speakers', methods=['POST'])
@login_required
def toggle_idle_speakers():
    """Toggle speakers on/off during idle mode"""
    data = request.json
    enabled = data.get('enabled', False)
    
    db = get_db()
    db.execute('''UPDATE settings SET value = ? WHERE key = 'idle_speakers_enabled' ''', 
               (str(enabled),))
    db.commit()
    
    
    Config.IDLE_SPEAKERS_ENABLED = enabled
    
    
    if not enabled:
        arduino.set_speakers([])
    
    
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                 VALUES (?, ?, ?, ?)''',
              (datetime.now().isoformat(), 'IDLE_SPEAKERS_TOGGLE', 
               f'Idle speakers {"enabled" if enabled else "disabled"}', session['user_id']))
    db.commit()
    
    return jsonify({'success': True, 'enabled': enabled})

@app.route('/trigger/idle', methods=['POST'])
@login_required
def trigger_idle():
    """Manually trigger idle display now."""
    db = get_db()
    
    db.execute(
        "UPDATE current_display SET mode = 'idle', updated_at = ? WHERE id = 1",
        (ph_now().isoformat(),)
    )
    db.commit()
    
    
    arduino.set_speakers([])
    
    
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                 VALUES (?, ?, ?, ?)''',
              (datetime.now().isoformat(), 'IDLE_TRIGGER', 
               'Idle display triggered manually', session['user_id']))
    db.commit()
    
    return jsonify({'success': True})

@app.route('/media/images/<filename>')
def serve_image(filename):
    """Serve image files (no login required so logo shows on login page)"""
    image_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), Config.IMAGE_FOLDER)
    return send_from_directory(image_folder, filename)

@app.route('/media/audio/<filename>')
@login_required
def serve_audio(filename):
    """Serve audio files for download"""
    audio_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), Config.AUDIO_FOLDER)
    return send_from_directory(audio_folder, filename, as_attachment=True)

@app.route('/media/recorded/<filename>')
@login_required
def serve_recorded(filename):
    """Serve recorded audio files (stream so browser <audio> works too)"""
    rec_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), Config.RECORDED_FOLDER)
    return send_from_directory(rec_folder, filename)

@app.route('/announce/stop', methods=['POST'])
@login_required
def stop_announcement():
    """Stop current announcement and return to idle"""
    try:
        audio_handler.stop()
        
        db = get_db()
        db.execute('''INSERT OR REPLACE INTO current_display (id, mode, content, updated_at)
                      VALUES (1, 'idle', '', ?)''',
                  (ph_now().isoformat(),))
        db.commit()
        
        
        
        arduino.set_speakers([])
        
        
        db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                     VALUES (?, ?, ?, ?)''',
                  (datetime.now().isoformat(), 'ANNOUNCEMENT_STOPPED', 
                   'Announcement stopped manually', session['user_id']))
        db.commit()
        
        return jsonify({'success': True, 'message': 'Announcement stopped, returning to idle'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/trigger/announcement', methods=['POST'])
@login_required
def trigger_announcement():
    """Manually trigger a scheduled announcement now"""
    data = request.json
    announcement_id = data.get('announcement_id')
    
    db = get_db()
    announcement = db.execute('''SELECT * FROM scheduled_announcements WHERE id = ?''', 
                             (announcement_id,)).fetchone()
    
    if not announcement:
        return jsonify({'success': False, 'error': 'Announcement not found'}), 404
    
    
    ann_type = announcement['type']
    content = announcement['content']
    speakers = json.loads(announcement['speakers']) if announcement['speakers'] else []
    
    if ann_type == 'text':
        db.execute('''UPDATE current_display SET mode = 'text', content = ?, 
                     font_size = 48, bg_color = '#000000' WHERE id = 1''')
    elif ann_type == 'audio':
        audio_handler.play(content, speakers)
        arduino.set_speakers(speakers)
    elif ann_type == 'video':
        db.execute('''UPDATE current_display SET mode = 'video', content = ?, 
                     with_audio = 1 WHERE id = 1''')
    
    db.commit()
    
    
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                 VALUES (?, ?, ?, ?)''',
              (datetime.now().isoformat(), 'ANNOUNCEMENT_TRIGGER', 
               f'Announcement triggered manually: {announcement["name"]}', session['user_id']))
    db.commit()
    
    return jsonify({'success': True})

@app.route('/emergency', methods=['GET', 'POST'])
@login_required
def emergency():
    if request.method == 'POST':
        data           = request.get_json(force=True) or {}
        emergency_type = data.get('type', '').strip()
        text           = data.get('text', '').strip()
        audio_file     = data.get('audio_file', '').strip()  

        if not emergency_type or not text:
            return jsonify({'success': False, 'error': 'Type and text are required'}), 400

        db = get_db()
        
        db.execute('''UPDATE current_display
                      SET mode=?, content=?, emergency_text=?, emergency_audio=?,
                          font_size=NULL, bg_color=NULL, updated_at=?
                      WHERE id=1''',
                  ('emergency', 'EMERGENCY ALERT', text, audio_file, ph_now().isoformat()))
        db.commit()

        
        arduino.set_speakers([1, 2, 3, 4])
        print(f"[Emergency] Speakers ON — type={emergency_type}")

        if audio_file and os.path.exists(audio_file):
            def _emg_done():
                pass
            audio_handler.play(audio_file, [1, 2, 3, 4], on_done=None)

        db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                     VALUES (?, ?, ?, ?)''',
                  (ph_now().isoformat(), 'EMERGENCY',
                   f'Emergency triggered: {emergency_type} | {text}',
                   session['user_id']))
        db.commit()

        try:
            audit_log('EMERGENCY', f'Emergency alert dispatched: {emergency_type}',
                      user=session.get('username', 'unknown'),
                      target=text, extra={'ip': request.remote_addr, 'audio': audio_file})
        except Exception:
            pass

        return jsonify({'success': True})

    
    db = get_db()
    emergency_files = db.execute(
        'SELECT * FROM emergency_files ORDER BY uploaded_at DESC'
    ).fetchall()
    return render_template('emergency.html', emergency_files=emergency_files)


@app.route('/emergency/upload', methods=['POST'])
@login_required
def emergency_upload():
    """Upload an emergency audio file and register it in the DB."""
    name  = request.form.get('name', '').strip()
    etype = request.form.get('type', 'general').strip()
    f     = request.files.get('file')

    if not name or not f or not f.filename:
        return jsonify({'success': False, 'error': 'Name and file are required'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in Config.ALLOWED_AUDIO_EXTENSIONS:
        return jsonify({'success': False,
                        'error': f'Invalid file type. Allowed: {", ".join(Config.ALLOWED_AUDIO_EXTENSIONS)}'}), 400

    
    os.makedirs(Config.EMERGENCY_FOLDER, exist_ok=True)
    safe_name = secure_filename(f.filename)
    save_path = os.path.join(Config.EMERGENCY_FOLDER, safe_name)
    
    base, dot_ext = os.path.splitext(safe_name)
    counter = 1
    while os.path.exists(save_path):
        save_path = os.path.join(Config.EMERGENCY_FOLDER, f"{base}_{counter}{dot_ext}")
        counter += 1
    f.save(save_path)

    db = get_db()
    db.execute(
        'INSERT INTO emergency_files (name, file_path, type, uploaded_at) VALUES (?, ?, ?, ?)',
        (name, save_path, etype, ph_now().isoformat())
    )
    db.commit()
    print(f"[Emergency] Audio uploaded: {save_path}")
    return jsonify({'success': True, 'path': save_path})


@app.route('/emergency/stop', methods=['POST'])
@login_required
def emergency_stop():
    """Stop emergency mode — silence all speakers, return to idle."""
    db = get_db()
    db.execute(
        "UPDATE current_display SET mode='idle', content='', updated_at=? WHERE id=1",
        (ph_now().isoformat(),)
    )
    db.commit()

    try:
        audio_handler.stop()
    except Exception:
        pass
    arduino.set_speakers([])
    print("[Emergency] Stopped — speakers OFF, mode=idle")

    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                 VALUES (?, ?, ?, ?)''',
              (ph_now().isoformat(), 'EMERGENCY_STOP', 'Emergency alert stopped',
               session['user_id']))
    db.commit()
    return jsonify({'success': True})


@app.route('/emergency/test', methods=['POST'])
@login_required
def emergency_test():
    """Play an emergency audio file as a test (speakers on, auto-off when done)."""
    data      = request.get_json(force=True) or {}
    file_path = data.get('file', '').strip()

    if not file_path or not os.path.exists(file_path):
        return jsonify({'success': False, 'error': f'File not found: {file_path}'}), 404

    arduino.set_speakers([1, 2, 3, 4])

    def _test_done():
        arduino.set_speakers([])
        print("[Emergency] Test playback done — speakers OFF")

    audio_handler.play(file_path, [1, 2, 3, 4], on_done=_test_done)
    return jsonify({'success': True})


@app.route('/emergency/test/stop', methods=['POST'])
@login_required
def emergency_test_stop():
    """Stop a currently-playing test audio and silence speakers."""
    try:
        audio_handler.stop()
    except Exception:
        pass
    arduino.set_speakers([])
    print("[Emergency] Test stopped — speakers OFF")
    return jsonify({'success': True})


@app.route('/emergency/delete/<int:file_id>', methods=['DELETE', 'POST'])
@login_required
def emergency_delete(file_id):
    """Delete an emergency audio file from disk and DB."""
    db  = get_db()
    row = db.execute('SELECT * FROM emergency_files WHERE id=?', (file_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'File not found'}), 404

    
    try:
        if os.path.exists(row['file_path']):
            os.remove(row['file_path'])
    except Exception as e:
        print(f"[Emergency] Could not delete file {row['file_path']}: {e}")

    db.execute('DELETE FROM emergency_files WHERE id=?', (file_id,))
    db.commit()
    return jsonify({'success': True})

@app.route('/schedule')
@login_required
def schedule():
    return render_template('schedule.html')

# ------------------------------------------------------------------
# Schedule API
# ------------------------------------------------------------------

@app.route('/api/schedules')
@login_required
def api_get_schedules():
    db = get_db()
    rows = db.execute('SELECT * FROM schedules ORDER BY schedule_time').fetchall()
    schedules = []
    for r in rows:
        try:
            dur = int(r['duration_seconds']) if r['duration_seconds'] else 0
        except Exception:
            dur = 0
        schedules.append({
            'id':               r['id'],
            'type':             r['type'],
            'content':          r['content'],
            'scheduled_time':   r['schedule_time'],
            'speakers':         r['speakers'],
            'status':           'active' if r['is_active'] else 'inactive',
            'repeat_days':      r['repeat_days'],
            'duration_seconds': dur,
        })
    return jsonify({'schedules': schedules})


@app.route('/api/schedule/upload', methods=['POST'])
@login_required
def api_schedule_upload():
    """Upload an audio or video file directly from the schedule modal.
    Returns {success, path, name} so the frontend can use the path as content."""
    media_type = request.form.get('media_type', '').strip()   # 'audio' or 'video'
    f          = request.files.get('file')

    if not media_type or not f or not f.filename:
        return jsonify({'success': False, 'error': 'media_type and file are required'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower()
    if media_type == 'audio':
        allowed = {'mp3', 'wav', 'ogg', 'm4a'}
        folder  = Config.AUDIO_FOLDER
    else:
        allowed = {'mp4', 'avi', 'mkv', 'mov', 'webm'}
        folder  = Config.VIDEO_FOLDER

    if ext not in allowed:
        return jsonify({'success': False,
                        'error': f'Invalid file type .{ext}. Allowed: {", ".join(sorted(allowed))}'}), 400

    os.makedirs(folder, exist_ok=True)
    safe_name = secure_filename(f.filename)
    save_path = os.path.join(folder, safe_name)
    
    base, dot_ext = os.path.splitext(safe_name)
    counter = 1
    while os.path.exists(save_path):
        save_path = os.path.join(folder, f"{base}_{counter}{dot_ext}")
        counter  += 1

    f.save(save_path)
    print(f"[v0] schedule upload saved: {save_path}")
    return jsonify({'success': True, 'path': save_path, 'name': os.path.basename(save_path)})


@app.route('/api/schedule', methods=['POST'])
@login_required
def api_add_schedule():
    data             = request.json or {}
    ann_type         = data.get('type', '').strip()
    content          = data.get('content', '').strip()
    time_str         = data.get('time', '')
    repeat           = data.get('repeat', 'once')
    speakers         = data.get('speakers', [])
    duration_seconds = int(data.get('duration_seconds', 0) or 0)

    if not ann_type or not content or not time_str:
        return jsonify({'success': False, 'error': 'type, content and time are required'}), 400

    schedule_time = time_str  

    repeat_map = {
        'daily':    [],
        'weekdays': ['mon', 'tue', 'wed', 'thu', 'fri'],
        'weekly':   [],
        'once':     [],
    }
    repeat_days = repeat_map.get(repeat, [])

    db = get_db()
    cur = db.execute(
        '''INSERT INTO schedules
               (schedule_time, type, content, speakers, repeat_days, duration_seconds, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (schedule_time, ann_type, content,
         json.dumps(speakers), json.dumps(repeat_days),
         duration_seconds, datetime.now().isoformat())
    )
    db.commit()
    new_id = cur.lastrowid

    scheduler.add_job(
        schedule_time, ann_type, content, speakers,
        repeat_days if repeat_days else None,
        new_id, duration_seconds
    )

    return jsonify({'success': True, 'id': new_id})


@app.route('/api/schedule/<int:schedule_id>', methods=['DELETE'])
@login_required
def api_delete_schedule(schedule_id):
    db = get_db()
    row = db.execute('SELECT id FROM schedules WHERE id = ?', (schedule_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'Schedule not found'}), 404

    db.execute('DELETE FROM schedules WHERE id = ?', (schedule_id,))
    db.commit()
    scheduler.remove_job(schedule_id)
    return jsonify({'success': True})


@app.route('/api/schedule/<int:schedule_id>/trigger', methods=['POST'])
@login_required
def api_trigger_schedule(schedule_id):
    """Immediately execute a scheduled announcement then revert after duration."""
    db  = get_db()
    row = db.execute('SELECT * FROM schedules WHERE id = ?', (schedule_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'Schedule not found'}), 404

    ann_type         = row['type']
    content          = row['content']
    speakers         = json.loads(row['speakers']) if row['speakers'] else []
    try:
        duration_seconds = int(row['duration_seconds']) if row['duration_seconds'] else 0
    except Exception:
        duration_seconds = 0

    try:
        now = ph_now().isoformat()

        if ann_type == 'text':
            db.execute(
                '''UPDATE current_display
                   SET mode=?, content=?, font_size=?, bg_color=?, updated_at=?
                   WHERE id=1''',
                ('text', content, 48, '#000000', now)
            )
            db.commit()

        elif ann_type == 'audio':
            audio_path = content if os.path.exists(content) \
                         else os.path.join(Config.AUDIO_FOLDER, os.path.basename(content))
            print(f"[v0] trigger audio: content={content!r}  resolved={audio_path!r}  exists={os.path.exists(audio_path)}")
            if os.path.exists(audio_path):
                arduino.set_speakers(speakers)
                audio_handler.play(audio_path, speakers,
                                   on_done=lambda: arduino.set_speakers([]))
            else:
                return jsonify({'success': False,
                                'error': f'Audio file not found: {audio_path}'}), 404

        elif ann_type == 'video':
            
            video_path = content if os.path.exists(content) \
                         else os.path.join(Config.VIDEO_FOLDER, os.path.basename(content))
            print(f"[v0] trigger video: content={content!r}  resolved={video_path!r}  exists={os.path.exists(video_path)}")
            if not os.path.exists(video_path):
                return jsonify({'success': False,
                                'error': f'Video file not found: {video_path}'}), 404
            arduino.set_speakers(speakers)
            db.execute(
                '''UPDATE current_display
                   SET mode=?, content=?, with_audio=?, speakers=?, updated_at=?
                   WHERE id=1''',
                ('video', video_path, 1,
                 json.dumps(speakers) if speakers else '[]', now)
            )
            db.commit()

        elif ann_type == 'tts':
            print(f"[v0] trigger tts: content={content!r}")
            tts_path = audio_handler.text_to_speech(content)
            print(f"[v0] tts_path={tts_path!r}  exists={os.path.exists(tts_path) if tts_path else False}")
            if tts_path and os.path.exists(tts_path):
                arduino.set_speakers(speakers)
                audio_handler.play(tts_path, speakers,
                                   on_done=lambda: arduino.set_speakers([]))
            else:
                return jsonify({'success': False,
                                'error': 'TTS generation failed — check gTTS/pyttsx3 install'}), 500

        db.execute(
            '''INSERT INTO logs (timestamp, event_type, description, user_id)
               VALUES (?, ?, ?, ?)''',
            (now, 'SCHEDULE_TRIGGER',
             f'Manually triggered schedule #{schedule_id}: {ann_type}'
             + (f' (duration: {duration_seconds}s)' if duration_seconds else ''),
             session['user_id'])
        )
        db.commit()

        
        if duration_seconds > 0:
            import threading, time as _time
            def _revert():
                _time.sleep(duration_seconds)
                try:
                    import sqlite3 as _sq, pytz as _pz
                    _ph = _pz.timezone('Asia/Manila')
                    conn = _sq.connect(Config.DATABASE)
                    conn.execute(
                        '''UPDATE current_display SET mode=?, content=?, updated_at=? WHERE id=1''',
                        ('idle', '', datetime.now(_ph).isoformat())
                    )
                    conn.commit()
                    conn.close()
                    print(f"[trigger] Reverted to idle after {duration_seconds}s")
                except Exception as ex:
                    print(f"Revert-to-idle error: {ex}")
            threading.Thread(target=_revert, daemon=True).start()

        return jsonify({'success': True, 'duration_seconds': duration_seconds})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/media/audio')
@login_required
def api_media_audio():
    """Return list of {name, path} dicts from Config.AUDIO_FOLDER (media/audio)."""
    folder = Config.AUDIO_FOLDER          # 'media/audio'
    files  = []
    if os.path.isdir(folder):
        for fname in sorted(os.listdir(folder)):
            if fname.lower().endswith(('.mp3', '.wav', '.ogg', '.m4a')):
                files.append({
                    'name': fname,
                    'path': os.path.join(folder, fname)   # 'media/audio/file.mp3'
                })
    print(f"[v0] api_media_audio: found {len(files)} files in {folder}")
    return jsonify({'files': files})


@app.route('/api/media/video')
@login_required
def api_media_video():
    """Return list of {name, path} dicts from Config.VIDEO_FOLDER (media/video)."""
    folder = Config.VIDEO_FOLDER          
    files  = []
    if os.path.isdir(folder):
        for fname in sorted(os.listdir(folder)):
            if fname.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm')):
                files.append({
                    'name': fname,
                    'path': os.path.join(folder, fname)   
                })
    print(f"[v0] api_media_video: found {len(files)} files in {folder}")
    return jsonify({'files': files})

@app.route('/sms')
@login_required
def sms():
    programs = getattr(Config, 'PROGRAMS', ['BSCPE','BSE','BPA','BTVTED','BSMA'])
    return render_template('sms.html', programs=programs)

@app.route('/api/sms/send', methods=['POST'])
@login_required
def send_sms():
    data     = request.json or {}
    message  = data.get('message', '').strip()
    programs = data.get('programs', [])
    years    = data.get('years', [])
    sections = data.get('sections', [])

    if not message:
        return jsonify({'success': False, 'error': 'Message is required.'}), 400

    
    db    = get_db()
    query = 'SELECT phone FROM students WHERE phone IS NOT NULL AND phone != "" '
    params = []

    if programs:
        placeholders = ','.join('?' * len(programs))
        query  += f' AND program IN ({placeholders})'
        params.extend(programs)

    if years:
        placeholders = ','.join('?' * len(years))
        query  += f' AND year IN ({placeholders})'
        params.extend(years)

    if sections:
        placeholders = ','.join('?' * len(sections))
        query  += f' AND section IN ({placeholders})'
        params.extend(sections)

    students     = db.execute(query, params).fetchall()
    phone_numbers = [s['phone'] for s in students if s['phone']]

    if not phone_numbers:
        return jsonify({'success': False,
                        'error': 'No students with phone numbers found for the selected filters.'}), 400

    result = sms_handler.send_bulk(message, phone_numbers)

    
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id, target)
                  VALUES (?, ?, ?, ?, ?)''',
               (ph_now().isoformat(), 'SMS',
                f"SMS sent: {result.get('success', 0)} ok, {result.get('failed', 0)} failed",
                session['user_id'],
                json.dumps({'programs': programs, 'years': years, 'sections': sections})))
    db.commit()

    
    audit_log('SMS_SEND', f"Bulk SMS: {result.get('success', 0)} sent, {result.get('failed', 0)} failed",
              user=session.get('username', 'unknown'),
              target=f'{len(phone_numbers)} recipients',
              status='success' if result.get('failed', 0) == 0 else 'warning',
              extra={
                  'success': result.get('success', 0),
                  'failed':  result.get('failed', 0),
                  'errors':  result.get('errors', []),
              })

    return jsonify({
        'success': result.get('failed', 0) == 0 or result.get('success', 0) > 0,
        'sent':    result.get('success', 0),
        'failed':  result.get('failed', 0),
        'errors':  result.get('errors', []),
    })

@app.route('/email')
@login_required
def email():
    return render_template('email.html', programs=Config.PROGRAMS)

@app.route('/api/email/send', methods=['POST'])
@login_required
def send_email():
    data     = request.json or {}
    subject  = data.get('subject', '').strip()
    body     = data.get('message', data.get('body', '')).strip()
    programs = data.get('programs', [])
    years    = data.get('years', [])

    if not subject or not body:
        return jsonify({'success': False, 'error': 'Subject and message are required.'}), 400
    
    
    db = get_db()
    query = 'SELECT email FROM students WHERE 1=1'
    params = []
    
    if programs:
        placeholders = ','.join('?' * len(programs))
        query += f' AND program IN ({placeholders})'
        params.extend(programs)
    
    if years:
        placeholders = ','.join('?' * len(years))
        query += f' AND year IN ({placeholders})'
        params.extend(years)
    
    students = db.execute(query, params).fetchall()
    emails   = [s['email'] for s in students if s['email']]

    if not emails:
        return jsonify({'success': False, 'error': 'No student email addresses found for the selected filters.'}), 400

    result = email_handler.send_bulk(subject, body, emails)

    status = 'success' if result['failed'] == 0 else ('warning' if result['success'] > 0 else 'failed')

    
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id, target)
                 VALUES (?, ?, ?, ?, ?)''',
              (datetime.now().isoformat(), 'EMAIL',
               f"Email: {result['success']} sent, {result['failed']} failed",
               session['user_id'], json.dumps({'programs': programs, 'years': years,
                                               'errors': result.get('errors', [])})))
    db.commit()

    return jsonify({
        'success':    result['failed'] == 0,
        'sent':       result['success'],
        'failed':     result['failed'],
        'total':      len(emails),
        'errors':     result.get('errors', []),
        'status':     status,
    })

@app.route('/api/email/history')
@login_required
def email_history():
    db   = get_db()
    rows = db.execute(
        '''SELECT timestamp, description, target
           FROM logs
           WHERE event_type = 'EMAIL'
           ORDER BY timestamp DESC
           LIMIT 20'''
    ).fetchall()

    history = []
    for r in rows:
        try:
            t = json.loads(r['target'] or '{}')
        except Exception:
            t = {}
        desc = r['description'] or ''
        history.append({
            'timestamp':  r['timestamp'],
            'subject':    desc,
            'recipients': t.get('total', ''),
        })

    return jsonify({'history': history})


@app.route('/api/students/count', methods=['POST'])
@login_required
def students_count():
    data     = request.json or {}
    programs = data.get('programs', [])
    years    = data.get('years', [])
    sections = data.get('sections', [])

    db    = get_db()
    query  = 'SELECT COUNT(*) FROM students WHERE phone IS NOT NULL AND phone != ""'
    params = []

    if programs:
        placeholders = ','.join('?' * len(programs))
        query += f' AND program IN ({placeholders})'
        params.extend(programs)

    if years:
        placeholders = ','.join('?' * len(years))
        query += f' AND year IN ({placeholders})'
        params.extend(years)

    if sections:
        placeholders = ','.join('?' * len(sections))
        query += f' AND section IN ({placeholders})'
        params.extend(sections)

    count = db.execute(query, params).fetchone()[0]
    return jsonify({'count': count})


@app.route('/api/sms/history', endpoint='api_sms_history')
@login_required
def sms_history():
    """Return recent SMS log entries."""
    db   = get_db()
    rows = db.execute(
        '''SELECT timestamp, description, target FROM logs
           WHERE event_type = 'SMS'
           ORDER BY timestamp DESC LIMIT 20'''
    ).fetchall()

    history = []
    for r in rows:
        try:
            tgt  = json.loads(r['target']) if r['target'] else {}
            prog = ', '.join(tgt.get('programs', [])) or 'All Programs'
            yr   = ', '.join(str(y) for y in tgt.get('years', [])) or 'All Years'
            sec  = ', '.join(tgt.get('sections', [])) or ''
            recipients = prog + ' | ' + yr + (f' | Sec {sec}' if sec else '')
        except Exception:
            recipients = r['target'] or 'All'

        history.append({
            'timestamp':  r['timestamp'],
            'message':    r['description'],
            'recipients': recipients,
        })

    return jsonify({'history': history})


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        data = request.json
        
        db = get_db()
        for key, value in data.items():
            db.execute('''INSERT OR REPLACE INTO settings (key, value)
                         VALUES (?, ?)''', (key, value))
        db.commit()
        
        
        if 'sms_provider' in data:
            sms_handler.update_config(data)
        if 'email_provider' in data:
            email_handler.update_config(data)
        
        return jsonify({'success': True})
    
    db = get_db()
    settings = db.execute('SELECT * FROM settings').fetchall()
    settings_dict = {s['key']: s['value'] for s in settings}
    
    return render_template('settings.html', settings=settings_dict)

@app.route('/logs')
@permission_required('logs_view')
def logs():
    db = get_db()
    
    
    event_type = request.args.get('type', '')
    start_date = request.args.get('start', '')
    end_date = request.args.get('end', '')
    
    query = 'SELECT * FROM logs WHERE 1=1'
    params = []
    
    if event_type:
        query += ' AND event_type = ?'
        params.append(event_type)
    if start_date:
        query += ' AND timestamp >= ?'
        params.append(start_date)
    if end_date:
        query += ' AND timestamp <= ?'
        params.append(end_date)
    
    query += ' ORDER BY timestamp DESC LIMIT 1000'
    
    logs = db.execute(query, params).fetchall()
    
    return render_template('logs.html', logs=logs)

@app.route('/logs/export')
@login_required
def export_logs():
    db = get_db()
    logs = db.execute('SELECT * FROM logs ORDER BY timestamp DESC').fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'Event Type', 'Description', 'User ID', 'Target', 'Status'])
    
    for log in logs:
        writer.writerow([log['timestamp'], log['event_type'], log['description'], 
                        log['user_id'], log['target'], log['status']])
    
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )

@app.route('/api/speakers/test', methods=['POST'])
@login_required
def test_speakers():
    data = request.json
    speaker = data.get('speaker')
    state = data.get('state')
    
    arduino.test_speaker(speaker, state)
    
    return jsonify({'success': True})

@app.route('/api/system/status')
@login_required
def system_status():
    status = monitor.get_status()
    speaker_status = arduino.get_speaker_status()
    
    return jsonify({
        'system': status,
        'speakers': speaker_status
    })

# ---------------------------------------------------------------------------
# API: Logs (paginated, filtered)
# ---------------------------------------------------------------------------
@app.route('/api/logs')
@login_required
def api_logs():
    db = get_db()

    page       = int(request.args.get('page', 1))
    per_page   = 20
    offset     = (page - 1) * per_page
    event_type = request.args.get('type', '')
    status     = request.args.get('status', '')
    date_from  = request.args.get('date_from', '')
    date_to    = request.args.get('date_to', '')

    where  = ['1=1']
    params = []

    if event_type:
        where.append('l.event_type = ?')
        params.append(event_type)
    if status:
        where.append('l.status = ?')
        params.append(status)
    if date_from:
        where.append("l.timestamp >= ?")
        params.append(date_from)
    if date_to:
        where.append("l.timestamp <= ?")
        params.append(date_to + ' 23:59:59')

    where_sql = ' AND '.join(where)

    total = db.execute(
        f'SELECT COUNT(*) FROM logs l WHERE {where_sql}', params
    ).fetchone()[0]

    rows = db.execute(
        f'''SELECT l.*, u.username
            FROM logs l
            LEFT JOIN users u ON l.user_id = u.id
            WHERE {where_sql}
            ORDER BY l.timestamp DESC
            LIMIT ? OFFSET ?''',
        params + [per_page, offset]
    ).fetchall()

    logs_list = []
    for r in rows:
        logs_list.append({
            'id':          r['id'],
            'timestamp':   r['timestamp'],
            'type':        r['event_type'],
            'description': r['description'],
            'target':      _format_log_target(r['target']),
            'status':      r['status'] or 'success',
            'username':    r['username'] or 'System',
            'channel':     None,
        })

    return jsonify({
        'logs':        logs_list,
        'total':       total,
        'total_pages': max(1, (total + per_page - 1) // per_page),
        'page':        page,
    })


@app.route('/api/logs/<int:log_id>')
@login_required
def api_log_detail(log_id):
    db  = get_db()
    row = db.execute(
        '''SELECT l.*, u.username FROM logs l
           LEFT JOIN users u ON l.user_id = u.id
           WHERE l.id = ?''', (log_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'log': {
        'id':          row['id'],
        'timestamp':   row['timestamp'],
        'type':        row['event_type'],
        'description': row['description'],
        'target':      _format_log_target(row['target']),
        'status':      row['status'] or 'success',
        'username':    row['username'] or 'System',
        'details':     row['description'],
        'channel':     None,
    }})


@app.route('/api/logs/delete', methods=['POST'])
@permission_required('logs_delete')
def api_logs_delete():
    """Bulk delete logs by list of ids, or all logs if ids is empty and confirm_all is true."""
    data       = request.json or {}
    ids        = data.get('ids', [])
    confirm_all = data.get('confirm_all', False)

    db = get_db()
    if confirm_all and not ids:
        db.execute('DELETE FROM logs')
        db.commit()
        return jsonify({'success': True, 'deleted': 'all'})

    if not ids:
        return jsonify({'success': False, 'error': 'No ids provided'}), 400

    placeholders = ','.join('?' * len(ids))
    db.execute(f'DELETE FROM logs WHERE id IN ({placeholders})', ids)
    db.commit()
    return jsonify({'success': True, 'deleted': len(ids)})


@app.route('/students/delete/<int:student_id>', methods=['DELETE', 'POST'])
@permission_required('students_delete')
def delete_student(student_id):
    db = get_db()
    row = db.execute('SELECT name FROM students WHERE id=?', (student_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'Student not found'}), 404
    db.execute('DELETE FROM students WHERE id=?', (student_id,))
    db.commit()
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?,?,?,?)''',
               (ph_now().isoformat(), 'STUDENT_DELETE',
                f'Deleted student: {row["name"]}', session['user_id']))
    db.commit()
    return jsonify({'success': True})


@app.route('/students/edit/<int:student_id>', methods=['GET'])
@permission_required('students_edit')
def get_student(student_id):
    db  = get_db()
    row = db.execute('SELECT * FROM students WHERE id=?', (student_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'Student not found'}), 404
    return jsonify({'success': True, 'student': dict(row)})


@app.route('/students/edit/<int:student_id>', methods=['POST'])
@permission_required('students_edit')
def edit_student(student_id):
    data = request.json or {}
    db   = get_db()
    row  = db.execute('SELECT id FROM students WHERE id=?', (student_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'Student not found'}), 404
    db.execute('''UPDATE students SET student_id=?, name=?, email=?, phone=?,
                  year=?, program=?, section=? WHERE id=?''',
               (data.get('student_id',''), data.get('name',''),
                data.get('email',''),      data.get('phone',''),
                data.get('year','1'),      data.get('program',''),
                data.get('section',''),    student_id))
    db.commit()
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?,?,?,?)''',
               (ph_now().isoformat(), 'STUDENT_EDIT',
                f'Updated student ID {student_id}: {data.get("name","")}',
                session['user_id']))
    db.commit()
    return jsonify({'success': True})


@app.route('/api/students/delete', methods=['POST'])
@permission_required('students_delete')
def api_students_bulk_delete():
    """Bulk delete students by list of ids."""
    data = request.json or {}
    ids  = data.get('ids', [])

    if not ids:
        return jsonify({'success': False, 'error': 'No ids provided'}), 400

    db = get_db()
    placeholders = ','.join('?' * len(ids))
    db.execute(f'DELETE FROM students WHERE id IN ({placeholders})', ids)
    db.commit()

    db.execute(
        '''INSERT INTO logs (timestamp, event_type, description, user_id)
           VALUES (?, ?, ?, ?)''',
        (datetime.now().isoformat(), 'STUDENT_DELETE',
         f'Bulk deleted {len(ids)} student(s)', session['user_id'])
    )
    db.commit()
    return jsonify({'success': True, 'deleted': len(ids)})


@app.route('/api/logs/export')
@login_required
def api_logs_export():
    db   = get_db()
    rows = db.execute(
        '''SELECT l.*, u.username FROM logs l
           LEFT JOIN users u ON l.user_id = u.id
           ORDER BY l.timestamp DESC'''
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'Event Type', 'Description', 'User', 'Target', 'Status'])
    for r in rows:
        writer.writerow([r['timestamp'], r['event_type'], r['description'],
                         r['username'] or 'System', r['target'], r['status']])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )


# ---------------------------------------------------------------------------
# API: Settings
# ---------------------------------------------------------------------------
@app.route('/api/settings')
@login_required
def api_settings_get():
    db   = get_db()
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    return jsonify({'settings': {r['key']: r['value'] for r in rows}})


@app.route('/api/settings/device', methods=['POST'])
@login_required
def api_settings_device():
    data = request.json or {}
    db   = get_db()
    mapping = {
        'serialPort':        'arduino_port',
        'audioOutput':       'audio_output',
        'displayResolution': 'display_resolution',
    }
    for form_key, db_key in mapping.items():
        if form_key in data:
            db.execute(
                'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                (db_key, data[form_key])
            )
    db.commit()

    
    if 'serialPort' in data:
        try:
            arduino.reconnect(data['serialPort'])
        except Exception:
            pass

    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?, ?, ?, ?)''',
               (datetime.now().isoformat(), 'SETTINGS_DEVICE',
                'Device settings updated', session['user_id']))
    db.commit()
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# API: Arduino / Relay
# ---------------------------------------------------------------------------
@app.route('/api/arduino/status')
@login_required
def api_arduino_status():
    connected = False
    try:
        connected = arduino.is_connected()
    except Exception:
        pass
    return jsonify({'connected': connected})


@app.route('/api/relay/test', methods=['POST'])
@login_required
def api_relay_test():
    """
    Pulse a relay channel for 1 second.
    Channel mapping:
        1-4  -> Speaker 1-4  (relay IN2-IN5)
        5    -> Green LED     (relay IN5)
        6    -> Red LED       (relay IN6)
    """
    data    = request.json or {}
    channel = data.get('channel')
    try:
        if channel == 5:
            
            arduino.green_on()
            threading.Timer(1.0, arduino.green_off).start()
        elif channel == 6:
            
            arduino.red_on()
            threading.Timer(1.0, arduino.red_off).start()
        elif isinstance(channel, int) and 1 <= channel <= 4:
            arduino.test_speaker(channel, True)
            threading.Timer(1.0, lambda c=channel: arduino.test_speaker(c, False)).start()
        else:
            return jsonify({'success': False, 'error': f'Invalid channel: {channel}'}), 400
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/relay/test-all', methods=['POST'])
@login_required
def api_relay_test_all():
    try:
        arduino.set_speakers([1, 2, 3, 4])
        threading.Timer(2.0, lambda: arduino.set_speakers([])).start()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API: Users (settings page)
# ---------------------------------------------------------------------------
@app.route('/api/users', methods=['GET'])
@login_required
def api_users():
    db   = get_db()
    rows = db.execute('SELECT id, username, role, created_at FROM users').fetchall()
    return jsonify({'users': [dict(r) for r in rows]})


@app.route('/api/users/change-password', methods=['POST'])
@login_required
def api_change_own_password():
    """Any logged-in user changes their own password."""
    data         = request.json or {}
    current_pass = data.get('current_password', '')
    new_pass     = data.get('new_password', '')
    confirm_pass = data.get('confirm_password', '')

    if not current_pass or not new_pass or not confirm_pass:
        return jsonify({'success': False, 'error': 'All fields are required.'}), 400
    if new_pass != confirm_pass:
        return jsonify({'success': False, 'error': 'New passwords do not match.'}), 400
    if len(new_pass) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters.'}), 400

    db   = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if not user or not check_password_hash(user['password'], current_pass):
        return jsonify({'success': False, 'error': 'Current password is incorrect.'}), 403

    db.execute('UPDATE users SET password = ? WHERE id = ?',
               (generate_password_hash(new_pass), session['user_id']))
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?, ?, ?, ?)''',
               (datetime.now().isoformat(), 'PASSWORD_CHANGE',
                f'User {session["username"]} changed their password', session['user_id']))
    db.commit()
    return jsonify({'success': True})


@app.route('/api/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
def api_admin_reset_password(user_id):
    """Admin resets any user's password without knowing the current one."""
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Admin access required.'}), 403

    data     = request.json or {}
    new_pass = data.get('new_password', '')
    confirm  = data.get('confirm_password', '')

    if not new_pass or not confirm:
        return jsonify({'success': False, 'error': 'All fields are required.'}), 400
    if new_pass != confirm:
        return jsonify({'success': False, 'error': 'Passwords do not match.'}), 400
    if len(new_pass) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters.'}), 400

    db   = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        return jsonify({'success': False, 'error': 'User not found.'}), 404

    db.execute('UPDATE users SET password = ? WHERE id = ?',
               (generate_password_hash(new_pass), user_id))
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?, ?, ?, ?)''',
               (datetime.now().isoformat(), 'PASSWORD_RESET',
                f'Admin {session["username"]} reset password for user: {user["username"]}',
                session['user_id']))
    db.commit()
    return jsonify({'success': True, 'username': user['username']})


@app.route('/api/users/<int:user_id>/permissions', methods=['GET', 'POST'])
@admin_required
def api_user_permissions(user_id):
    db  = get_db()
    row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    if row['role'] == 'admin':
        return jsonify({'success': False, 'error': 'Cannot set permissions for admin users'}), 400

    if request.method == 'GET':
        raw = row['permissions'] if row['permissions'] else '{}'
        try:
            perms = _json.loads(raw)
        except Exception:
            perms = {}
        return jsonify({'success': True, 'permissions': perms, 'username': row['username']})

    
    data  = request.get_json(force=True) or {}
    perms = data.get('permissions', {})
    
    allowed_keys = {
        'students_view', 'students_add', 'students_edit', 'students_delete',
        'logs_view', 'logs_delete',
        'schedule', 'emergency', 'announcements'
    }
    clean = {k: bool(v) for k, v in perms.items() if k in allowed_keys}
    db.execute('UPDATE users SET permissions=? WHERE id=?',
               (_json.dumps(clean), user_id))
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?, ?, ?, ?)''',
               (datetime.now().isoformat(), 'PERMISSION_UPDATE',
                f'Admin updated permissions for user: {row["username"]}',
                session['user_id']))
    db.commit()
    return jsonify({'success': True})


@app.route('/api/users/add', methods=['POST'])
@login_required
def api_add_user():
    data     = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role     = data.get('role', 'staff')

    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400

    db = get_db()
    try:
        db.execute(
            '''INSERT INTO users (username, password, role, created_at)
               VALUES (?, ?, ?, ?)''',
            (username, generate_password_hash(password), role, datetime.now().isoformat())
        )
        db.commit()
        db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                      VALUES (?, ?, ?, ?)''',
                   (datetime.now().isoformat(), 'USER_ADD',
                    f'User added: {username}', session['user_id']))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ---------------------------------------------------------------------------
# Recording / Live-audio endpoints
# ---------------------------------------------------------------------------


_recording_state = {
    'active':    False,
    'started_at': None,
    'duration':   0,
    'last_file':  None,
    'process':    None,   
}

@app.route('/announce/record/start', methods=['POST'])
@login_required
def record_start():
    global _recording_state
    if _recording_state['active']:
        return jsonify({'success': False, 'error': 'Recording already in progress'})

    data     = request.json or {}
    duration = int(data.get('duration', 30))

    import time as _time
    _recording_state.update({
        'active':     True,
        'started_at': _time.time(),
        'duration':   duration,
        'last_file':  None,
        'process':    None,
    })

    def _do_record():
        print(f"[record] Starting {duration}s recording ...")
        result_path = audio_handler.record_audio(duration)
        _recording_state['active']    = False
        _recording_state['last_file'] = result_path
        _recording_state['process']   = None
        print(f"[record] Finished. File: {result_path}")

    t = threading.Thread(target=_do_record, daemon=True)
    t.start()
    _recording_state['process'] = t

    db = get_db()
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?, ?, ?, ?)''',
               (ph_now().isoformat(), 'RECORDING_START',
                f'Recording started ({duration}s)', session['user_id']))
    db.commit()
    return jsonify({'success': True, 'duration': duration})


@app.route('/announce/record/stop', methods=['POST'])
@login_required
def record_stop():
    global _recording_state
    audio_handler.stop()
    _recording_state['active'] = False
    return jsonify({'success': True})


@app.route('/announce/record/status')
@login_required
def record_status():
    import time as _time
    state  = _recording_state
    result = {'active': state['active'], 'last_file': None}
    if state['active'] and state['started_at']:
        elapsed  = _time.time() - state['started_at']
        result['elapsed']   = int(elapsed)
        result['duration']  = state['duration']
        result['remaining'] = max(0, state['duration'] - int(elapsed))
    if state['last_file']:
        fname = os.path.basename(state['last_file'])
        result['last_file'] = fname
        result['last_file_url'] = f'/media/recorded/{fname}'
    return jsonify(result)


@app.route('/announce/record/list')
@login_required
def record_list():
    folder = Config.RECORDED_FOLDER
    if not os.path.exists(folder):
        return jsonify({'files': []})
    files = []
    for name in sorted(os.listdir(folder), reverse=True):
        if name.lower().endswith(('.mp3', '.wav')):
            fp   = os.path.join(folder, name)
            size = os.path.getsize(fp)
            files.append({
                'name': name,
                'url':  f'/media/recorded/{name}',
                'size': f'{size // 1024} KB',
            })
    return jsonify({'files': files})


@app.route('/announce/audio/url', methods=['POST'])
@login_required
def play_audio_url():
    """Play a server-side audio file (by URL path) through the speakers."""
    data = request.json or {}
    url  = data.get('url', '')
    
    rel  = url.lstrip('/')
    path = os.path.join(os.path.dirname(__file__), rel)
    if not os.path.exists(path):
        return jsonify({'success': False, 'error': f'File not found: {rel}'})
    speakers = [int(s) for s in data.get('speakers', [1, 2, 3, 4])]
    if speakers:
        arduino.set_speakers(speakers)
        print(f"[AudioURL] Speakers ON: {speakers}")

    def _url_done():
        arduino.set_speakers([])
        print("[AudioURL] Playback done — speakers OFF")

    audio_handler.play(path, speakers, on_done=_url_done if speakers else None)
    return jsonify({'success': True})


@app.route('/announce/live/start', methods=['POST'])
@login_required
def live_audio_start():
    data     = request.json or {}
    speakers = data.get('speakers', [1, 2, 3, 4])

    def _do_live():
        arduino.set_speakers([int(s) for s in speakers])
        audio_handler.play_live(speakers)

    import threading
    threading.Thread(target=_do_live, daemon=True).start()

    db = get_db()
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?, ?, ?, ?)''',
               (datetime.now().isoformat(), 'LIVE_AUDIO_START',
                'Live audio started', session['user_id']))
    db.commit()
    return jsonify({'success': True})


@app.route('/announce/live/stop', methods=['POST'])
@login_required
def live_audio_stop():
    audio_handler.stop()
    arduino.set_speakers([])
    db = get_db()
    db.execute('''INSERT INTO logs (timestamp, event_type, description, user_id)
                  VALUES (?, ?, ?, ?)''',
               (datetime.now().isoformat(), 'LIVE_AUDIO_STOP',
                'Live audio stopped', session['user_id']))
    db.commit()
    return jsonify({'success': True})


if __name__ == '__main__':
    init_db()

    run_migrations()

    scheduler.start()

    
    app.run(host='0.0.0.0', port=5000, debug=False)
