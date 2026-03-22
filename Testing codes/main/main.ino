// ═══════════════════════════════════════════════════════
// MFA EXAM HALL SECURITY SYSTEM
// ESP32 WROOM — Main Controller v3
// ═══════════════════════════════════════════════════════
// KEY ASSIGNMENT:
//   PIN digits  → 1, 2, 4, 5, 7, 8  (safe only)
//   A           → Student auth start
//   B           → Fallback / go back
//   C           → Faculty mode + CONFIRM PIN
//   3           → CLEAR PIN
//   6, 9        → Reserved (do nothing)
// ═══════════════════════════════════════════════════════

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

// ── UPDATE THESE 4 LINES ────────────────────────────────
const char* WIFI_SSID   = "vivo Y16";
const char* WIFI_PASS   = "standingnexttoyou";
const char* SERVER_IP   = "192.168.154.107";
const int   SERVER_PORT = 3000;
const char* MQTT_SERVER = "192.168.154.107";
const int   MQTT_PORT   = 1883;
const char* ROOM_ID     = "HALL-A";

// ── PINS ────────────────────────────────────────────────
#define SS_PIN     5
#define RST_PIN    2
#define BUZZER_PIN 26
#define LED_GREEN  32
#define TRIG_PIN   12
#define ECHO_PIN   34

// ── OLED ────────────────────────────────────────────────
#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// ── RFID ────────────────────────────────────────────────
MFRC522 rfid(SS_PIN, RST_PIN);

// ── KEYPAD ──────────────────────────────────────────────
const byte ROWS = 3;
const byte COLS = 4;
char keys[ROWS][COLS] = {
  {'1','2','3','A'},
  {'4','5','6','B'},
  {'7','8','9','C'}
};
byte rowPins[ROWS] = {4, 14, 27};
byte colPins[COLS] = {13, 15, 25, 33};
Keypad keypad = Keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

// ── MQTT ────────────────────────────────────────────────
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

// ── STATE MACHINE ───────────────────────────────────────
int  state      = 0;
bool authActive = false;

String enteredPIN  = "";
String scannedUID  = "";
String studentName = "";
String studentRoll = "";
String studentSeat = "";

unsigned long lastDistCheck  = 0;
unsigned long faceTimeout    = 0;
bool waitingFaceResult       = false;
bool waitingQRResult         = false;

bool isSafeDigit(char k) {
  return k=='1'||k=='2'||k=='4'||
         k=='5'||k=='7'||k=='8';
}

// ══════════════════════════════════════════════════════
// BUZZER + LED
// ══════════════════════════════════════════════════════
void beep(int dur) {
  digitalWrite(BUZZER_PIN, HIGH);
  delay(dur);
  digitalWrite(BUZZER_PIN, LOW);
}

void successSignal() {
  digitalWrite(LED_GREEN, HIGH);
  beep(100); delay(80);
  beep(100); delay(80);
  beep(100);
  delay(1500);
  digitalWrite(LED_GREEN, LOW);
}

void failSignal() {
  digitalWrite(LED_GREEN, LOW);
  beep(500); delay(200); beep(500);
}

void wrongRoomSignal() {
  beep(800); delay(300); beep(800);
}

void trespassSignal() {
  for (int i = 0; i < 10; i++) {
    digitalWrite(LED_GREEN, HIGH);
    beep(120);
    digitalWrite(LED_GREEN, LOW);
    delay(80);
  }
}

void pinRequestSignal() {
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_GREEN, HIGH);
    beep(400);
    digitalWrite(LED_GREEN, LOW);
    delay(400);
  }
}

void startupSignal() {
  digitalWrite(LED_GREEN, HIGH);
  beep(200); delay(100); beep(200);
  delay(200);
  digitalWrite(LED_GREEN, LOW);
}

// ══════════════════════════════════════════════════════
// OLED
// ══════════════════════════════════════════════════════
void showOLED(String l1, String l2="",
              String l3="", String l4="") {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0,  0); display.println(l1);
  display.setCursor(0, 16); display.println(l2);
  display.setCursor(0, 32); display.println(l3);
  display.setCursor(0, 48); display.println(l4);
  display.display();
}

void showHome() {
  state             = 0;
  authActive        = false;
  enteredPIN        = "";
  scannedUID        = "";
  studentName       = "";
  studentRoll       = "";
  waitingFaceResult = false;
  waitingQRResult   = false;
  showOLED(
    "MFA Exam System",
    "Room: " + String(ROOM_ID),
    "A=Student  C=Faculty",
    "B=Fallback"
  );
}

String pinDots() {
  String d = "";
  for (int i=0; i<enteredPIN.length(); i++) d += "* ";
  return d;
}

// ══════════════════════════════════════════════════════
// DISTANCE
// ══════════════════════════════════════════════════════
long getDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long dur = pulseIn(ECHO_PIN, HIGH, 30000);
  return dur * 0.034 / 2;
}

// ══════════════════════════════════════════════════════
// HTTP POST
// ══════════════════════════════════════════════════════
String httpPost(String endpoint, String body) {
  if (WiFi.status() != WL_CONNECTED)
    return "{\"error\":\"wifi_disconnected\"}";
  HTTPClient http;
  String url = "http://" + String(SERVER_IP) +
               ":" + String(SERVER_PORT) + endpoint;
  http.begin(url);
  http.addHeader("Content-Type","application/json");
  http.setTimeout(8000);
  int code = http.POST(body);
  String resp = (code > 0) ? http.getString()
                           : "{\"error\":\"http_fail\"}";
  http.end();
  Serial.println("POST " + endpoint + " [" + String(code) + "]");
  Serial.println(resp);
  return resp;
}

// ══════════════════════════════════════════════════════
// WIFI
// ══════════════════════════════════════════════════════
void connectWiFi() {
  showOLED("Connecting WiFi...", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 30) {
    delay(500);
    Serial.print(".");
    tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi OK: " + WiFi.localIP().toString());
    showOLED("WiFi Connected!", WiFi.localIP().toString());
    delay(1000);
  } else {
    showOLED("WiFi FAILED!", "Restarting...");
    delay(3000);
    ESP.restart();
  }
}

// ══════════════════════════════════════════════════════
// MQTT CALLBACK
// ══════════════════════════════════════════════════════
void mqttCallback(char* topic, byte* payload,
                  unsigned int length) {
  String msg = "";
  for (int i=0; i<length; i++) msg += (char)payload[i];
  Serial.println("MQTT [" + String(topic) + "]: " + msg);

  if (String(topic) == "exam/face/result"
      && waitingFaceResult) {
    StaticJsonDocument<512> doc;
    deserializeJson(doc, msg);
    bool   match       = doc["match"].as<bool>();
    float  conf        = doc["confidence"].as<float>();
    bool   correctRoom = doc["correct_room"].as<bool>();
    String name        = doc["student_name"].as<String>();
    String seat        = doc["seat_no"].as<String>();
    String assigned    = doc["assigned_room"].as<String>();
    waitingFaceResult  = false;

    if (!match) {
      showOLED("Face NOT matched!",
               "Press B for fallback",
               "or try again", "");
      failSignal();
      state = 5;
      return;
    }
    if (!correctRoom) {
      showOLED("WRONG ROOM!",
               name,
               "Go to: " + assigned,
               "This is: " + String(ROOM_ID));
      wrongRoomSignal();
      delay(4000);
      showHome();
      return;
    }
    showOLED("Face MATCHED!",
             "ENTRY GRANTED!",
             name,
             "Seat: " + seat);
    successSignal();
    delay(3000);
    showHome();
  }

  if (String(topic) == "exam/qr/result"
      && waitingQRResult) {
    StaticJsonDocument<256> doc;
    deserializeJson(doc, msg);
    bool   valid   = doc["valid"].as<bool>();
    bool   expired = doc["expired"].as<bool>();
    String roll_no = doc["student_id"].as<String>();
    String reason  = doc["reason"].as<String>();
    waitingQRResult = false;

    if (valid) {
      showOLED("QR VALID!","ENTRY GRANTED!",roll_no,"");
      successSignal();
      delay(3000);
      showHome();
    } else {
      String errMsg = expired ? "QR expired!"
        : reason == "wrong_room" ? "Wrong room QR!"
        : reason == "already_used" ? "Already used!"
        : "Invalid QR!";
      showOLED("QR FAILED!",errMsg,"B=back  2=PIN","");
      failSignal();
      state = 5;
    }
  }
}

void reconnectMQTT() {
  if (mqtt.connected()) return;
  Serial.print("MQTT connecting...");
  if (mqtt.connect("esp32wroom_hall")) {
    Serial.println("connected!");
    mqtt.subscribe("exam/face/result");
    mqtt.subscribe("exam/qr/result");
    mqtt.subscribe("exam/anomaly/alert");
  } else {
    Serial.println("failed rc=" + String(mqtt.state()));
  }
}

// ══════════════════════════════════════════════════════
// RFID TAP
// ══════════════════════════════════════════════════════
void handleRFIDTap(String uid) {
  Serial.println("Card: " + uid);
  showOLED("Card scanned...","Checking room...","","");
  beep(80);

  StaticJsonDocument<128> req;
  req["rfid_uid"] = uid;
  req["room_id"]  = ROOM_ID;
  String reqBody;
  serializeJson(req, reqBody);
  String resp = httpPost("/student/rfid-check", reqBody);

  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, resp)) {
    showOLED("Server error!","Try again","","");
    failSignal(); delay(2000); showHome(); return;
  }

  bool found = doc["found"].as<bool>();
  if (!found) {
    showOLED("Card not found!",
             "Not registered",
             uid.substring(0,8),
             "Contact admin");
    failSignal(); delay(3000); showHome(); return;
  }

  bool correctRoom = doc["correct_room"].as<bool>();
  studentName = doc["student_name"].as<String>();
  studentRoll = doc["roll_no"].as<String>();
  studentSeat = doc["seat_no"].as<String>();
  scannedUID  = uid;

  if (!correctRoom) {
    String assigned = doc["assigned_room"].as<String>();
    showOLED("!! WRONG ROOM !!",
             studentName,
             "Your room: " + assigned,
             "Here: " + String(ROOM_ID));
    wrongRoomSignal();
    delay(5000);
    showHome();
    return;
  }

  showOLED("Welcome " + studentName + "!",
           "Seat: " + studentSeat,
           "Look at camera...",
           "Starting face scan");
  delay(1500);

  StaticJsonDocument<128> camCmd;
  camCmd["cmd"]        = "face_verify";
  camCmd["student_id"] = studentRoll;
  camCmd["room_id"]    = ROOM_ID;
  String camStr;
  serializeJson(camCmd, camStr);
  mqtt.publish("exam/cam/command", camStr.c_str());

  showOLED("Scanning face...", studentName,
           "Stay still please", "B=fallback");
  state             = 2;
  waitingFaceResult = true;
  faceTimeout       = millis() + 15000;
}

// ══════════════════════════════════════════════════════
// FACULTY AUTH
// ══════════════════════════════════════════════════════
void handleFacultyAuth() {
  if (enteredPIN.length() == 0) {
    showOLED("Faculty Login","No PIN entered!",
             "Use 1,2,4,5,7,8","C=confirm");
    failSignal(); return;
  }
  showOLED("Verifying PIN...","","","");

  StaticJsonDocument<128> doc;
  doc["room_id"] = ROOM_ID;
  doc["pin"]     = enteredPIN;
  String body;
  serializeJson(doc, body);
  String resp = httpPost("/faculty/auth", body);

  StaticJsonDocument<512> res;
  deserializeJson(res, resp);

  if (res["success"].as<bool>()) {
    String name    = res["faculty_name"].as<String>();
    String pin     = res["student_pin"].as<String>();
    String subject = res["subject"].as<String>();
    showOLED("AUTH OK! Welcome!",
             name,
             subject,
             "Stud PIN: " + pin);
    successSignal();
    Serial.println("FACULTY: " + name);
    Serial.println("Student PIN: " + pin);
    delay(5000);
    showHome();
  } else {
    String reason = res["reason"].as<String>();
    showOLED("Faculty FAILED!",
             reason == "invalid_pin" ? "Wrong PIN!"
             : reason,
             "3=clear  C=retry",
             "Digits: 1,2,4,5,7,8");
    failSignal();
    delay(2000);
    enteredPIN = "";
    showOLED("Faculty Login",
             "Room: " + String(ROOM_ID),
             "Enter PIN:",
             "C=confirm  3=clear");
  }
}

// ══════════════════════════════════════════════════════
// STUDENT EMERGENCY PIN
// ══════════════════════════════════════════════════════
void handleStudentPIN() {
  if (enteredPIN.length() == 0) {
    showOLED("Emergency PIN","No PIN!",
             "C=confirm 3=clear",""); failSignal(); return;
  }
  showOLED("Verifying PIN...","","","");

  StaticJsonDocument<128> doc;
  doc["room_id"]  = ROOM_ID;
  doc["rfid_uid"] = scannedUID;
  doc["pin"]      = enteredPIN;
  String body;
  serializeJson(doc, body);
  String resp = httpPost("/student/auth/pin", body);

  StaticJsonDocument<256> res;
  deserializeJson(res, resp);

  if (res["success"].as<bool>()) {
    showOLED("PIN ACCEPTED!",
             "ENTRY GRANTED!",
             res["student_name"].as<String>(),
             "Attendance saved");
    successSignal();
    delay(3000);
    showHome();
  } else {
    showOLED("Wrong PIN!","Access denied",
             "3=clear  C=retry","B=back");
    failSignal();
    delay(2000);
    enteredPIN = "";
    showOLED("Emergency PIN","Enter PIN:",
             "C=confirm  3=clear","B=back");
  }
}

// ══════════════════════════════════════════════════════
// EXIT
// ══════════════════════════════════════════════════════
void handleExit(String uid) {
  showOLED("Recording exit...","","","");
  StaticJsonDocument<128> doc;
  doc["room_id"]  = ROOM_ID;
  doc["rfid_uid"] = uid;
  doc["method"]   = "rfid";
  String body;
  serializeJson(doc, body);
  String resp = httpPost("/student/exit", body);
  StaticJsonDocument<256> res;
  deserializeJson(res, resp);
  if (res["success"].as<bool>()) {
    showOLED("Exit recorded!",
             res["student_name"].as<String>(),
             "Goodbye!","");
    beep(200); delay(100); beep(200);
  } else {
    showOLED("Exit error!","Contact admin","","");
    failSignal();
  }
  delay(2000);
  showHome();
}

// ══════════════════════════════════════════════════════
// TRESPASS
// ══════════════════════════════════════════════════════
void handleTrespass(long dist) {
  Serial.println("TRESPASS! dist=" + String(dist));
  showOLED("!! TRESPASS !!","Unauthorised person",
           "Admin notified!",
           "dist=" + String(dist) + "cm");
  trespassSignal();
  StaticJsonDocument<64> doc;
  doc["cmd"]    = "snapshot";
  doc["reason"] = "trespass";
  String body;
  serializeJson(doc, body);
  mqtt.publish("exam/cam/command", body.c_str());
  delay(2000);
  showHome();
}

// ══════════════════════════════════════════════════════
// SETUP
// ══════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  Serial.println("\nMFA Exam System v3 booting...");

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_GREEN,  OUTPUT);
  pinMode(TRIG_PIN,   OUTPUT);
  pinMode(ECHO_PIN,   INPUT);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(LED_GREEN,  LOW);

  Wire.begin(21, 22);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("OLED failed!");
    while(true);
  }
  showOLED("MFA Exam v3","Booting...","","");

  SPI.begin();
  rfid.PCD_Init();
  Serial.println("RFID OK");

  connectWiFi();

  mqtt.setServer(MQTT_SERVER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);
  reconnectMQTT();

  startupSignal();
  showHome();
  Serial.println("System ready!");
  Serial.println("Safe PIN: 1,2,4,5,7,8");
  Serial.println("C=confirm 3=clear B=back");
}

// ══════════════════════════════════════════════════════
// LOOP
// ══════════════════════════════════════════════════════
void loop() {
  if (!mqtt.connected()) reconnectMQTT();
  mqtt.loop();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped! Reconnecting...");
    connectWiFi();
  }

  if (millis() - lastDistCheck > 300) {
    lastDistCheck = millis();
    long dist = getDistance();
    if (dist > 0 && dist < 40
        && !authActive && state == 0) {
      handleTrespass(dist);
      return;
    }
  }

  if (waitingFaceResult && millis() > faceTimeout) {
    waitingFaceResult = false;
    showOLED("Face timeout!","Press B fallback","","");
    failSignal();
    state = 5;
  }

  char key = keypad.getKey();

  if (state == 0) {
    if (key == 'A') {
      authActive = true; state = 1;
      showOLED("Student Auth",
               "Room: " + String(ROOM_ID),
               "Tap RFID card...", "B=fallback");
    }
    if (key == 'C') {
      authActive = true; state = 3; enteredPIN = "";
      showOLED("Faculty Login",
               "Room: " + String(ROOM_ID),
               "Enter PIN (1,2,4,5,7,8):",
               "C=confirm  3=clear");
    }
  }

  else if (state == 1) {
    if (key == 'B') {
      scannedUID = ""; state = 5;
      showOLED("Fallback Menu","7=QR scan",
               "2=Student PIN","B=go back"); return;
    }
    if (rfid.PICC_IsNewCardPresent() &&
        rfid.PICC_ReadCardSerial()) {
      String uid = "";
      for (byte i=0; i<rfid.uid.size; i++) {
        if (rfid.uid.uidByte[i] < 0x10) uid += "0";
        uid += String(rfid.uid.uidByte[i], HEX);
      }
      uid.toUpperCase();
      rfid.PICC_HaltA();
      rfid.PCD_StopCrypto1();
      handleRFIDTap(uid);
    }
  }

  else if (state == 2) {
    if (key == 'B') {
      waitingFaceResult = false;
      showOLED("Fallback Menu","7=QR scan",
               "2=Student PIN","B=go back");
      state = 5;
    }
  }

  else if (state == 3) {
    if (isSafeDigit(key)) {
      enteredPIN += key;
      showOLED("Faculty Login",
               "PIN: " + pinDots(),
               "(" + String(enteredPIN.length()) + " digits)",
               "C=confirm  3=clear");
    }
    if (key == '3') {
      enteredPIN = "";
      showOLED("Faculty Login","PIN cleared!",
               "Enter PIN:","C=confirm  3=clear");
    }
    if (key == 'C') handleFacultyAuth();
    if (key == 'B') showHome();
  }

  else if (state == 4) {
    if (isSafeDigit(key)) {
      enteredPIN += key;
      showOLED("Emergency PIN",
               "PIN: " + pinDots(),
               "(" + String(enteredPIN.length()) + " digits)",
               "C=confirm  3=clear");
    }
    if (key == '3') {
      enteredPIN = "";
      showOLED("Emergency PIN","PIN cleared!",
               "Enter PIN:","C=confirm  3=clear");
    }
    if (key == 'C') handleStudentPIN();
    if (key == 'B') {
      state = 5;
      showOLED("Fallback Menu","7=QR scan",
               "2=Student PIN","B=go back");
    }
  }

  else if (state == 5) {
    if (key == '7') {
      StaticJsonDocument<128> doc;
      doc["cmd"]="qr_scan"; doc["student_id"]=studentRoll;
      doc["room_id"]=ROOM_ID;
      String body; serializeJson(doc, body);
      mqtt.publish("exam/cam/command", body.c_str());
      showOLED("QR Scan","Show QR to camera",
               "Hold steady...","B=cancel");
      state = 6; waitingQRResult = true;
    }
    if (key == '2') {
      enteredPIN = ""; state = 4;
      pinRequestSignal();
      showOLED("Emergency PIN",
               "Invigilator notified!",
               "Enter PIN given:",
               "C=confirm  3=clear");
    }
    if (key == 'B') showHome();
  }

  else if (state == 6) {
    if (key == 'B') {
      waitingQRResult = false; state = 5;
      showOLED("Fallback Menu","7=QR scan",
               "2=Student PIN","B=go back");
    }
  }

  else if (state == 7) {
    if (key == 'B') { showHome(); return; }
    if (rfid.PICC_IsNewCardPresent() &&
        rfid.PICC_ReadCardSerial()) {
      String uid = "";
      for (byte i=0; i<rfid.uid.size; i++) {
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