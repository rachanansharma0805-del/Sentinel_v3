// ═══════════════════════════════════════════════════════════════
// SENTINEL — MFA EXAM HALL SECURITY SYSTEM
// ESP32-CAM (AI Thinker) Firmware v1
// ═══════════════════════════════════════════════════════════════
//
// BOARD: AI Thinker ESP32-CAM  (select in Arduino IDE)
//        Tools → Board → AI Thinker ESP32-CAM
//
// UPLOAD:
//   1. Bridge GPIO 0 → GND before powering on
//   2. Flash via MB board UART (or FTDI)
//   3. Remove GPIO 0 bridge, press RESET
//
// WHAT THIS DOES:
//   • Subscribes to  exam/cam/command  via MQTT
//   • On cmd=face_verify  → captures frame → POST /api/face/verify
//                         → publishes result to exam/face/result
//   • On cmd=qr_scan      → captures frame → POST /api/qr/scan
//                         → publishes result to exam/qr/result
//   • On cmd=snapshot     → captures frame → POST /api/anomaly/snapshot
//                         → publishes ack to exam/anomaly
//
// MQTT TOPICS (mirrors WROOM subscriptions):
//   SUB: exam/cam/command
//   PUB: exam/face/result
//   PUB: exam/qr/result
//   PUB: exam/anomaly
//
// ── PINOUT (AI Thinker ESP32-CAM, fixed by hardware) ──────────
//   GPIO 0  = Boot / camera XCLK (tie LOW to flash, float for run)
//   GPIO 4  = Flash LED (active HIGH — use carefully, draws ~1A)
//   Camera  = OV2640 on dedicated CSI bus (pins handled by esp_camera)
//   No SPI/I2C/keypad — camera only
// ══════════════════════════════════════════════════════════════

#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ── UPDATE THESE TO MATCH WROOM CONFIG ──────────────────────────
const char* WIFI_SSID   = "vivo Y16";
const char* WIFI_PASS   = "standingnexttoyou";
const char* SERVER_IP   = "192.168.154.107";
const int   SERVER_PORT = 3000;
const char* MQTT_SERVER = "192.168.154.107";
const int   MQTT_PORT   = 1883;
const char* ROOM_ID     = "HALL-A";  // Must match WROOM

// ── CAMERA BEHAVIOUR ─────────────────────────────────────────────
// FRAMESIZE_VGA   (640×480) — good balance; use for face verify
// FRAMESIZE_QVGA  (320×240) — faster upload; use if face verify lags
// FRAMESIZE_SVGA  (800×600) — higher quality; use for QR if needed
#define CAM_FACE_SIZE   FRAMESIZE_VGA
#define CAM_QR_SIZE     FRAMESIZE_VGA
#define CAM_SNAP_SIZE   FRAMESIZE_QVGA  // trespass snapshot — speed > quality
#define CAM_JPEG_QUAL   12              // 0 (best) – 63 (worst). 10-15 is a good range.
#define FLASH_MS        80              // How long to fire the LED flash (ms)
#define CAPTURE_RETRIES 3               // Retry on blank frame

// ── AI THINKER ESP32-CAM CAMERA PIN MAP ─────────────────────────
// DO NOT CHANGE — these are hardware-fixed on the AI Thinker module
#define PWDN_GPIO_NUM    32
#define RESET_GPIO_NUM   -1
#define XCLK_GPIO_NUM     0
#define SIOD_GPIO_NUM    26
#define SIOC_GPIO_NUM    27
#define Y9_GPIO_NUM      35
#define Y8_GPIO_NUM      34
#define Y7_GPIO_NUM      39
#define Y6_GPIO_NUM      36
#define Y5_GPIO_NUM      21
#define Y4_GPIO_NUM      19
#define Y3_GPIO_NUM      18
#define Y2_GPIO_NUM       5
#define VSYNC_GPIO_NUM   25
#define HREF_GPIO_NUM    23
#define PCLK_GPIO_NUM    22
#define FLASH_LED_PIN     4

// ── GLOBALS ──────────────────────────────────────────────────────
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

bool camReady = false;

// ══════════════════════════════════════════════════════════════
// CAMERA INIT
// ══════════════════════════════════════════════════════════════
bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // Prefer larger frame buffers when PSRAM is present
  if (psramFound()) {
    config.frame_size   = FRAMESIZE_VGA;
    config.jpeg_quality = CAM_JPEG_QUAL;
    config.fb_count     = 2;  // double buffer — smoother capture
    Serial.println("[CAM] PSRAM found — using double buffer VGA");
  } else {
    // Without PSRAM, keep it small or we run out of heap
    config.frame_size   = FRAMESIZE_QVGA;
    config.jpeg_quality = 15;
    config.fb_count     = 1;
    Serial.println("[CAM] No PSRAM — using QVGA single buffer");
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[CAM] Init FAILED: 0x%x\n", err);
    return false;
  }

  // Sensor tuning — OV2640 specific
  sensor_t* s = esp_camera_sensor_get();
  s->set_brightness(s, 1);     // slight brightness boost (-2..2)
  s->set_contrast(s, 0);
  s->set_saturation(s, 0);
  s->set_whitebal(s, 1);       // auto white balance on
  s->set_awb_gain(s, 1);       // AWB gain on
  s->set_exposure_ctrl(s, 1);  // auto exposure on
  s->set_aec2(s, 1);           // AEC DSP on
  s->set_gain_ctrl(s, 1);      // auto gain on
  s->set_hmirror(s, 0);        // flip if your camera is inverted
  s->set_vflip(s, 0);

  Serial.println("[CAM] Initialised OK");
  return true;
}

// ══════════════════════════════════════════════════════════════
// CHANGE RESOLUTION — call before a specific capture type
// ══════════════════════════════════════════════════════════════
void setResolution(framesize_t size) {
  sensor_t* s = esp_camera_sensor_get();
  if (s) s->set_framesize(s, size);
  // Drain 2 stale frames after resolution change
  for (int i = 0; i < 2; i++) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
    delay(50);
  }
}

// ══════════════════════════════════════════════════════════════
// CAPTURE — with retries and optional flash
// ══════════════════════════════════════════════════════════════
camera_fb_t* captureFrame(bool useFlash = false) {
  if (useFlash) {
    digitalWrite(FLASH_LED_PIN, HIGH);
    delay(FLASH_MS);  // Let auto-exposure settle briefly
  }

  camera_fb_t* fb = nullptr;
  for (int attempt = 0; attempt < CAPTURE_RETRIES; attempt++) {
    fb = esp_camera_fb_get();
    if (fb && fb->len > 0) break;
    if (fb) esp_camera_fb_return(fb);
    fb = nullptr;
    Serial.printf("[CAM] Blank frame attempt %d/%d\n",
                  attempt + 1, CAPTURE_RETRIES);
    delay(100);
  }

  if (useFlash) {
    digitalWrite(FLASH_LED_PIN, LOW);
  }

  if (!fb) {
    Serial.println("[CAM] Capture FAILED after retries");
  } else {
    Serial.printf("[CAM] Captured %d bytes\n", fb->len);
  }
  return fb;
}

// ══════════════════════════════════════════════════════════════
// HTTP MULTIPART POST — sends frame as "image" field
// ══════════════════════════════════════════════════════════════
String httpPostImage(const char* endpoint,
                     camera_fb_t* fb,
                     const char* metaJson = "{}") {
  if (WiFi.status() != WL_CONNECTED) {
    return "{\"error\":\"wifi_disconnected\"}";
  }

  HTTPClient http;
  String url = "http://" + String(SERVER_IP) +
               ":" + String(SERVER_PORT) + endpoint;
  http.begin(url);
  http.setTimeout(12000);  // 12s — face recognition can be slow

  // Build multipart/form-data manually
  // Boundary must not appear in the JPEG data
  String boundary = "SentinelBoundary7734";
  String contentType = "multipart/form-data; boundary=" + boundary;
  http.addHeader("Content-Type", contentType);

  // --boundary\r\n
  // Content-Disposition: form-data; name="meta"\r\n\r\n
  // <metaJson>\r\n
  // --boundary\r\n
  // Content-Disposition: form-data; name="image"; filename="cam.jpg"\r\n
  // Content-Type: image/jpeg\r\n\r\n
  // <jpeg bytes>
  // \r\n--boundary--\r\n

  String partMeta =
    "--" + boundary + "\r\n"
    "Content-Disposition: form-data; name=\"meta\"\r\n\r\n" +
    String(metaJson) + "\r\n";

  String partImageHeader =
    "--" + boundary + "\r\n"
    "Content-Disposition: form-data; name=\"image\"; filename=\"cam.jpg\"\r\n"
    "Content-Type: image/jpeg\r\n\r\n";

  String partEnd = "\r\n--" + boundary + "--\r\n";

  // Combine into a single buffer
  // Total size = partMeta + partImageHeader + fb->len + partEnd
  size_t totalLen = partMeta.length()
                  + partImageHeader.length()
                  + fb->len
                  + partEnd.length();

  uint8_t* body = (uint8_t*)malloc(totalLen);
  if (!body) {
    Serial.println("[HTTP] malloc failed for multipart body");
    return "{\"error\":\"oom\"}";
  }

  size_t pos = 0;
  memcpy(body + pos, partMeta.c_str(),        partMeta.length());
  pos += partMeta.length();
  memcpy(body + pos, partImageHeader.c_str(), partImageHeader.length());
  pos += partImageHeader.length();
  memcpy(body + pos, fb->buf,                 fb->len);
  pos += fb->len;
  memcpy(body + pos, partEnd.c_str(),         partEnd.length());

  int code = http.POST(body, totalLen);
  free(body);

  String resp = (code > 0)
    ? http.getString()
    : "{\"error\":\"http_" + String(code) + "\"}";
  http.end();

  Serial.printf("[HTTP] POST %s -> %d\n", endpoint, code);
  return resp;
}

// ══════════════════════════════════════════════════════════════
// HTTP RAW POST — for trespass snapshot (raw bytes, no multipart)
// ══════════════════════════════════════════════════════════════
bool httpPostRaw(const char* endpoint,
                 camera_fb_t* fb,
                 const char* reason,
                 const char* room) {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  String url = "http://" + String(SERVER_IP) +
               ":" + String(SERVER_PORT) + endpoint;
  http.begin(url);
  http.setTimeout(8000);
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("X-Reason", reason);
  http.addHeader("X-Room",   room);

  int code = http.POST(fb->buf, fb->len);
  http.end();
  Serial.printf("[HTTP] Snapshot POST -> %d\n", code);
  return (code == 200);
}

// ══════════════════════════════════════════════════════════════
// MQTT PUBLISH HELPER
// ══════════════════════════════════════════════════════════════
void publish(const char* topic, const char* payload) {
  if (mqtt.connected()) {
    mqtt.publish(topic, payload);
    Serial.printf("[MQTT] PUB %s -> %s\n", topic, payload);
  } else {
    Serial.println("[MQTT] Cannot publish — not connected");
  }
}

// ══════════════════════════════════════════════════════════════
// COMMAND HANDLERS
// ══════════════════════════════════════════════════════════════

// ── cmd = face_verify ────────────────────────────────────────
//  WROOM already validated the room via /student/rfid-check
//  CAM captures, POSTs to /api/face/verify, forwards result
void handleFaceVerify(const String& studentId) {
  Serial.println("[CMD] face_verify for: " + studentId);

  setResolution(CAM_FACE_SIZE);
  delay(300);  // Let OV2640 auto-exposure settle at new resolution

  camera_fb_t* fb = captureFrame(false);  // no flash for face — harsh
  if (!fb) {
    publish("exam/face/result",
      "{\"match\":false,\"confidence\":999,"
      "\"reason\":\"capture_failed\"}");
    return;
  }

  // Build meta JSON
  StaticJsonDocument<128> meta;
  meta["room_id"]     = ROOM_ID;
  meta["student_id"]  = studentId;
  meta["auth_method"] = "rfid+face";
  String metaStr;
  serializeJson(meta, metaStr);

  String resp = httpPostImage("/api/face/verify", fb, metaStr.c_str());
  esp_camera_fb_return(fb);

  // Parse server response and relay to WROOM
  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, resp);
  if (err) {
    Serial.println("[FACE] JSON parse error: " + String(err.c_str()));
    publish("exam/face/result",
      "{\"match\":false,\"confidence\":999,"
      "\"reason\":\"server_parse_error\"}");
    return;
  }

  // Relay the full server response as-is
  // server.py /api/face/verify returns:
  //   match, confidence, student_id, student_name,
  //   seat_no, assigned_room, correct_room, message
  publish("exam/face/result", resp.c_str());
}

// ── cmd = qr_scan ─────────────────────────────────────────────
//  CAM captures, POSTs to /api/qr/scan, forwards result
void handleQRScan(const String& studentId) {
  Serial.println("[CMD] qr_scan for: " + studentId);

  setResolution(CAM_QR_SIZE);
  delay(200);

  // Use flash for QR — printed QR codes need good contrast
  camera_fb_t* fb = captureFrame(true);
  if (!fb) {
    publish("exam/qr/result",
      "{\"valid\":false,\"reason\":\"capture_failed\"}");
    return;
  }

  StaticJsonDocument<128> meta;
  meta["room_id"]    = ROOM_ID;
  meta["student_id"] = studentId;
  String metaStr;
  serializeJson(meta, metaStr);

  String resp = httpPostImage("/api/qr/scan", fb, metaStr.c_str());
  esp_camera_fb_return(fb);

  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, resp);
  if (err) {
    publish("exam/qr/result",
      "{\"valid\":false,\"reason\":\"server_parse_error\"}");
    return;
  }

  // Relay server response:
  //   valid, expired, student_id, reason
  publish("exam/qr/result", resp.c_str());
}

// ── cmd = snapshot ────────────────────────────────────────────
//  Trespass capture — speed priority, raw POST
void handleSnapshot(const String& reason) {
  Serial.println("[CMD] snapshot reason: " + reason);

  setResolution(CAM_SNAP_SIZE);
  delay(100);

  // Use flash — likely night or low-light doorway condition
  camera_fb_t* fb = captureFrame(true);
  if (!fb) {
    publish("exam/anomaly",
      "{\"saved\":false,\"reason\":\"capture_failed\"}");
    return;
  }

  bool saved = httpPostRaw(
    "/api/anomaly/snapshot", fb,
    reason.c_str(), ROOM_ID
  );
  esp_camera_fb_return(fb);

  StaticJsonDocument<128> ack;
  ack["saved"]   = saved;
  ack["room_id"] = ROOM_ID;
  ack["reason"]  = reason;
  String ackStr;
  serializeJson(ack, ackStr);
  publish("exam/anomaly", ackStr.c_str());
}

// ══════════════════════════════════════════════════════════════
// MQTT CALLBACK — receives commands from WROOM
// ══════════════════════════════════════════════════════════════
void mqttCallback(char* topic, byte* payload, unsigned int len) {
  String msg = "";
  for (unsigned int i = 0; i < len; i++) msg += (char)payload[i];
  Serial.println("[MQTT] [" + String(topic) + "] " + msg);

  if (String(topic) != "exam/cam/command") return;

  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, msg)) {
    Serial.println("[MQTT] Bad JSON in command");
    return;
  }

  String cmd        = doc["cmd"]        | "";
  String studentId  = doc["student_id"] | "";
  String snapReason = doc["reason"]     | "trespass";

  if (cmd == "face_verify") {
    handleFaceVerify(studentId);
  } else if (cmd == "qr_scan") {
    handleQRScan(studentId);
  } else if (cmd == "snapshot") {
    handleSnapshot(snapReason);
  } else {
    Serial.println("[MQTT] Unknown cmd: " + cmd);
  }
}

// ══════════════════════════════════════════════════════════════
// WIFI
// ══════════════════════════════════════════════════════════════
void connectWiFi() {
  Serial.print("[WiFi] Connecting to " + String(WIFI_SSID));
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 40) {
    delay(500);
    Serial.print(".");
    tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WiFi] Connected: " + WiFi.localIP().toString());
  } else {
    Serial.println("\n[WiFi] FAILED — restarting in 3s");
    delay(3000);
    ESP.restart();
  }
}

// ══════════════════════════════════════════════════════════════
// MQTT CONNECT
// ══════════════════════════════════════════════════════════════
void reconnectMQTT() {
  if (mqtt.connected()) return;
  Serial.print("[MQTT] Connecting...");
  // Client ID must differ from WROOM — use mac suffix
  String cid = "esp32cam_" + String(ROOM_ID) + "_" +
               String((uint32_t)ESP.getEfuseMac(), HEX);
  if (mqtt.connect(cid.c_str())) {
    Serial.println("OK");
    mqtt.subscribe("exam/cam/command");
    Serial.println("[MQTT] Subscribed: exam/cam/command");

    // Announce readiness to the broker (useful for debugging)
    StaticJsonDocument<128> hello;
    hello["event"]   = "cam_online";
    hello["room_id"] = ROOM_ID;
    hello["ip"]      = WiFi.localIP().toString();
    String helloStr;
    serializeJson(hello, helloStr);
    mqtt.publish("exam/status", helloStr.c_str());
  } else {
    Serial.println("FAIL rc=" + String(mqtt.state()));
  }
}

// ══════════════════════════════════════════════════════════════
// SETUP
// ══════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n[BOOT] Sentinel ESP32-CAM v1");
  Serial.println("[BOOT] Room: " + String(ROOM_ID));

  // Flash LED pin
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);

  // Brief flash to confirm power-on
  digitalWrite(FLASH_LED_PIN, HIGH);
  delay(100);
  digitalWrite(FLASH_LED_PIN, LOW);

  // Init camera
  camReady = initCamera();
  if (!camReady) {
    Serial.println("[BOOT] Camera failed — will retry on first command");
  }

  // WiFi
  connectWiFi();

  // MQTT
  mqtt.setServer(MQTT_SERVER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);
  reconnectMQTT();

  Serial.println("[BOOT] Ready. Waiting for MQTT commands...");
  Serial.println("[BOOT] Subscribed to: exam/cam/command");
  Serial.println("[BOOT] Publishing to:  exam/face/result");
  Serial.println("[BOOT]                 exam/qr/result");
  Serial.println("[BOOT]                 exam/anomaly");
}

// ══════════════════════════════════════════════════════════════
// LOOP
// ══════════════════════════════════════════════════════════════
void loop() {
  // MQTT keepalive
  if (!mqtt.connected()) {
    reconnectMQTT();
  }
  mqtt.loop();

  // WiFi watchdog
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Dropped — reconnecting...");
    connectWiFi();
  }

  // Camera lazy-init retry — in case it failed on boot
  if (!camReady) {
    static unsigned long lastRetry = 0;
    if (millis() - lastRetry > 10000) {
      lastRetry = millis();
      Serial.println("[CAM] Retrying camera init...");
      camReady = initCamera();
    }
  }

  delay(10);
}
