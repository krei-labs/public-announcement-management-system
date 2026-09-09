"""
Announcement Scheduler using APScheduler
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import sqlite3
import json
import os
import pytz
from config import Config


PH_TZ = pytz.timezone(Config.SCHEDULER_TIMEZONE)

def ph_now():
    """Return current datetime in Philippine Time (Asia/Manila)."""
    return datetime.now(PH_TZ)

class AnnouncementScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone=PH_TZ)
        self.jobs = {}
    
    def set_audio_handler(self, audio_handler):
        """Inject the shared AudioHandler instance so execute_announcement
        reuses the same singleton (with pre-detected ALSA devices) instead of
        creating a new one on every scheduled fire."""
        self._audio_handler = audio_handler

    def set_arduino(self, arduino):
        """Inject the shared Arduino controller so execute_announcement can
        activate/deactivate speakers without importing from app.py."""
        self._arduino = arduino

    def start(self):
        """Start the scheduler"""
        self.scheduler.start()
        print(f"Scheduler started  |  PH time now: {ph_now().strftime('%Y-%m-%d %H:%M:%S %Z')}")

        self.load_schedules()
    
    def stop(self):
        """Stop the scheduler"""
        self.scheduler.shutdown()
        print("Scheduler stopped")
    
    def load_schedules(self):
        """Load schedules from database"""
        try:
            db = sqlite3.connect(Config.DATABASE)
            db.row_factory = sqlite3.Row

            schedules = db.execute('SELECT * FROM schedules WHERE is_active = 1').fetchall()

            for schedule in schedules:
                duration = 0
                try:
                    duration = int(schedule['duration_seconds']) if schedule['duration_seconds'] else 0
                except Exception:
                    duration = 0

                self.add_job(
                    schedule['schedule_time'],
                    schedule['type'],
                    schedule['content'],
                    json.loads(schedule['speakers']) if schedule['speakers'] else [],
                    json.loads(schedule['repeat_days']) if schedule['repeat_days'] else [],
                    schedule['id'],
                    duration
                )

            db.close()
            print(f"Loaded {len(schedules)} schedules")

        except Exception as e:
            print(f"Error loading schedules: {e}")
    
    def add_job(self, schedule_time, announcement_type, content, speakers,
                repeat_days=None, job_id=None, duration_seconds=0):
        """
        Add a scheduled job.
        schedule_time   : "HH:MM" format
        announcement_type: 'text', 'audio', 'video', 'tts'
        content         : text or file path
        speakers        : list of speaker numbers
        repeat_days     : list of day abbreviations or None for daily
        duration_seconds: seconds to show before reverting to idle (0 = no revert)
        """
        try:
            parts  = schedule_time.split(':')   
            hour   = int(parts[0])
            minute = int(parts[1])

            
            if repeat_days:
                trigger = CronTrigger(
                    hour=hour, minute=minute,
                    day_of_week=','.join(repeat_days),
                    timezone=Config.SCHEDULER_TIMEZONE
                )
            else:
                trigger = CronTrigger(
                    hour=hour, minute=minute,
                    timezone=Config.SCHEDULER_TIMEZONE
                )

            job = self.scheduler.add_job(
                self.execute_announcement,
                trigger,
                args=[announcement_type, content, speakers, duration_seconds],
                id=str(job_id) if job_id else None,
                replace_existing=True
            )

            if job_id:
                self.jobs[job_id] = job

            print(f"Added scheduled job: {announcement_type} at {schedule_time}"
                  + (f" (duration: {duration_seconds}s)" if duration_seconds else ""))
            return True

        except Exception as e:
            print(f"Error adding job: {e}")
            return False
    
    def remove_job(self, job_id):
        """Remove a scheduled job"""
        try:
            self.scheduler.remove_job(str(job_id))
            if job_id in self.jobs:
                del self.jobs[job_id]
            print(f"Removed job {job_id}")
            return True
        except Exception as e:
            print(f"Error removing job: {e}")
            return False
    
    def execute_announcement(self, announcement_type, content, speakers, duration_seconds=0):
        """Execute a scheduled announcement, then revert to idle after duration_seconds (if > 0)."""
        print(f"Executing scheduled announcement: {announcement_type}"
              + (f" for {duration_seconds}s" if duration_seconds else ""))

        try:
            db  = sqlite3.connect(Config.DATABASE)
            now = ph_now().isoformat()

            _ah       = getattr(self, '_audio_handler', None)
            _arduino  = getattr(self, '_arduino', None)
            if _ah is None:
                from audio_handler import AudioHandler as _AH
                _ah = _AH()

            
            def _speakers_on():
                if _arduino and speakers:
                    _arduino.set_speakers(speakers)
                    print(f"[scheduler] Speakers ON: {speakers}")

            def _speakers_off():
                if _arduino:
                    _arduino.set_speakers([])
                    print(f"[scheduler] Speakers OFF")

            if announcement_type == 'text':
                db.execute(
                    '''UPDATE current_display
                       SET mode=?, content=?, font_size=?, bg_color=?, updated_at=?
                       WHERE id=1''',
                    ('text', content, 48, '#000000', now)
                )
                db.commit()
                print(f"[scheduler] current_display -> text  updated_at={now}")

            elif announcement_type == 'audio':
                audio_path = content if os.path.exists(content) \
                             else os.path.join(Config.AUDIO_FOLDER,
                                               os.path.basename(content))
                print(f"[scheduler] audio: resolved={audio_path!r}  exists={os.path.exists(audio_path)}")
                if os.path.exists(audio_path):
                    _speakers_on()
                    _ah.play(audio_path, speakers, on_done=_speakers_off)
                else:
                    print(f"[scheduler] Audio file not found: {audio_path}")

            elif announcement_type == 'video':
                
                video_path = content if os.path.exists(content) \
                             else os.path.join(Config.VIDEO_FOLDER,
                                               os.path.basename(content))
                print(f"[scheduler] video: resolved={video_path!r}  exists={os.path.exists(video_path)}")
                if not os.path.exists(video_path):
                    print(f"[scheduler] Video file not found: {video_path}")
                else:
                    _speakers_on()
                    db.execute(
                        '''UPDATE current_display
                           SET mode=?, content=?, with_audio=?, speakers=?, updated_at=?
                           WHERE id=1''',
                        ('video', video_path, 1,
                         json.dumps(speakers) if speakers else '[]', now)
                    )
                    db.commit()
                    print(f"[scheduler] current_display -> video  path={video_path}  speakers={speakers}")

            elif announcement_type == 'tts':
                print(f"[scheduler] tts: content={content!r}")
                tts_path = _ah.text_to_speech(content)
                print(f"[scheduler] tts_path={tts_path!r}  exists={os.path.exists(tts_path) if tts_path else False}")
                if tts_path and os.path.exists(tts_path):
                    _speakers_on()
                    _ah.play(tts_path, speakers, on_done=_speakers_off)
                else:
                    print(f"[scheduler] TTS generation failed for: {content}")

            db.execute('''INSERT INTO logs (timestamp, event_type, description, status)
                          VALUES (?, ?, ?, ?)''',
                       (now, 'SCHEDULED_ANNOUNCEMENT',
                        f'Executed {announcement_type}: {content}', 'success'))
            db.commit()
            db.close()

            
            if duration_seconds and int(duration_seconds) > 0:
                from datetime import timedelta
                revert_time   = ph_now() + timedelta(seconds=int(duration_seconds))
                revert_job_id = f"revert_{id(self)}_{int(revert_time.timestamp())}"
                self.scheduler.add_job(
                    self._revert_to_idle,
                    trigger='date',
                    run_date=revert_time,
                    timezone=PH_TZ,
                    id=revert_job_id,
                    replace_existing=True
                )
                print(f"Revert to idle scheduled in {duration_seconds}s  "
                      f"at PH {revert_time.strftime('%H:%M:%S')} (job: {revert_job_id})")

        except Exception as e:
            print(f"Error executing announcement: {e}")
            try:
                db = sqlite3.connect(Config.DATABASE)
                db.execute('''INSERT INTO logs (timestamp, event_type, description, status)
                              VALUES (?, ?, ?, ?)''',
                           (ph_now().isoformat(), 'SCHEDULED_ANNOUNCEMENT',
                            f'Error: {str(e)}', 'error'))
                db.commit()
                db.close()
            except Exception:
                pass

    def _revert_to_idle(self):
        """Revert current_display back to idle mode."""
        try:
            db  = sqlite3.connect(Config.DATABASE)
            now = ph_now().isoformat()
            db.execute(
                "UPDATE current_display SET mode='idle', content='', updated_at=? WHERE id=1",
                (now,)
            )
            db.commit()
            db.close()
            print(f"[scheduler] Reverted to idle at PH {ph_now().strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"Error reverting to idle: {e}")
    
    def get_next_run_time(self, job_id):
        """Get next run time for a job"""
        try:
            job = self.scheduler.get_job(str(job_id))
            if job:
                return job.next_run_time
            return None
        except:
            return None


if __name__ == '__main__':
    scheduler = AnnouncementScheduler()
    scheduler.start()
    
    scheduler.add_job(
        "14:30",
        "text",
        "Test scheduled announcement",
        [1, 2],
        job_id=999
    )
    
    print("Scheduler running. Press Ctrl+C to stop.")
    
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()
