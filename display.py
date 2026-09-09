
"""
TCC Announcement Display System
- Tkinter canvas for idle / text / emergency screens
- ffplay subprocess for video playback (hardware-accelerated, no frame lag)
- Database monitor drives all mode transitions
"""

import tkinter as tk
from PIL import Image, ImageTk
import sqlite3
import os
import signal
import threading
import time
import subprocess
from datetime import datetime
import pygame

from config import Config


# ---------------------------------------------------------------------------#

# ---------------------------------------------------------------------------#

def _kill_proc(proc):
    """Send SIGKILL to proc immediately. No-op if already dead."""
    if proc is None:
        return
    try:
        if proc.poll() is None:               
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------#
# ---------------------------------------------------------------------------#

class DisplaySystem:

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("TCC Announcement Display")

        
        monitors = self._detect_monitors()
        if len(monitors) > 1:
            mon = monitors[1]
        else:
            mon = monitors[0]

        self._mon_x      = mon['x']
        self._mon_y      = mon['y']
        self._mon_width  = mon['width']
        self._mon_height = mon['height']

        self.display_width  = self._mon_width
        self.display_height = self._mon_height

        if Config.FULLSCREEN:
            self.root.geometry(
                f"{self._mon_width}x{self._mon_height}"
                f"+{self._mon_x}+{self._mon_y}"
            )
            self.root.attributes('-fullscreen', True)
            self.root.bind('<Escape>', lambda e: self.root.attributes('-fullscreen', False))
        else:
            self.root.geometry(
                f"{self._mon_width}x{self._mon_height}"
                f"+{self._mon_x}+{self._mon_y}"
            )

        self.root.configure(bg='black')

        self.canvas = tk.Canvas(
            self.root,
            width=self._mon_width,
            height=self._mon_height,
            bg='black',
            highlightthickness=0
        )
        self.canvas.pack(fill='both', expand=True)

        
        self.current_mode      = 'idle'
        self.idle_display_mode = Config.IDLE_MODE_DEFAULT

        
        
        
        self._idle_gen = 0

        
        self._ffplay_proc: subprocess.Popen | None = None
        self._ffplay_lock = threading.Lock()

        
        self._idle_pil_images: list[Image.Image] = []
        self._slideshow_photo: ImageTk.PhotoImage | None = None
        self.idle_videos: list[str] = []
        self.idle_index = 0

        
        self._logo_image: ImageTk.PhotoImage | None = None
        self._logo_size = 560   

        
        self.quotes = [
            "Education is the most powerful weapon which you can use to change the world. - Nelson Mandela",
            "The future belongs to those who believe in the beauty of their dreams. - Eleanor Roosevelt",
            "Success is not final, failure is not fatal: It is the courage to continue that counts. - Winston Churchill",
            "Believe you can and you're halfway there. - Theodore Roosevelt",
            "The only way to do great work is to love what you do. - Steve Jobs",
            "Your education is a dress rehearsal for a life that is yours to lead. - Nora Ephron",
            "The beautiful thing about learning is that no one can take it away from you. - B.B. King",
            "Don't let what you cannot do interfere with what you can do. - John Wooden",
            "Strive not to be a success, but rather to be of value. - Albert Einstein",
            "The expert in anything was once a beginner. - Helen Hayes",
        ]
        self.quote_index    = 0
        self._quote_counter = 0

        
        
        os.environ.setdefault('SDL_AUDIODRIVER', 'alsa')
        
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
        except Exception as _pm_err:
            print(f"[display] pygame.mixer.init warning: {_pm_err}")
        self.load_idle_images()
        self.load_idle_videos()

        self.running = True
        self.monitor_thread = threading.Thread(
            target=self.monitor_database, daemon=True)
        self.monitor_thread.start()

        self.show_idle()

    # -----------------------------------------------------------------------#
    
    # -----------------------------------------------------------------------#

    def _detect_monitors(self):
        monitors = []
        try:
            result = subprocess.run(
                ['xrandr'], capture_output=True, text=True)
            for line in result.stdout.split('\n'):
                if ' connected' in line:
                    parts = line.split()
                    geo_idx = (parts.index('primary') + 1
                               if 'primary' in parts else 2)
                    if geo_idx < len(parts):
                        geo = parts[geo_idx]
                        if 'x' in geo and '+' in geo:
                            size, pos = geo.split('+', 1)
                            w, h = map(int, size.split('x'))
                            px   = int(pos.split('+')[0])
                            py   = int(pos.split('+')[1]) if '+' in pos else 0
                            monitors.append(
                                {'width': w, 'height': h, 'x': px, 'y': py})
        except Exception as e:
            print(f"[display] xrandr error: {e}")

        if not monitors:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            if sw > 2000:
                hw = sw // 2
                monitors = [
                    {'width': hw, 'height': sh, 'x': 0,  'y': 0},
                    {'width': hw, 'height': sh, 'x': hw, 'y': 0},
                ]
            else:
                monitors = [{'width': sw, 'height': sh, 'x': 0, 'y': 0}]
        return monitors

    # -----------------------------------------------------------------------#
    
    # -----------------------------------------------------------------------#

    def load_idle_images(self):
        self._idle_pil_images = []
        self._slideshow_photo = None
        folder = Config.IDLE_FOLDER
        if os.path.exists(folder):
            names = sorted(
                f for f in os.listdir(folder)
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))
            )
            for name in names:
                try:
                    img = Image.open(os.path.join(folder, name)).convert('RGB')
                    img = img.resize(
                        (self.display_width, self.display_height),
                        Image.Resampling.LANCZOS
                    )
                    self._idle_pil_images.append(img)
                    print(f"[idle] Loaded image: {name}")
                except Exception as e:
                    print(f"[idle] Error loading {name}: {e}")
        print(f"[idle] {len(self._idle_pil_images)} slideshow image(s) ready")

    def load_idle_videos(self):
        self.idle_videos = []
        folder = Config.IDLE_FOLDER
        if os.path.exists(folder):
            names = sorted(
                f for f in os.listdir(folder)
                if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov'))
            )
            for name in names:
                self.idle_videos.append(os.path.join(folder, name))
                print(f"[idle] Loaded video: {name}")
        print(f"[idle] {len(self.idle_videos)} idle video(s) ready")

    def _default_idle_pil(self):
        img = Image.new('RGB',
                        (self.display_width, self.display_height), '#800000')
        if os.path.exists(Config.LOGO_PATH):
            try:
                logo = Image.open(Config.LOGO_PATH).convert('RGBA')
                logo.thumbnail((300, 300), Image.Resampling.LANCZOS)
                lx = (self.display_width  - logo.width)  // 2
                ly = (self.display_height - logo.height) // 2 - 150
                img.paste(logo, (lx, ly), logo)
            except Exception:
                pass
        return img

    # -----------------------------------------------------------------------#
    
    # -----------------------------------------------------------------------#

    def _stop_ffplay(self):
        """
        Kill any running ffplay subprocess immediately using SIGKILL.
        Safe to call from any thread.
        """
        with self._ffplay_lock:
            proc = self._ffplay_proc
            self._ffplay_proc = None

        if proc is not None:
            print(f"[video] killing ffplay pid={proc.pid}")
            _kill_proc(proc)

    def _launch_ffplay(self, video_path, with_audio=True,
                       on_finish=None, loop=False):
        """
        Launch ffplay covering the target monitor window exactly.

        ffplay arguments used:
          -x / -y        → exact pixel size
          -left / -top   → position on screen (X11 window placement)
          -noborder      → no window decorations
          -fs            → full-screen within the window (maps to monitor)
          -autoexit      → process exits when video ends (for non-loop)
          -loop 0        → infinite loop (for idle video)
          -an            → disable audio when with_audio=False
          -vf scale=w:h  → ensure correct resolution
          -loglevel quiet

        on_finish: optional callable invoked on Tkinter thread when ffplay exits.
        """
        if not os.path.exists(video_path):
            print(f"[video] file not found: {video_path}")
            if on_finish:
                self.root.after(0, on_finish)
            return

        
        self._stop_ffplay()

        w = self._mon_width
        h = self._mon_height
        x = self._mon_x
        y = self._mon_y

        cmd = [
            'ffplay',
            '-loglevel', 'quiet',
            '-x', str(w),
            '-y', str(h),
            '-left', str(x),
            '-top',  str(y),
            '-noborder',
        ]

        if loop:
            cmd += ['-loop', '0']
        else:
            cmd += ['-autoexit']

        if not with_audio:
            cmd += ['-an']

        cmd += [
            '-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,'
                   f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2',
            video_path
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            with self._ffplay_lock:
                self._ffplay_proc = proc
            print(f"[video] ffplay pid={proc.pid}  loop={loop}  audio={with_audio}")
        except Exception as e:
            print(f"[video] ffplay launch error: {e}")
            if on_finish:
                self.root.after(0, on_finish)
            return

        
        if on_finish is not None or not loop:
            def _watcher():
                proc.wait()
                
                with self._ffplay_lock:
                    still_ours = (self._ffplay_proc is proc)
                if still_ours:
                    with self._ffplay_lock:
                        self._ffplay_proc = None
                    if on_finish:
                        self.root.after(0, on_finish)
            threading.Thread(target=_watcher, daemon=True).start()

    # -----------------------------------------------------------------------#
    # -----------------------------------------------------------------------#

    def show_idle(self):
        """Route to the correct idle sub-mode. Always called on Tkinter thread."""
        if self.current_mode != 'idle':
            return
        mode = self.idle_display_mode
        if mode == 'slideshow':
            self.show_idle_slideshow(self._idle_gen, tick=0)
        elif mode == 'video':
            self.show_idle_video()
        else:
            self.show_idle_welcome(self._idle_gen)

    def show_idle_welcome(self, gen=None):
        if self.current_mode != 'idle' or self.idle_display_mode != 'welcome':
            return
        if gen is not None and gen != self._idle_gen:
            return

        self.canvas.delete('all')
        self.canvas.configure(bg='#800000')

        cx = self.display_width  // 2
        cy = self.display_height // 2

        self.canvas.create_text(cx, 70,
            text="ANNOUNCEMENT DISPLAY",
            font=('Arial', 56, 'bold'), fill='white')

        if os.path.exists(Config.LOGO_PATH):
            try:
                
                logo_px = int(self.display_height * 0.45)
                logo = Image.open(Config.LOGO_PATH).convert('RGBA')
                logo.thumbnail((logo_px, logo_px), Image.Resampling.LANCZOS)
                self._logo_image = ImageTk.PhotoImage(logo)
                self.canvas.create_image(cx, cy - 40, image=self._logo_image)
            except Exception as e:
                print(f"[welcome] logo error: {e}")

        self.canvas.create_text(cx, cy + 140,
            text=Config.IDLE_WELCOME_TEXT,
            font=('Arial', 44, 'bold'), fill='white')

        self.canvas.create_text(cx, cy + 220,
            text=self.quotes[self.quote_index],
            font=('Arial', 28, 'italic'), fill='#FFD700',
            width=self.display_width - 300, justify='center')

        try:
            import pytz
            _now = datetime.now(pytz.timezone('Asia/Manila'))
        except ImportError:
            _now = datetime.now()

        self.canvas.create_text(cx, self.display_height - 120,
            text=_now.strftime("%I:%M:%S %p"),
            font=('Arial', 72, 'bold'), fill='white')
        self.canvas.create_text(cx, self.display_height - 60,
            text=_now.strftime("%B %d, %Y"),
            font=('Arial', 36), fill='#FFD700')

        self._quote_counter += 1
        if self._quote_counter >= 30:
            self.quote_index    = (self.quote_index + 1) % len(self.quotes)
            self._quote_counter = 0

        captured = self._idle_gen
        self.root.after(1000, lambda: self.show_idle_welcome(captured))

    def show_idle_slideshow(self, gen=None, tick=0):
        if self.current_mode != 'idle' or self.idle_display_mode != 'slideshow':
            return
        if gen is not None and gen != self._idle_gen:
            return

        if not self._idle_pil_images:
            self.load_idle_images()
        if not self._idle_pil_images:
            self._idle_pil_images.append(self._default_idle_pil())

        interval = max(1, Config.IDLE_SLIDESHOW_INTERVAL)
        n        = len(self._idle_pil_images)

        if tick == 0 or (tick % interval == 0):
            pil_img = self._idle_pil_images[self.idle_index % n]
            _old    = self._slideshow_photo          
            self._slideshow_photo = ImageTk.PhotoImage(pil_img)
            del _old
            if tick > 0:
                self.idle_index = (self.idle_index + 1) % n

        self.canvas.delete('all')
        self.canvas.configure(bg='black')

        if self._slideshow_photo is not None:
            self.canvas.create_image(
                0, 0, image=self._slideshow_photo, anchor='nw')

        cx = self.display_width // 2

        try:
            import pytz
            _now = datetime.now(pytz.timezone('Asia/Manila'))
        except ImportError:
            _now = datetime.now()

        self.canvas.create_text(cx, self.display_height - 130,
            text=_now.strftime("%I:%M:%S %p"),
            font=('Arial', 72, 'bold'), fill='white')
        self.canvas.create_text(cx, self.display_height - 60,
            text=_now.strftime("%B %d, %Y"),
            font=('Arial', 38), fill='#FFD700')

        captured = self._idle_gen
        self.root.after(1000,
            lambda: self.show_idle_slideshow(captured, tick + 1))

    def show_idle_video(self):
        """
        Play idle videos using ffplay.
        ffplay handles its own frame scheduling at native speed.
        When one video ends, the next is queued automatically.
        """
        if self.current_mode != 'idle' or self.idle_display_mode != 'video':
            return

        self.load_idle_videos()

        if not self.idle_videos:
            print("[idle] No idle videos — falling back to welcome")
            self.idle_display_mode = 'welcome'
            self._idle_gen += 1
            self.show_idle_welcome(self._idle_gen)
            return

        video_path = self.idle_videos[self.idle_index % len(self.idle_videos)]
        self.idle_index = (self.idle_index + 1) % len(self.idle_videos)
        with_audio = Config.IDLE_SPEAKERS_ENABLED
        print(f"[idle] Playing idle video: {video_path}  audio={with_audio}")

        
        self.canvas.delete('all')
        self.canvas.configure(bg='black')

        captured_gen = self._idle_gen

        def _on_idle_video_end():
            
            if (self.current_mode == 'idle'
                    and self.idle_display_mode == 'video'
                    and self._idle_gen == captured_gen):
                self.root.after(200, self.show_idle_video)

        self._launch_ffplay(video_path, with_audio=with_audio,
                            on_finish=_on_idle_video_end, loop=False)

    # -----------------------------------------------------------------------#
    # -----------------------------------------------------------------------#

    def show_text(self, text, font_size=48, bg_color='#000000'):
        if self.current_mode != 'text':
            return

        self.canvas.delete('all')
        self.canvas.configure(bg=bg_color)
        self.canvas.create_text(
            self.display_width  // 2,
            self.display_height // 2,
            text=text,
            font=('Arial', font_size, 'bold'),
            fill='white',
            width=self.display_width - 100,
            justify='center'
        )

        try:
            import pytz
            ts = datetime.now(pytz.timezone('Asia/Manila')).strftime("%I:%M %p")
        except ImportError:
            ts = datetime.now().strftime("%I:%M %p")

        self.canvas.create_text(
            self.display_width - 50, self.display_height - 30,
            text=ts, font=('Arial', 20), fill='white', anchor='e'
        )

        if self.current_mode == 'text':
            self.root.after(1000,
                lambda t=text, f=font_size, b=bg_color: self.show_text(t, f, b))

    # -----------------------------------------------------------------------#
    # -----------------------------------------------------------------------#

    def show_video(self, video_path, with_audio=True):
        """
        Play an announcement video at full native speed via ffplay.
        ffplay runs as a standalone process — no frame decoding in Python.
        """
        if not os.path.exists(video_path):
            print(f"[video] File not found: {video_path}")
            self.current_mode = 'idle'
            self._idle_gen += 1
            self.root.after(0, self.show_idle)
            return

        
        self._idle_gen += 1

        
        self.canvas.delete('all')
        self.canvas.configure(bg='black')

        def _on_video_end():
            print("[video] finished naturally")
            if self.current_mode == 'video':
                self._on_video_ended()

        self._launch_ffplay(video_path, with_audio=with_audio,
                            on_finish=_on_video_end, loop=False)

    def _on_video_ended(self):
        """
        Called when ffplay exits naturally (video finished).
        Write mode='idle' back to DB so the app.py speaker-watcher thread
        detects the video->idle transition and turns off the relay speakers.
        """
        self.current_mode = 'idle'
        self._idle_gen += 1
        
        try:
            _db = sqlite3.connect(Config.DATABASE, timeout=3)
            _db.execute(
                "UPDATE current_display SET mode='idle', updated_at=? WHERE id=1",
                (datetime.now().isoformat(),)
            )
            _db.commit()
            _db.close()
        except Exception as _e:
            print(f"[video] DB write on end error: {_e}")
        self.root.after(0, self.show_idle)

    # -----------------------------------------------------------------------#
    
    # -----------------------------------------------------------------------#

    def show_emergency(self, text, audio_path=None):
        """Render the red flashing emergency screen and loop audio via pygame."""
        self.canvas.delete('all')
        self.canvas.configure(bg='#8B0000')
        cx = self.display_width  // 2
        cy = self.display_height // 2
        self.canvas.create_rectangle(0, 0, self.display_width, 200,
            fill='#FF0000', outline='')
        self.canvas.create_text(cx, 100,
            text="EMERGENCY ALERT",
            font=('Arial', 60, 'bold'), fill='white')
        self.canvas.create_text(cx, cy,
            text=text,
            font=('Arial', 70, 'bold'), fill='white',
            width=self.display_width - 100, justify='center')
        self.canvas.create_text(cx, self.display_height - 100,
            text="FOLLOW EMERGENCY PROCEDURES",
            font=('Arial', 40, 'bold'), fill='#FFD700')

        if audio_path:
            
            abs_path = os.path.abspath(audio_path)
            if os.path.exists(abs_path):
                try:
                    pygame.mixer.music.load(abs_path)
                    pygame.mixer.music.play(-1)   
                    print(f"[Emergency] Playing audio (loop): {abs_path}")
                except Exception as ae:
                    print(f"[Emergency] Audio error: {ae}")
            else:
                print(f"[Emergency] Audio file not found: {abs_path}")

        
        if self.current_mode == 'emergency':
            self.root.after(500,
                lambda: self.toggle_emergency_flash(text, audio_path))

    def toggle_emergency_flash(self, text, audio_path):
        """Alternate background colour every 500 ms while in emergency mode."""
        if self.current_mode != 'emergency':
            return
        current_bg = str(self.canvas.cget('bg'))
        new_bg = '#8B0000' if current_bg == '#FF0000' else '#FF0000'
        self.canvas.configure(bg=new_bg)
        self.root.after(500,
            lambda: self.toggle_emergency_flash(text, audio_path))

    def stop_emergency(self):
        """Stop flashing, silence audio, return to idle."""
        self.current_mode = 'idle'   
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        self._idle_gen += 1
        self.root.after(0, self.show_idle)

    # -----------------------------------------------------------------------#
    
    # -----------------------------------------------------------------------#

    def set_idle_mode(self, mode):
        if mode not in ('welcome', 'slideshow', 'video'):
            return False
        self.idle_display_mode = mode
        self.idle_index        = 0
        self._slideshow_photo  = None
        if self.current_mode == 'idle':
            self._stop_ffplay()   
            self._idle_gen += 1
            self.root.after(0, self.show_idle)
        return True

    def trigger_idle_now(self):
        """Stop everything and go to idle immediately."""
        self._stop_ffplay()
        pygame.mixer.music.stop()
        self.current_mode = 'idle'
        self._idle_gen   += 1
        self.root.after(0, self.show_idle)

    def trigger_announcement_now(self, announcement_type, **kwargs):
        if announcement_type == 'text':
            text      = kwargs.get('text', '')
            font_size = kwargs.get('font_size', 48)
            bg_color  = kwargs.get('bg_color', '#000000')
            self.current_mode = 'text'
            self.root.after(0,
                lambda t=text, f=font_size, b=bg_color: self.show_text(t, f, b))

        elif announcement_type == 'audio':
            audio_path = kwargs.get('audio_path', '')
            if os.path.exists(audio_path):
                self.root.after(0, lambda: self.play_audio(audio_path))

        elif announcement_type == 'video':
            video_path = kwargs.get('video_path', '')
            with_audio = kwargs.get('with_audio', True)
            if os.path.exists(video_path):
                self.current_mode = 'video'
                self.root.after(0,
                    lambda vp=video_path, wa=with_audio: self.show_video(vp, wa))

    def play_audio(self, audio_path):
        """Play audio via ffplay subprocess (non-blocking, no HDMI glitch)."""
        abs_path = os.path.abspath(audio_path)
        if not os.path.exists(abs_path):
            print(f"[audio] file not found: {abs_path}")
            return
        def _play():
            try:
                subprocess.Popen(
                    ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', abs_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                print(f"[audio] ffplay error: {e}")
        threading.Thread(target=_play, daemon=True, name='DisplayAudio').start()

    # -----------------------------------------------------------------------#
    # -----------------------------------------------------------------------#

    def monitor_database(self):
        """
        Poll current_display every second.
        Fires a display update ONLY when mode, idle sub-mode, or video content
        actually changes — never on a mere updated_at timestamp bump.
        """
        last_mode      = None
        last_idle_mode = None
        last_content   = None

        while self.running:
            try:
                db = sqlite3.connect(Config.DATABASE)
                db.row_factory = sqlite3.Row
                row = db.execute(
                    'SELECT * FROM current_display WHERE id = 1'
                ).fetchone()

                if row:
                    mode    = row['mode']
                    content = row['content']

                    
                    keys     = row.keys()
                    db_idle  = (row['idle_mode']
                                if 'idle_mode' in keys else None)
                    
                    idle_mode = db_idle or self.idle_display_mode

                    mode_changed    = (mode != last_mode)
                    idle_changed    = (mode == 'idle'
                                       and idle_mode != last_idle_mode)
                    content_changed = (mode == 'video'
                                       and content != last_content)

                    if mode_changed or idle_changed or content_changed:
                        print(f"[monitor] mode -> {mode}  idle_mode={idle_mode}")
                        last_mode      = mode
                        last_idle_mode = idle_mode
                        last_content   = content

                        
                        if mode == 'idle':
                            self._stop_ffplay()
                            
                            
                            if self.current_mode == 'emergency':
                                self.root.after(0, self.stop_emergency)
                            else:
                                self.current_mode      = 'idle'
                                self.idle_display_mode = idle_mode
                                self.idle_index        = 0
                                self._idle_gen        += 1
                                self.root.after(0, self.show_idle)

                        elif mode == 'text':
                            
                            self._stop_ffplay()
                            self.current_mode = 'text'
                            _t = row['content']   or ''
                            _f = row['font_size'] or 48
                            _b = row['bg_color']  or '#000000'
                            self.root.after(0,
                                lambda t=_t, f=_f, b=_b: self.show_text(t, f, b))

                        elif mode == 'video':
                            self.current_mode = 'video'
                            _p = row['content']
                            _a = bool(row['with_audio'])
                            self.root.after(0,
                                lambda p=_p, a=_a: self.show_video(p, a))

                        elif mode == 'emergency':
                            self._stop_ffplay()
                            self.current_mode = 'emergency'
                            _et = row['emergency_text'] or 'EMERGENCY ALERT'
                            
                            _ea = row['emergency_audio'] or None
                            self.root.after(0,
                                lambda t=_et, ap=_ea: self.show_emergency(t, ap))

                db.close()

            except Exception as e:
                print(f"[monitor] DB error: {e}")

            time.sleep(1)

    # -----------------------------------------------------------------------#
    
    # -----------------------------------------------------------------------#

    def run(self):
        print("Display system started")
        self.root.mainloop()

    def cleanup(self):
        self.running = False
        self._stop_ffplay()
        pygame.mixer.quit()


# ---------------------------------------------------------------------------#

# ---------------------------------------------------------------------------#

if __name__ == '__main__':
    display = DisplaySystem()
    try:
        display.run()
    except KeyboardInterrupt:
        print("\nShutting down display system...")
    finally:
        display.cleanup()
