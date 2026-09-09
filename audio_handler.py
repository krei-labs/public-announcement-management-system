"""
Audio Handler
- Text-to-Speech  : gTTS (online) with pyttsx3 offline fallback
- Playback        : pygame (non-blocking)
- Recording       : arecord → WAV → ffmpeg → MP3  (USB mic auto-detected)
- Live passthrough: arecord | aplay pipeline (USB mic → speakers)
"""

import os
import threading
import subprocess
import time
from datetime import datetime

import pygame
from config import Config


try:
    from gtts import gTTS
    _GTTS_OK = True
except ImportError:
    _GTTS_OK = False

try:
    import pyttsx3
    _PYTTSX3_OK = True
except ImportError:
    _PYTTSX3_OK = False


def _parse_alsa_card_device(line):
    """
    Parse an 'arecord -l' / 'aplay -l' card line such as:
        card 3: Microphone [USB Condenser Microphone], device 0: USB Audio [...]
    Returns (card_num, device_num) as strings, or (None, None) on failure.
    The format is:  card <N>: <name>, device <M>: ...
    """
    import re
    m = re.search(r'card\s+(\d+).*device\s+(\d+)', line)
    if m:
        return m.group(1), m.group(2)
    return None, None


def _find_usb_mic():
    """
    Scan 'arecord -l' for first USB microphone / condenser / audio capture.
    Returns plughw:CARD,DEV string.  Falls back to 'plughw:1,0'.
    """
    try:
        result = subprocess.run(
            ['arecord', '-l'], capture_output=True, text=True, timeout=5
        )
        print(f"[AudioHandler] arecord -l:\n{result.stdout}")
        for line in result.stdout.splitlines():
            low = line.lower()
            if 'usb' in low or 'microphone' in low or 'condenser' in low:
                card, dev = _parse_alsa_card_device(line)
                if card is not None:
                    device = f'plughw:{card},{dev}'
                    print(f"[AudioHandler] USB mic -> {device}")
                    return device
    except Exception as e:
        print(f"[AudioHandler] _find_usb_mic error: {e}")
    print("[AudioHandler] USB mic not found — falling back to plughw:1,0")
    return 'plughw:1,0'


def _find_playback_device():
    """
    Scan 'aplay -l' for playback device in priority order:
      1. USB audio output (non-HDMI)
      2. bcm2835 Headphones (RPi 3.5mm jack)
      3. Hard fallback plughw:1,0
    """
    try:
        result = subprocess.run(
            ['aplay', '-l'], capture_output=True, text=True, timeout=5
        )
        print(f"[AudioHandler] aplay -l:\n{result.stdout}")
        lines = result.stdout.splitlines()

        
        for line in lines:
            low = line.lower()
            if ('usb' in low or 'audio device' in low) and 'hdmi' not in low:
                card, dev = _parse_alsa_card_device(line)
                if card is not None:
                    device = f'plughw:{card},{dev}'
                    print(f"[AudioHandler] Playback (USB) -> {device}")
                    return device

        
        for line in lines:
            if 'headphone' in line.lower() and 'hdmi' not in line.lower():
                card, dev = _parse_alsa_card_device(line)
                if card is not None:
                    device = f'plughw:{card},{dev}'
                    print(f"[AudioHandler] Playback (headphones) -> {device}")
                    return device

    except Exception as e:
        print(f"[AudioHandler] _find_playback_device error: {e}")

    print("[AudioHandler] No playback device found — falling back to plughw:1,0")
    return 'plughw:1,0'


class AudioHandler:
    def __init__(self):
        
        
        self.is_playing    = False
        self.live_process  = None
        self._rec_process  = None
        self._play_process = None   
        self._play_thread  = None

    # ------------------------------------------------------------------ #
    
    # ------------------------------------------------------------------ #
    def play(self, audio_path, speakers=None, on_done=None):
        """
        Play an audio file in a background thread using aplay (WAV) or
        ffplay (MP3/any format).  Does NOT use pygame.mixer so the ALSA
        device used for playback is independent of the HDMI display signal,
        eliminating the display blank/flicker on Raspberry Pi.

        on_done: optional callable invoked after playback finishes or stops.
        """
        if not os.path.exists(audio_path):
            print(f"[AudioHandler] File not found: {audio_path}")
            return False

        
        self._stop_playback()

        def _do():
            self.is_playing = True
            try:
                ext = os.path.splitext(audio_path)[1].lower()
                out_dev = _find_playback_device()

                if ext == '.wav':
                    
                    cmd = ['aplay', '-D', out_dev, audio_path]
                else:
                    
                    
                    cmd = [
                        'ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet',
                        audio_path
                    ]

                print(f"[AudioHandler] Playing via {cmd[0]}: {audio_path}")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self._play_process = proc
                proc.wait()
                self._play_process = None
                print(f"[AudioHandler] Playback finished: {audio_path}")
            except Exception as e:
                print(f"[AudioHandler] Playback error: {e}")
                self._play_process = None
            finally:
                self.is_playing = False
                if callable(on_done):
                    try:
                        on_done()
                    except Exception as cb_err:
                        print(f"[AudioHandler] on_done callback error: {cb_err}")

        self._play_thread = threading.Thread(target=_do, daemon=True,
                                             name='AudioPlayback')
        self._play_thread.start()
        return True

    def _stop_playback(self):
        """Terminate the current aplay/ffplay process if running."""
        proc = self._play_process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._play_process = None

    def stop(self):
        """Stop all audio: playback, recording, and live stream."""
        self._stop_playback()
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        
        rec = getattr(self, '_rec_process', None)
        if rec:
            try:
                rec.terminate()
                rec.wait(timeout=2)
            except Exception:
                pass
            self._rec_process = None
        self.is_playing = False
        self._stop_live()

    # ------------------------------------------------------------------ #
    
    # ------------------------------------------------------------------ #
    def text_to_speech(self, text, output_path=None, lang='en'):
        """
        Convert text to MP3.  Returns the saved file path or None on error.
        Tries gTTS first (needs internet), falls back to pyttsx3.
        """
        os.makedirs(Config.AUDIO_FOLDER, exist_ok=True)

        if not output_path:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = os.path.join(Config.AUDIO_FOLDER, f'tts_{ts}.mp3')

        
        if _GTTS_OK:
            try:
                tts = gTTS(text=text, lang=lang, slow=False)
                tts.save(output_path)
                print(f"[AudioHandler] TTS saved (gTTS): {output_path}")
                return output_path
            except Exception as e:
                print(f"[AudioHandler] gTTS error: {e}")

        
        if _PYTTSX3_OK:
            try:
                engine = pyttsx3.init()
                engine.setProperty('rate', 160)
                
                wav_path = output_path.replace('.mp3', '.wav')
                engine.save_to_file(text, wav_path)
                engine.runAndWait()
                engine.stop()
                if os.path.exists(wav_path):
                    self._wav_to_mp3(wav_path, output_path)
                    os.remove(wav_path)
                    print(f"[AudioHandler] TTS saved (pyttsx3): {output_path}")
                    return output_path
            except Exception as e:
                print(f"[AudioHandler] pyttsx3 error: {e}")

        
        try:
            wav_path = output_path.replace('.mp3', '.wav')
            subprocess.run(
                ['espeak', '-w', wav_path, text],
                capture_output=True, timeout=30, check=True
            )
            if os.path.exists(wav_path):
                self._wav_to_mp3(wav_path, output_path)
                os.remove(wav_path)
                print(f"[AudioHandler] TTS saved (espeak): {output_path}")
                return output_path
        except Exception as e:
            print(f"[AudioHandler] espeak error: {e}")

        print("[AudioHandler] All TTS engines failed.")
        return None

    # ------------------------------------------------------------------ #
    
    # ------------------------------------------------------------------ #
    def record_audio(self, duration, output_path=None):
        """
        Record from USB mic for <duration> seconds.
        Returns path to MP3 file, or None on error.
        """
        os.makedirs(Config.RECORDED_FOLDER, exist_ok=True)

        if not output_path:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = os.path.join(Config.RECORDED_FOLDER, f'rec_{ts}.mp3')

        wav_path = output_path.replace('.mp3', '.wav')
        mic_dev  = _find_usb_mic()

        print(f"[AudioHandler] Recording {duration}s from {mic_dev} -> {wav_path}")

        try:
            proc = subprocess.Popen(
                ['arecord', '-D', mic_dev, '-f', 'S16_LE',
                 '-r', '44100', '-c', '1', '-t', 'wav',
                 '-d', str(int(duration)), wav_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            
            self._rec_process = proc
            self.is_playing   = True

            proc.wait()   

            self._rec_process = None
            self.is_playing   = False

            if proc.returncode not in (0, -15, 1):
                
                stderr_out = proc.stderr.read().decode('utf-8', errors='ignore')
                print(f"[AudioHandler] arecord exit {proc.returncode}: {stderr_out}")
                if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 100:
                    return None
        except Exception as e:
            print(f"[AudioHandler] Recording exception: {e}")
            self._rec_process = None
            self.is_playing   = False
            return None

        
        if os.path.exists(wav_path):
            ok = self._wav_to_mp3(wav_path, output_path)
            os.remove(wav_path)
            if ok:
                print(f"[AudioHandler] Recording saved: {output_path}")
                return output_path

        return None

    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    def play_live(self, speakers=None):
        """
        Route USB mic audio directly to the speaker output in real time
        using an arecord | aplay pipeline.  Runs until stop() is called.
        """
        self._stop_live()   

        mic_dev = _find_usb_mic()
        out_dev = _find_playback_device()
        print(f"[AudioHandler] Live: {mic_dev} -> {out_dev}")

        
        try:
            rec_proc = subprocess.Popen(
                ['arecord', '-D', mic_dev, '-f', 'S16_LE',
                 '-r', '44100', '-c', '1', '-t', 'raw'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            play_proc = subprocess.Popen(
                ['aplay', '-D', out_dev, '-f', 'S16_LE',
                 '-r', '44100', '-c', '1', '-t', 'raw'],
                stdin=rec_proc.stdout, stderr=subprocess.DEVNULL
            )
            rec_proc.stdout.close()   

            self.live_process  = play_proc
            self._rec_process  = rec_proc
            self.is_playing    = True

            print("[AudioHandler] Live audio pipeline started")

            
            while self.is_playing and play_proc.poll() is None:
                time.sleep(0.2)

        except Exception as e:
            print(f"[AudioHandler] Live audio error: {e}")
        finally:
            self._stop_live()

    def _stop_live(self):
        for attr in ('live_process', '_rec_process'):
            proc = getattr(self, attr, None)
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                setattr(self, attr, None)
        self.is_playing = False



    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    def _wav_to_mp3(self, wav_path, mp3_path):
        """Convert WAV to MP3 using ffmpeg. Returns True on success."""
        try:
            result = subprocess.run(
                [
                    'ffmpeg', '-y',
                    '-i', wav_path,
                    '-codec:a', 'libmp3lame',
                    '-qscale:a', '2',
                    mp3_path,
                ],
                capture_output=True, text=True, timeout=60
            )
            return result.returncode == 0
        except Exception as e:
            print(f"[AudioHandler] ffmpeg convert error: {e}")
            return False

    def get_audio_files(self, folder='audio'):
        """Return sorted list of audio files in the given sub-folder."""
        path = os.path.join(Config.UPLOAD_FOLDER, folder) \
               if folder == 'audio' else getattr(Config, f'{folder.upper()}_FOLDER', folder)
        if not os.path.exists(path):
            return []
        files = []
        for name in os.listdir(path):
            if name.lower().endswith(('.mp3', '.wav')):
                fp = os.path.join(path, name)
                files.append({
                    'name':     name,
                    'path':     fp,
                    'size':     os.path.getsize(fp),
                    'modified': os.path.getmtime(fp),
                })
        return sorted(files, key=lambda x: x['modified'], reverse=True)
