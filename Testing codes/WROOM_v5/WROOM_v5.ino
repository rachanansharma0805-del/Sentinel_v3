// ═══════════════════════════════════════════════════════════════
// SENTINEL — MFA EXAM HALL SECURITY SYSTEM
// ESP32 WROOM — Main Controller v7  (PRODUCTION)
// ═══════════════════════════════════════════════════════════════
//
// CHANGES IN v7:
//  - Trespass: 3 consecutive readings required (no false triggers)
//  - Trespass: cooldown raised to 60s
//  - Trespass: HC-SR04 only active when truly idle (state=0, no auth)
//  - Face result: detailed OLED — shows confidence, match/no-match
//  - Face result: shows HOW entry was granted (method proof)
//  - Entry log: method shown on OLED for 4s before going home
//  - Buzzer: direct HIGH/LOW test mode via key 9 (for debugging)
//  - Buzzer: BC547 drive confirmed — active-HIGH on GPIO26
//  - QR: improved feedback — tells student to hold steady longer
//  - CAM ping: active ping before face scan (from v6)
//  - Face timeout: 25s (from v5)
//  - All flows sequential — no ghost triggers between states
//
// KEY ASSIGNMENTS:
//  1,2,4,5,7,8 = PIN digits (safe only — no 3,6,9)
//  A = Student auth start
//  B = Fallback menu / go back
//  C = Faculty mode + CONFIRM PIN
//  3 = CLEAR PIN entry
//  9 = Buzzer test (home screen only — for debugging)
//
// HC-SR04 PLACEMENT:
//  Point sensor at the DOORWAY/ENTRANCE — open space.
//  It should only detect someone when they walk INTO range.
//  Do NOT point at a wall. Ideal distance to trigger: 50-80cm.
//  Adjust TRESPASS_MAX_DIST below to match your setup.
// ═══════════════════════════════════════════════════════════════

#include <SPI.h>
#include <MFRC522.h>
#include <Keypad.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>

// ── UPDATE THESE ──────────────────────────────────────────────
const char* WIFI_SSID   = "vivo Y16";
const char* WIFI_PASS   = "standingnexttoyou";
const char* SERVER_IP   = "192.168.154.107";
const int   SERVER_PORT = 3000;
const char* MQTT_SERVER = "192.168.154.107";
const int   MQTT_PORT   = 1883;
const char* ROOM_ID     = "HALL-A";

// ── PIN ASSIGNMENTS ───────────────────────────────────────────
#define SS_PIN      5
#define RST_PIN     2
#define BUZZER_PIN  26
#define LED_GREEN   32
#define TRIG_PIN    12
#define ECHO_PIN    34

// ── TRESPASS TUNING ───────────────────────────────────────────
// Set TRESPASS_MAX_DIST to the distance (cm) at which a person
// standing in the doorway should trigger the alarm.
// Point HC-SR04 at the doorway entrance — open space in front.
// Test: Serial Monitor prints distance every 500ms in state 0.
#define TRESPASS_MIN_DIST    5    // ignore reflections < 5cm
#define TRESPASS_MAX_DIST   80    // trigger if closer than 80cm
#define TRESPASS_CONFIRMS    3    // need 3 consecutive readings
#define TRESPASS_COOLDOWN_MS 60000UL  // 60 seconds between alarms

// ── OLED ──────────────────────────────────────────────────────
#define SCREEN_W 128
#define SCREEN_H  64
Adafruit_SSD1306 oled(SCREEN_W, SCREEN_H, &Wire, -1);

// ── RFID ──────────────────────────────────────────────────────
MFRC522 rfid(SS_PIN, RST_PIN);

// ── KEYPAD ────────────────────────────────────────────────────
const byte ROWS = 3, COLS = 4;
char keys[ROWS][COLS] = {
  {'1','2','3','A'},
  {'4','5','6','B'},
  {'7','8','9','C'}
};
byte rowPins[ROWS] = {4, 14, 27};
byte colPins[COLS] = {13, 15, 25, 33};
Keypad keypad = Keypad(
  makeKeymap(keys), rowPins, colPins, ROWS, COLS);

// ── MQTT ──────────────────────────────────────────────────────
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

// ══════════════════════════════════════════════════════════════
// NON-BLOCKING BUZZER
// ══════════════════════════════════════════════════════════════
struct BuzzerPattern {
  int  onMs, offMs, repeats, current;
  bool isOn, active;
  unsigned long lastChange;
};

BuzzerPattern buzzer = {0, 0, 0, 0, false, false, 0};

void buzzerStart(int onMs, int offMs, int repeats) {
  digitalWrite(BUZZER_PIN, LOW);
  buzzer = {onMs, offMs, repeats, 0, true, true, millis()};
  digitalWrite(BUZZER_PIN, HIGH);
}

void buzzerUpdate() {
  if (!buzzer.active) return;
  unsigned long now = millis();
  if (buzzer.isOn) {
    if (now - buzzer.lastChange >= (unsigned long)buzzer.onMs) {
      digitalWrite(BUZZER_PIN, LOW);
      buzzer.isOn = false;
      buzzer.lastChange = now;
      if (++buzzer.current >= buzzer.repeats) buzzer.active = false;
    }
  } else {
    if (now - buzzer.lastChange >= (unsigned long)buzzer.offMs) {
      if (buzzer.current < buzzer.repeats) {
        digitalWrite(BUZZER_PIN, HIGH);
        buzzer.isOn = true;
        buzzer.lastChange = now;
      }
    }
  }
}

void buzzerStop() {
  buzzer.active = false;
  digitalWrite(BUZZER_PIN, LOW);
}

// Named patterns
void beepSuccess()    { buzzerStart(150, 80,  3); }  // 3 quick beeps
void beepFail()       { buzzerStart(600, 200, 2); }  // 2 long beeps
void beepWrongRoom()  { buzzerStart(900, 300, 2); }  // 2 extra long
void beepPINReq()     { buzzerStart(400, 400, 3); }  // 3 slow pulses
void beepStartup()    { buzzerStart(200, 100, 2); }  // startup
void beepTrespassAlarm() {                            // 10 rapid
  // Blocking for trespass — intentional, prevents re-trigger
  for (int i = 0; i < 10; i++) {
    digitalWrite(BUZZER_PIN, HIGH);
    digitalWrite(LED_GREEN,  HIGH);
    delay(150);
    digitalWrite(BUZZER_PIN, LOW);
    digitalWrite(LED_GREEN,  LOW);
    delay(100);
  }
}

// ══════════════════════════════════════════════════════════════
// STATE MACHINE
// 0=home 1=wait RFID 2=face scan 3=faculty PIN
// 4=student emergency PIN 5=fallback menu 6=QR scan 7=exit
// ══════════════════════════════════════════════════════════════
int  appState   = 0;
bool authActive = false;

String enteredPIN  = "";
String scannedUID  = "";
String studentName = "";
String studentRoll = "";
String studentSeat = "";

unsigned long lastDistCheck    = 0;
unsigned long faceTimeout      = 0;
unsigned long trespassCooldown = 0;
unsigned long stateTimeout     = 0;
bool waitingFaceResult         = false;
bool waitingQRResult           = false;

// CAM online tracking
bool          camOnline   = false;
unsigned long camLastSeen = 0;

// Trespass confirmation counter
int trespassConfirmCount = 0;

// HC-SR04 rolling average
#define DIST_SAMPLES 5
long distBuffer[DIST_SAMPLES];
int  distIdx = 0;

// ══════════════════════════════════════════════════════════════
// OLED HELPERS
// ══════════════════════════════════════════════════════════════
void showOLED(String l1, String l2 = "",
              String l3 = "", String l4 = "") {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  if (l2 == "" && l3 == "" && l4 == "") {
    oled.setTextSize(2);
    oled.setCursor(0, 20);
    oled.println(l1);
  } else {
    oled.setTextSize(1);
    oled.setCursor(0,  0); oled.println(l1);
    oled.setCursor(0, 16); oled.println(l2);
    oled.setCursor(0, 32); oled.println(l3);
    oled.setCursor(0, 48); oled.println(l4);
  }
  oled.display();
}

void showHome() {
  appState          = 0;
  authActive        = false;
  enteredPIN        = "";
  scannedUID        = "";
  studentName       = "";
  studentRoll       = "";
  waitingFaceResult = false;
  waitingQRResult   = false;
  stateTimeout      = 0;
  trespassConfirmCount = 0;
  buzzerStop();
  digitalWrite(LED_GREEN, LOW);
  showOLED(
    "SENTINEL v7",
    "Room: " + String(ROOM_ID),
    "A=Student  C=Faculty",
    "B=Fallback"
  );
}

void showError(String msg1, String msg2 = "") {
  showOLED("!! ERROR !!", msg1, msg2, "Returning...");
  beepFail();
  stateTimeout = millis() + 3000;
}

String pinDots() {
  String d = "";
  for (unsigned int i = 0; i < enteredPIN.length(); i++) d += "* ";
  return d;
}

bool isSafeDigit(char k) {
  return k == '1' || k == '2' || k == '4' ||
         k == '5' || k == '7' || k == '8';
}

// ══════════════════════════════════════════════════════════════
// HC-SR04 — 5-sample rolling average
// ══════════════════════════════════════════════════════════════
long getDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long dur = pulseIn(ECHO_PIN, HIGH, 30000);
  if (dur == 0) return 999;

  long dist = dur * 0.034 / 2;
  distBuffer[distIdx] = dist;
  distIdx = (distIdx + 1) % DIST_SAMPLES;

  long sum = 0;
  for (int i = 0; i < DIST_SAMPLES; i++) sum += distBuffer[i];
  return sum / DIST_SAMPLES;
}

// ══════════════════════════════════════════════════════════════
// HTTP POST
// ══════════════════════════════════════════════════════════════
String httpPost(String endpoint, String body) {
  if (WiFi.status() != WL_CONNECTED)
    return "{\"error\":\"wifi_disconnected\"}";
  HTTPClient http;
  String url = "http://" + String(SERVER_IP) +
               ":" + String(SERVER_PORT) + endpoint;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(8000);
  int    code = http.POST(body);
  String resp = (code > 0)
    ? http.getString()
    : "{\"error\":\"http_" + String(code) + "\"}";
  http.end();
  Serial.println("[HTTP] POST " + endpoint + " -> " + String(code));
  return resp;
}

// ══════════════════════════════════════════════════════════════
// WIFI
// ══════════════════════════════════════════════════════════════
void connectWiFi() {
  showOLED("Connecting...", "WiFi: " + String(WIFI_SSID));
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 30) {
    delay(500); Serial.print("."); tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi OK: " + WiFi.localIP().toString());
    showOLED("WiFi Connected!",
             WiFi.localIP().toString(),
             "Server: " + String(SERVER_IP),
             "Port: " + String(SERVER_PORT));
    delay(1500);
  } else {
    showOLED("WiFi FAILED!", "Check hotspot", "Restarting...", "");
    delay(3000);
    ESP.restart();
  }
}

// ══════════════════════════════════════════════════════════════
// MQTT CALLBACK
// ══════════════════════════════════════════════════════════════
void mqttCallback(char* topic, byte* payload, unsigned int len) {
  String msg = "";
  for (unsigned int i = 0; i < len; i++) msg += (char)payload[i];
  Serial.println("[MQTT] [" + String(topic) + "] " + msg);

  // ── CAM heartbeat ──────────────────────────────────────
  if (String(topic) == "exam/status") {
    StaticJsonDocument<128> doc;
    deserializeJson(doc, msg);
    if (String(doc["event"] | "") == "cam_online") {
      camOnline   = true;
      camLastSeen = millis();
      Serial.println("[CAM] Online confirmed");
    }
    return;
  }

  // ── FACE RESULT ────────────────────────────────────────
  if (String(topic) == "exam/face/result" && waitingFaceResult) {
    waitingFaceResult = false;
    buzzerStop();

    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, msg)) {
      showError("Face parse error", "Try again");
      appState = 5;
      return;
    }

    bool   match       = doc["match"]         | false;
    float  conf        = doc["confidence"]    | 999.0;
    bool   correctRoom = doc["correct_room"]  | false;
    String name        = doc["student_name"]  | "Unknown";
    String seat        = doc["seat_no"]       | "?";
    String assigned    = doc["assigned_room"] | "?";
    String reason      = doc["reason"]        | "";

    // ── Detailed face result feedback ──────────────────
    Serial.printf("[FACE] match=%s conf=%.1f correct_room=%s\n",
                  match ? "YES" : "NO", conf,
                  correctRoom ? "YES" : "NO");

    if (!match) {
      // Show confidence so student knows how close they were
      String confMsg = "Conf: " + String((int)conf) +
                       " (need <160)";
      showOLED("Face REJECTED!",
               confMsg,
               "B=fallback  A=retry",
               reason == "" ? "Hold steady+look cam" : reason);
      beepFail();
      appState = 5;
      return;
    }

    if (!correctRoom) {
      showOLED("WRONG ROOM!",
               name.substring(0, 16),
               "Go to: " + assigned,
               "This room: " + String(ROOM_ID));
      beepWrongRoom();
      Serial.println("[WRONG ROOM] " + name + " -> " + assigned);
      stateTimeout = millis() + 5000;
      return;
    }

    // ── SUCCESS — show proof of entry ──────────────────
    digitalWrite(LED_GREEN, HIGH);
    String confLine = "Conf:" + String((int)conf) + " Face+RFID";
    showOLED("ENTRY GRANTED!",
             name.substring(0, 16),
             "Seat: " + seat,
             confLine);
    beepSuccess();
    Serial.println("[ENTRY GRANTED] " + name +
                   " conf=" + String(conf, 1) +
                   " method=rfid+face");
    stateTimeout = millis() + 4000;
    return;
  }

  // ── QR RESULT ──────────────────────────────────────────
  if (String(topic) == "exam/qr/result" && waitingQRResult) {
    waitingQRResult = false;
    buzzerStop();

    StaticJsonDocument<256> doc;
    deserializeJson(doc, msg);
    bool   valid   = doc["valid"]      | false;
    bool   expired = doc["expired"]    | false;
    String roll    = doc["student_id"] | "?";
    String reason  = doc["reason"]     | "unknown";

    if (valid) {
      digitalWrite(LED_GREEN, HIGH);
      showOLED("QR ACCEPTED!",
               "ENTRY GRANTED!",
               roll.substring(0, 16),
               "Method: QR fallback");
      beepSuccess();
      Serial.println("[QR ENTRY] " + roll + " method=qr_fallback");
      stateTimeout = millis() + 4000;
    } else {
      String errMsg =
        expired                    ? "QR expired!"           :
        reason == "wrong_room"     ? "Wrong room QR!"        :
        reason == "already_used_today" ? "QR used today!"    :
        reason == "tampered"       ? "Invalid QR!"           :
        reason == "no_qr_detected" ? "QR not seen-hold still":
        "QR failed: " + reason;

      showOLED("QR FAILED!",
               errMsg.substring(0, 20),
               "Try again or press",
               "2=PIN  B=back");
      beepFail();
      appState = 5;
    }
  }
}

// ══════════════════════════════════════════════════════════════
// MQTT CONNECT
// ══════════════════════════════════════════════════════════════
void reconnectMQTT() {
  if (mqtt.connected()) return;
  Serial.print("[MQTT] connecting...");
  String cid = "wroom_" + String(ROOM_ID) + "_" +
               String((uint32_t)ESP.getEfuseMac(), HEX);
  if (mqtt.connect(cid.c_str())) {
    Serial.println("OK");
    mqtt.subscribe("exam/face/result");
    mqtt.subscribe("exam/qr/result");
    mqtt.subscribe("exam/anomaly/alert");
    mqtt.subscribe("exam/status");
  } else {
    Serial.println("FAIL rc=" + String(mqtt.state()));
  }
}

// ══════════════════════════════════════════════════════════════
// CORE: RFID TAP
// ══════════════════════════════════════════════════════════════
void handleRFIDTap(String uid) {
  Serial.println("[RFID] Card: " + uid);
  buzzerStop();

  showOLED("Card scanned...",
           uid.substring(0, 11),
           "Checking server...", "");

  StaticJsonDocument<128> req;
  req["rfid_uid"] = uid;
  req["room_id"]  = ROOM_ID;
  String reqBody;
  serializeJson(req, reqBody);

  String resp = httpPost("/student/rfid-check", reqBody);

  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, resp)) {
    showError("Server error!", "Is server.py running?");
    appState = 0;
    return;
  }

  bool found = doc["found"] | false;
  if (!found) {
    String reason = doc["reason"] | "unknown";
    showOLED("CARD UNKNOWN!",
             reason == "not_registered"
               ? "Not in system"
               : "Error: " + reason,
             uid.substring(0, 11),
             "Contact admin");
    beepFail();
    stateTimeout = millis() + 4000;
    return;
  }

  bool correctRoom = doc["correct_room"] | false;
  studentName = doc["student_name"] | "Unknown";
  studentRoll = doc["roll_no"]      | "?";
  studentSeat = doc["seat_no"]      | "?";
  scannedUID  = uid;

  if (!correctRoom) {
    String assigned = doc["assigned_room"] | "?";
    showOLED("WRONG ROOM!",
             studentName.substring(0, 16),
             "Your hall: " + assigned,
             "This hall: " + String(ROOM_ID));
    beepWrongRoom();
    stateTimeout = millis() + 5000;
    return;
  }

  // ── Ping CAM before triggering face scan ────────────
  if (!camOnline || millis() - camLastSeen > 30000) {
    Serial.println("[CAM] Pinging...");
    showOLED("Checking camera...",
             studentName.substring(0, 16),
             "Please wait...", "");
    mqtt.publish("exam/cam/ping", "ping");
    unsigned long pingStart = millis();
    while (!camOnline && millis() - pingStart < 3000) {
      mqtt.loop(); delay(100);
    }
    if (!camOnline) {
      showOLED("Camera offline!",
               "Check CAM power",
               "Wait 10s + retry",
               "or press B=fallback");
      beepFail();
      appState   = 5;
      scannedUID = uid;  // keep UID for fallback
      return;
    }
  }

  // ── RFID OK + CAM ready — start face scan ──────────
  beepSuccess();
  showOLED("Welcome!",
           studentName.substring(0, 16),
           "Seat: " + studentSeat,
           "Look at CAM now!");
  delay(1200);

  StaticJsonDocument<128> cam;
  cam["cmd"]        = "face_verify";
  cam["student_id"] = studentRoll;
  cam["room_id"]    = ROOM_ID;
  String camStr;
  serializeJson(cam, camStr);
  mqtt.publish("exam/cam/command", camStr.c_str());

  showOLED("Scanning face...",
           studentName.substring(0, 16),
           "Stay still, look cam",
           "B=fallback if fail");
  appState          = 2;
  waitingFaceResult = true;
  faceTimeout       = millis() + 25000;
}

// ══════════════════════════════════════════════════════════════
// CORE: FACULTY AUTH
// ══════════════════════════════════════════════════════════════
void handleFacultyAuth() {
  if (enteredPIN.length() < 4) {
    showOLED("Faculty Login",
             "PIN too short!",
             "Min 4 digits",
             "C=confirm 3=clear");
    beepFail();
    return;
  }

  showOLED("Verifying PIN...", "Please wait...", "", "");

  StaticJsonDocument<128> req;
  req["room_id"] = ROOM_ID;
  req["pin"]     = enteredPIN;
  String body;
  serializeJson(req, body);
  String resp = httpPost("/faculty/auth", body);

  StaticJsonDocument<512> res;
  if (deserializeJson(res, resp)) {
    showError("Server error!", "Try again");
    enteredPIN = "";
    return;
  }

  bool success = res["success"] | false;
  if (success) {
    String name    = res["faculty_name"] | "Faculty";
    String pin     = res["student_pin"]  | "------";
    String subject = res["subject"]      | "Exam";

    digitalWrite(LED_GREEN, HIGH);
    showOLED("AUTH SUCCESS!",
             name.substring(0, 16),
             subject.substring(0, 20),
             "Stud.PIN: " + pin);
    beepSuccess();
    Serial.println("[FACULTY OK] " + name +
                   " | Student PIN: " + pin);
    delay(6000);  // show PIN for 6 seconds
    showHome();
  } else {
    String reason = res["reason"] | "error";
    String errMsg =
      reason == "invalid_pin"      ? "Wrong PIN!" :
      reason == "no_session_today" ? "No session today!" :
      "Error: " + reason;
    showOLED("AUTH FAILED!",
             errMsg.substring(0, 20),
             "Safe digits:1,2,4,5,7,8",
             "C=retry  3=clear");
    beepFail();
    enteredPIN = "";
  }
}

// ══════════════════════════════════════════════════════════════
// CORE: STUDENT EMERGENCY PIN
// ══════════════════════════════════════════════════════════════
void handleStudentPIN() {
  if (enteredPIN.length() < 4) {
    showOLED("Emergency PIN",
             "Too short! Min 4",
             "digits needed",
             "C=confirm 3=clear");
    beepFail();
    return;
  }

  showOLED("Verifying PIN...", "Please wait...", "", "");

  StaticJsonDocument<128> req;
  req["room_id"]  = ROOM_ID;
  req["rfid_uid"] = scannedUID;
  req["pin"]      = enteredPIN;
  String body;
  serializeJson(req, body);
  String resp = httpPost("/student/auth/pin", body);

  StaticJsonDocument<256> res;
  deserializeJson(res, resp);

  if (res["success"] | false) {
    String name = res["student_name"] | "Student";
    digitalWrite(LED_GREEN, HIGH);
    showOLED("PIN ACCEPTED!",
             "ENTRY GRANTED!",
             name.substring(0, 16),
             "Method: Emerg. PIN");
    beepSuccess();
    Serial.println("[PIN ENTRY] " + name + " method=emergency_pin");
    stateTimeout = millis() + 4000;
  } else {
    showOLED("PIN REJECTED!",
             "Wrong PIN entered",
             "C=retry  3=clear",
             "B=back to menu");
    beepFail();
    enteredPIN = "";
  }
}

// ══════════════════════════════════════════════════════════════
// CORE: EXIT RECORDING
// ══════════════════════════════════════════════════════════════
void handleExit(String uid) {
  buzzerStop();
  showOLED("Recording exit...", "", "", "");

  StaticJsonDocument<128> req;
  req["room_id"]  = ROOM_ID;
  req["rfid_uid"] = uid;
  req["method"]   = "rfid";
  String body;
  serializeJson(req, body);
  String resp = httpPost("/student/exit", body);

  StaticJsonDocument<256> res;
  deserializeJson(res, resp);

  if (res["success"] | false) {
    String name = res["student_name"] | "Student";
    showOLED("Exit Recorded!",
             name.substring(0, 16),
             "Goodbye!",
             "Have a good day");
    buzzerStart(200, 100, 2);
    Serial.println("[EXIT] " + name);
  } else {
    showOLED("Exit Error!",
             "Card not found",
             "Contact admin", "");
    beepFail();
  }
  stateTimeout = millis() + 3000;
}

// ══════════════════════════════════════════════════════════════
// CORE: TRESPASS (v7 — requires 3 consecutive readings)
// ══════════════════════════════════════════════════════════════
void handleTrespass(long dist) {
  trespassCooldown     = millis() + TRESPASS_COOLDOWN_MS;
  trespassConfirmCount = 0;

  Serial.println("[TRESPASS] CONFIRMED dist=" +
                 String(dist) + "cm");

  showOLED("!! TRESPASS !!",
           "Unauthorized entry",
           "Security alerted!",
           "Dist: " + String(dist) + "cm");

  beepTrespassAlarm();  // blocking 10-beep alarm

  // Tell CAM to snapshot
  StaticJsonDocument<64> cam;
  cam["cmd"]    = "snapshot";
  cam["reason"] = "trespass";
  String camBody;
  serializeJson(cam, camBody);
  mqtt.publish("exam/cam/command", camBody.c_str());

  showOLED("TRESPASS logged!",
           "Admin notified",
           "Snapshot captured",
           "Cooldown: 60s");
  delay(3000);
  showHome();
}

// ══════════════════════════════════════════════════════════════
// SETUP
// ══════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n[BOOT] SENTINEL v7");
  Serial.println("[BOOT] Room: " + String(ROOM_ID));

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_GREEN,  OUTPUT);
  pinMode(TRIG_PIN,   OUTPUT);
  pinMode(ECHO_PIN,   INPUT);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(LED_GREEN,  LOW);
  digitalWrite(TRIG_PIN,   LOW);

  // Init distance buffer to "nothing detected"
  for (int i = 0; i < DIST_SAMPLES; i++) distBuffer[i] = 999;

  // OLED
  Wire.begin(21, 22);
  if (!oled.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("[ERROR] OLED not found!");
  } else {
    showOLED("SENTINEL v7",
             "Room: " + String(ROOM_ID),
             "Booting...", "");
    Serial.println("[BOOT] OLED OK");
  }

  // RFID
  SPI.begin(18, 19, 23, SS_PIN);
  rfid.PCD_Init();
  byte ver = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  Serial.println("[BOOT] RFID ver: 0x" + String(ver, HEX));
  if (ver == 0x00 || ver == 0xFF) {
    showOLED("RFID ERROR!", "Check: SS=5 SCK=18",
             "MOSI=23 MISO=19", "");
    delay(3000);
  }

  // WiFi
  connectWiFi();

  // MQTT
  mqtt.setServer(MQTT_SERVER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);
  reconnectMQTT();

  // Buzzer startup test — confirms buzzer is wired correctly
  Serial.println("[BOOT] Buzzer test...");
  beepStartup();
  delay(800);

  showHome();
  Serial.println("[BOOT] SENTINEL v7 ready!");
  Serial.println("[BOOT] Trespass range: " +
                 String(TRESPASS_MIN_DIST) + "-" +
                 String(TRESPASS_MAX_DIST) + "cm");
  Serial.println("[BOOT] Trespass confirms needed: " +
                 String(TRESPASS_CONFIRMS));
  Serial.println("[BOOT] Key 9 at home = buzzer test");
}

// ══════════════════════════════════════════════════════════════
// LOOP
// ══════════════════════════════════════════════════════════════
void loop() {
  buzzerUpdate();

  if (!mqtt.connected()) reconnectMQTT();
  mqtt.loop();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Dropped! Reconnecting...");
    showOLED("WiFi dropped!", "Reconnecting...", "", "");
    connectWiFi();
  }

  // State timeout — auto return home
  if (stateTimeout > 0 && millis() > stateTimeout) {
    stateTimeout = 0;
    showHome();
    return;
  }

  // Face scan timeout
  if (waitingFaceResult && millis() > faceTimeout) {
    waitingFaceResult = false;
    showOLED("Face timeout!",
             "CAM took too long",
             "B=fallback menu",
             "A=try again");
    beepFail();
    appState = 5;
  }

  // ── HC-SR04 TRESPASS CHECK ────────────────────────────
  // Only runs when: truly idle (state=0), no auth in progress,
  // cooldown expired, buzzer not active.
  // Requires TRESPASS_CONFIRMS consecutive readings to trigger.
  if (appState == 0 &&
      !authActive &&
      !buzzer.active &&
      millis() > trespassCooldown &&
      millis() - lastDistCheck > 600) {

    lastDistCheck = millis();
    long dist = getDistance();

    // Debug: print distance so you can calibrate placement
    Serial.println("[HC-SR04] dist=" + String(dist) + "cm");

    if (dist > TRESPASS_MIN_DIST && dist < TRESPASS_MAX_DIST) {
      trespassConfirmCount++;
      Serial.println("[TRESPASS] Confirm " +
                     String(trespassConfirmCount) + "/" +
                     String(TRESPASS_CONFIRMS));
      if (trespassConfirmCount >= TRESPASS_CONFIRMS) {
        handleTrespass(dist);
        return;
      }
    } else {
      // Nothing close — reset counter
      if (trespassConfirmCount > 0) {
        Serial.println("[TRESPASS] Reset counter");
        trespassConfirmCount = 0;
      }
    }
  }

  // ── KEYPAD ───────────────────────────────────────────
  char key = keypad.getKey();

  // ══════════════════════════════════════════════════════
  // STATE 0: HOME
  // ══════════════════════════════════════════════════════
  if (appState == 0) {
    if (key == 'A') {
      authActive = true;
      appState   = 1;
      buzzerStop();
      trespassConfirmCount = 0;
      showOLED("Student Entry",
               "Room: " + String(ROOM_ID),
               "Tap RFID card now",
               "B=fallback menu");
    }
    if (key == 'C') {
      authActive = true;
      appState   = 3;
      enteredPIN = "";
      buzzerStop();
      trespassConfirmCount = 0;
      showOLED("Faculty Login",
               "Room: " + String(ROOM_ID),
               "Enter PIN:",
               "C=confirm  3=clear");
    }
    if (key == 'B') {
      authActive = true;
      appState   = 5;
      scannedUID = "";
      trespassConfirmCount = 0;
      showOLED("Fallback Menu",
               "7 = QR scan",
               "2 = Student PIN",
               "B = go back");
    }
    // Key 9 = buzzer test (debug only)
    if (key == '9') {
      Serial.println("[TEST] Buzzer test triggered");
      showOLED("Buzzer Test", "Listen for beeps", "", "");
      // Direct drive test — bypasses non-blocking system
      for (int i = 0; i < 3; i++) {
        digitalWrite(BUZZER_PIN, HIGH);
        delay(300);
        digitalWrite(BUZZER_PIN, LOW);
        delay(200);
      }
      delay(500);
      showHome();
    }
  }

  // ══════════════════════════════════════════════════════
  // STATE 1: WAITING RFID
  // ══════════════════════════════════════════════════════
  else if (appState == 1) {
    if (key == 'B') {
      scannedUID = "";
      appState   = 5;
      showOLED("Fallback Menu",
               "7 = QR scan",
               "2 = Student PIN",
               "B = go back");
      return;
    }
    if (rfid.PICC_IsNewCardPresent() &&
        rfid.PICC_ReadCardSerial()) {
      String uid = "";
      for (byte i = 0; i < rfid.uid.size; i++) {
        if (rfid.uid.uidByte[i] < 0x10) uid += "0";
        uid += String(rfid.uid.uidByte[i], HEX);
      }
      uid.toUpperCase();
      rfid.PICC_HaltA();
      rfid.PCD_StopCrypto1();
      handleRFIDTap(uid);
    }
  }

  // ══════════════════════════════════════════════════════
  // STATE 2: FACE SCAN IN PROGRESS
  // ══════════════════════════════════════════════════════
  else if (appState == 2) {
    if (key == 'B') {
      waitingFaceResult = false;
      buzzerStop();
      appState = 5;
      showOLED("Fallback Menu",
               "7 = QR scan",
               "2 = Student PIN",
               "B = go back");
    }
    // Result arrives via mqttCallback
  }

  // ══════════════════════════════════════════════════════
  // STATE 3: FACULTY PIN ENTRY
  // ══════════════════════════════════════════════════════
  else if (appState == 3) {
    if (isSafeDigit(key)) {
      enteredPIN += key;
      showOLED("Faculty Login",
               "PIN: " + pinDots(),
               String(enteredPIN.length()) + " digits",
               "C=confirm  3=clear");
    }
    if (key == '3') {
      enteredPIN = "";
      showOLED("Faculty Login", "PIN cleared!", "Enter PIN:", "C=confirm");
    }
    if (key == 'C') handleFacultyAuth();
    if (key == 'B') showHome();
  }

  // ══════════════════════════════════════════════════════
  // STATE 4: STUDENT EMERGENCY PIN
  // ══════════════════════════════════════════════════════
  else if (appState == 4) {
    if (isSafeDigit(key)) {
      enteredPIN += key;
      showOLED("Emergency PIN",
               "PIN: " + pinDots(),
               String(enteredPIN.length()) + " digits",
               "C=confirm  3=clear");
    }
    if (key == '3') {
      enteredPIN = "";
      showOLED("Emergency PIN", "Cleared!", "Enter PIN given:", "C=confirm");
    }
    if (key == 'C') handleStudentPIN();
    if (key == 'B') {
      appState = 5;
      showOLED("Fallback Menu", "7=QR  2=PIN", "B=back", "");
    }
  }

  // ══════════════════════════════════════════════════════
  // STATE 5: FALLBACK MENU
  // ══════════════════════════════════════════════════════
  else if (appState == 5) {
    if (key == '7') {
      // QR scan — tell CAM to scan
      StaticJsonDocument<128> cam;
      cam["cmd"]        = "qr_scan";
      cam["student_id"] = studentRoll;
      cam["room_id"]    = ROOM_ID;
      String body;
      serializeJson(cam, body);
      mqtt.publish("exam/cam/command", body.c_str());

      showOLED("QR Scan",
               "Hold QR to camera",
               "15-20cm, no glare",
               "B=cancel");
      appState        = 6;
      waitingQRResult = true;
    }
    if (key == '2') {
      enteredPIN = "";
      appState   = 4;
      beepPINReq();
      showOLED("Emergency PIN",
               "Invigilator notified",
               "Enter PIN when given:",
               "C=confirm  3=clear");
    }
    if (key == 'B') showHome();
  }

  // ══════════════════════════════════════════════════════
  // STATE 6: QR SCAN IN PROGRESS
  // ══════════════════════════════════════════════════════
  else if (appState == 6) {
    if (key == 'B') {
      waitingQRResult = false;
      appState = 5;
      showOLED("Fallback Menu", "7=QR  2=PIN", "B=back", "");
    }
    // Result arrives via mqttCallback
  }

  // ══════════════════════════════════════════════════════
  // STATE 7: EXIT MODE
  // ══════════════════════════════════════════════════════
  else if (appState == 7) {
    if (key == 'B') { showHome(); return; }
    if (rfid.PICC_IsNewCardPresent() &&
        rfid.PICC_ReadCardSerial()) {
      String uid = "";
      for (byte i = 0; i < rfid.uid.size; i++) {
        if (rfid.uid.uidByte[i] < 0x10) uid += "0";
        uid += String(rfid.uid.uidByte[i], HEX);
      }
      uid.toUpperCase();
      rfid.PICC_HaltA();
      rfid.PCD_StopCrypto1();
      handleExit(uid);
    }
  }
}