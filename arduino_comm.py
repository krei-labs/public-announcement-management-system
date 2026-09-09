"""
Arduino Serial Communication Controller
Handles relay control for speakers and LEDs.

Physical relay wiring (Arduino digital pins):
    Pin 2 (SPEAKER_1) -> Speaker 1   relay IN1
    Pin 3 (SPEAKER_2) -> Speaker 2   relay IN2
    Pin 4 (SPEAKER_3) -> Speaker 3   relay IN3
    Pin 5 (SPEAKER_4) -> Speaker 4   relay IN4
    Pin 6 (GREEN_LED) -> Green LED   relay IN5  (lights when speakers ON)
    Pin 7 (RED_LED)   -> Red LED     relay IN6  (lights when speakers OFF)

Serial command protocol (must match relay_control.ino exactly):
    S<bitmask>        Set speakers by bitmask (bit0=Spk1 … bit3=Spk4)
                      e.g. S15 = all on, S1 = spk1 only, S0 = all off
    LR<0|1>           Red LED  off/on  (LR1 = on, LR0 = off)
    LG<0|1>           Green LED off/on (LG1 = on, LG0 = off)
    T<1-4><0|1>       Test individual speaker (T11 = spk1 on, T10 = spk1 off)
    A                 All relays OFF
    X                 All speakers ON
    ?                 Print status to serial
"""

import serial
import time
import threading
from config import Config


class ArduinoController:
    def __init__(self, port=Config.ARDUINO_PORT, baud=Config.ARDUINO_BAUD):
        self.port           = port
        self.baud           = baud
        self.ser            = None
        self.connected      = False
        self.speaker_status = {1: False, 2: False, 3: False, 4: False}
        self.led_status     = {'red': True, 'green': False}  
        self.lock           = threading.Lock()
        self.connect()

    # ------------------------------------------------------------------
    
    # ------------------------------------------------------------------
    def connect(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(2)           # wait for Arduino reset
            self.connected = True
            print(f"[arduino] Connected on {self.port} @ {self.baud}")
        except serial.SerialException as e:
            print(f"[arduino] Connection failed: {e}")
            self.connected = False

    def reconnect(self, port=None):
        """Re-open serial connection, optionally on a different port."""
        self.disconnect()
        if port:
            self.port = port
        self.connect()

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.connected = False
        print("[arduino] Disconnected")

    def is_connected(self):
        return self.connected and bool(self.ser and self.ser.is_open)

    # ------------------------------------------------------------------
    
    # ------------------------------------------------------------------
    def send_command(self, command):
        """Send command string + newline; log the response."""
        with self.lock:
            if not self.is_connected():
                print(f"[arduino] NOT CONNECTED — skipped: {command!r}")
                return False
            try:
                self.ser.write(f"{command}\n".encode())
                time.sleep(0.05)
                if self.ser.in_waiting:
                    resp = self.ser.readline().decode(errors='replace').strip()
                    print(f"[arduino] {command!r} -> {resp!r}")
                return True
            except Exception as e:
                print(f"[arduino] send error ({command!r}): {e}")
                self.connected = False
                return False

    # ------------------------------------------------------------------
    
    # ------------------------------------------------------------------
    def set_speakers(self, speakers):
        """
        Activate speakers in the given list [1-4]; deactivate all others.
        Drives Green LED on when any speaker active, Red LED otherwise.
        The firmware's setSpeakersBitmask() already handles the LEDs,
        but we also track state locally.
        """
        for i in range(1, 5):
            self.speaker_status[i] = False
        for spk in speakers:
            if 1 <= spk <= 4:
                self.speaker_status[spk] = True

        
        bitmask = 0
        for spk in range(1, 5):
            if self.speaker_status[spk]:
                bitmask |= (1 << (spk - 1))

        active = bool(bitmask)
        self.led_status['green'] = active
        self.led_status['red']   = not active

        self.send_command(f"S{bitmask}")
        print(f"[arduino] set_speakers({speakers}) bitmask={bitmask} "
              f"green={'ON' if active else 'OFF'} red={'ON' if not active else 'OFF'}")

    def test_speaker(self, speaker, state):
        """
        Toggle one speaker on/off using the firmware's T command.
        Also updates local state so get_speaker_status() stays accurate.
        """
        if not (1 <= speaker <= 4):
            return
        self.speaker_status[speaker] = bool(state)

        
        active = any(self.speaker_status.values())
        self.led_status['green'] = active
        self.led_status['red']   = not active

        
        self.send_command(f"T{speaker}{'1' if state else '0'}")
        print(f"[arduino] test_speaker({speaker}, {state})")

    # ------------------------------------------------------------------
    
    # ------------------------------------------------------------------
    def set_led(self, color, state):
        """Drive red or green LED independently of speaker state."""
        if color not in ('red', 'green'):
            return
        self.led_status[color] = bool(state)
        if color == 'red':
            self.send_command(f"LR{'1' if state else '0'}")
            print(f"[arduino] Red LED -> {'ON' if state else 'OFF'}")
        else:
            self.send_command(f"LG{'1' if state else '0'}")
            print(f"[arduino] Green LED -> {'ON' if state else 'OFF'}")

    
    def red_on(self):    self.set_led('red',   True)
    def red_off(self):   self.set_led('red',   False)
    def green_on(self):  self.set_led('green', True)
    def green_off(self): self.set_led('green', False)

    # ------------------------------------------------------------------
    # Bulk helpers
    # ------------------------------------------------------------------
    def all_off(self):
        """All speakers OFF, Red LED ON, Green LED OFF  (firmware: A)."""
        for i in range(1, 5):
            self.speaker_status[i] = False
        self.led_status = {'red': True, 'green': False}
        self.send_command("A")
        print("[arduino] all_off()")

    def all_speakers_on(self):
        """All speakers ON  (firmware: X)."""
        for i in range(1, 5):
            self.speaker_status[i] = True
        self.led_status = {'red': False, 'green': True}
        self.send_command("X")
        print("[arduino] all_speakers_on()")

    def emergency_mode(self):
        self.all_speakers_on()

    def get_status_from_arduino(self):
        """Ask Arduino for live status (sends '?'); returns raw response."""
        self.send_command("?")

    # ------------------------------------------------------------------
    
    # ------------------------------------------------------------------
    def get_speaker_status(self):
        
        
        return {
            'speakers':  {str(k): v for k, v in self.speaker_status.items()},
            'leds':      self.led_status.copy(),
            'connected': self.is_connected(),
        }

    def __del__(self):
        self.disconnect()


# ---------------------------------------------------------------------------
# Manual test  (run: python arduino_comm.py)
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    ctrl = ArduinoController()
    if not ctrl.is_connected():
        print("Arduino not connected — check port.")
        raise SystemExit(1)

    print("\n--- All speakers ON ---")
    ctrl.all_speakers_on()
    time.sleep(2)

    print("\n--- Individual speakers ---")
    for i in range(1, 5):
        print(f"  Speaker {i}")
        ctrl.set_speakers([i])
        time.sleep(1)

    print("\n--- Combinations ---")
    ctrl.set_speakers([1, 3]);  time.sleep(1)
    ctrl.set_speakers([2, 4]);  time.sleep(1)

    print("\n--- LED direct control ---")
    ctrl.red_on();    time.sleep(1); ctrl.red_off()
    ctrl.green_on();  time.sleep(1); ctrl.green_off()

    print("\n--- All OFF ---")
    ctrl.all_off()
    print("Done.")
