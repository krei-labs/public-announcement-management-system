/*
 * Tanauan City College - Public Announcement System
 * Arduino Relay Control
 *
 * 6-channel relay module wiring:
 *   IN1 (Arduino pin 2) -> Speaker 1
 *   IN2 (Arduino pin 3) -> Speaker 2
 *   IN3 (Arduino pin 4) -> Speaker 3
 *   IN4 (Arduino pin 5) -> Speaker 4
 *   IN5 (Arduino pin 6) -> Green LED  (active = any speaker on)
 *   IN6 (Arduino pin 7) -> Red LED    (active = all speakers off)
 *
 * Serial commands (9600 baud):
 *   S<bitmask>     Set speakers by bitmask (bit0=Spk1 … bit3=Spk4)
 *                  e.g. S15 = all on, S1 = spk1 only, S0 = all off
 *   LR<0|1>        Red LED  off/on
 *   LG<0|1>        Green LED off/on
 *   T<1-4><0|1>    Test individual speaker  (T11=spk1 on, T10=spk1 off)
 *   A              All OFF
 *   X              All speakers ON
 *   ?              Print status
 */

// Relay pins — LOW = relay energised (active-low relay board)
const int SPEAKER_1 = 2;
const int SPEAKER_2 = 3;
const int SPEAKER_3 = 4;
const int SPEAKER_4 = 5;
const int GREEN_LED = 6;
const int RED_LED   = 7;

// Active-LOW relay board with devices wired to NC (normally-closed) terminals.
// NC contact is CLOSED when relay is NOT triggered (coil de-energised).
const int RELAY_ON  = HIGH;
const int RELAY_OFF = LOW;

bool speaker1 = false;
bool speaker2 = false;
bool speaker3 = false;
bool speaker4 = false;
bool redLED = true;
bool greenLED = false;

void setup() {
  // Set pins to OUTPUT and write RELAY_OFF immediately so relays don't
  // glitch ON during the Arduino boot / Serial.begin() initialisation phase.
  // RELAY_OFF = LOW = coil triggered = NC opens = device OFF  (safe state).
  int allPins[] = {SPEAKER_1, SPEAKER_2, SPEAKER_3, SPEAKER_4, GREEN_LED, RED_LED};
  for (int i = 0; i < 6; i++) {
    pinMode(allPins[i], OUTPUT);
    digitalWrite(allPins[i], RELAY_OFF);
  }

  Serial.begin(9600);

  // Override Red LED to ON now that pins are stable
  // RELAY_ON = HIGH = coil released = NC closed = Red LED ON
  digitalWrite(RED_LED, RELAY_ON);

  Serial.println("TCC Announcement System - Arduino Ready");
  Serial.println("State: All speakers OFF | Red LED ON | Green LED OFF");
  Serial.println("Commands: S<bitmask>, LR<0|1>, LG<0|1>, T<1-4><0|1>, A, X, ?");
}

void loop() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    
    if (command.length() > 0) {
      processCommand(command);
    }
  }
}

void processCommand(String cmd) {
  char type = cmd.charAt(0);
  
  switch (type) {
    case 'S':
      setSpeakersBitmask(cmd.substring(1).toInt());
      Serial.println("OK: Speakers set");
      break;
      
    case 'L':
      if (cmd.length() >= 3) {
        char ledColor = cmd.charAt(1);
        int state = cmd.substring(2).toInt();
        
        if (ledColor == 'R') {
          setLED(RED_LED, state);
          redLED = state;
          Serial.println("OK: Red LED " + String(state ? "ON" : "OFF"));
        } else if (ledColor == 'G') {
          setLED(GREEN_LED, state);
          greenLED = state;
          Serial.println("OK: Green LED " + String(state ? "ON" : "OFF"));
        }
      }
      break;
      
    case 'T':
      if (cmd.length() >= 3) {
        int speaker = cmd.substring(1, 2).toInt();
        int state = cmd.substring(2).toInt();
        testSpeaker(speaker, state);
        Serial.println("OK: Speaker " + String(speaker) + " " + String(state ? "ON" : "OFF"));
      }
      break;
      
    case 'A':
      allOff();
      Serial.println("OK: All OFF");
      break;
      
    case 'X':
      allSpeakersOn();
      Serial.println("OK: All speakers ON");
      break;
      
    case '?':
      printStatus();
      break;
      
    default:
      Serial.println("ERROR: Unknown command");
      break;
  }
}

void setSpeakersBitmask(int bitmask) {
  
  speaker1 = bitmask & 0x01;
  speaker2 = bitmask & 0x02;
  speaker3 = bitmask & 0x04;
  speaker4 = bitmask & 0x08;
  
  digitalWrite(SPEAKER_1, speaker1 ? RELAY_ON : RELAY_OFF);
  digitalWrite(SPEAKER_2, speaker2 ? RELAY_ON : RELAY_OFF);
  digitalWrite(SPEAKER_3, speaker3 ? RELAY_ON : RELAY_OFF);
  digitalWrite(SPEAKER_4, speaker4 ? RELAY_ON : RELAY_OFF);
  
  bool anySpeakerOn = speaker1 || speaker2 || speaker3 || speaker4;
  
  if (anySpeakerOn) {
    digitalWrite(RED_LED, RELAY_OFF);
    digitalWrite(GREEN_LED, RELAY_ON);
    redLED = false;
    greenLED = true;
  } else {
    digitalWrite(RED_LED, RELAY_ON);
    digitalWrite(GREEN_LED, RELAY_OFF);
    redLED = true;
    greenLED = false;
  }
}

void testSpeaker(int speaker, int state) {
  int pin;
  bool* speakerState;
  
  switch (speaker) {
    case 1:
      pin = SPEAKER_1;
      speakerState = &speaker1;
      break;
    case 2:
      pin = SPEAKER_2;
      speakerState = &speaker2;
      break;
    case 3:
      pin = SPEAKER_3;
      speakerState = &speaker3;
      break;
    case 4:
      pin = SPEAKER_4;
      speakerState = &speaker4;
      break;
    default:
      return;
  }
  
  *speakerState = state;
  digitalWrite(pin, state ? RELAY_ON : RELAY_OFF);
  
  bool anySpeakerOn = speaker1 || speaker2 || speaker3 || speaker4;
  
  if (anySpeakerOn) {
    digitalWrite(RED_LED, RELAY_OFF);
    digitalWrite(GREEN_LED, RELAY_ON);
    redLED = false;
    greenLED = true;
  } else {
    digitalWrite(RED_LED, RELAY_ON);
    digitalWrite(GREEN_LED, RELAY_OFF);
    redLED = true;
    greenLED = false;
  }
}

void setLED(int pin, int state) {
  digitalWrite(pin, state ? RELAY_ON : RELAY_OFF);
}

void allOff() {
  speaker1 = false;
  speaker2 = false;
  speaker3 = false;
  speaker4 = false;
  
  digitalWrite(SPEAKER_1, RELAY_OFF);
  digitalWrite(SPEAKER_2, RELAY_OFF);
  digitalWrite(SPEAKER_3, RELAY_OFF);
  digitalWrite(SPEAKER_4, RELAY_OFF);
  
  digitalWrite(RED_LED, RELAY_ON);
  digitalWrite(GREEN_LED, RELAY_OFF);
  redLED = true;
  greenLED = false;
}

void allSpeakersOn() {
  speaker1 = true;
  speaker2 = true;
  speaker3 = true;
  speaker4 = true;
  
  digitalWrite(SPEAKER_1, RELAY_ON);
  digitalWrite(SPEAKER_2, RELAY_ON);
  digitalWrite(SPEAKER_3, RELAY_ON);
  digitalWrite(SPEAKER_4, RELAY_ON);
  
  digitalWrite(RED_LED, RELAY_OFF);
  digitalWrite(GREEN_LED, RELAY_ON);
  redLED = false;
  greenLED = true;
}

void printStatus() {
  Serial.println("=== Speaker Status ===");
  Serial.println("Speaker 1: " + String(speaker1 ? "ON" : "OFF"));
  Serial.println("Speaker 2: " + String(speaker2 ? "ON" : "OFF"));
  Serial.println("Speaker 3: " + String(speaker3 ? "ON" : "OFF"));
  Serial.println("Speaker 4: " + String(speaker4 ? "ON" : "OFF"));
  Serial.println("Red LED: " + String(redLED ? "ON" : "OFF"));
  Serial.println("Green LED: " + String(greenLED ? "ON" : "OFF"));
  Serial.println("=====================");
}
