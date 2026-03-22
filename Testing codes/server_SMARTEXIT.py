"""
MFA EXAM HALL SECURITY SYSTEM
Flask Backend Server — FINAL
=============================
All fixes included:
- is_valid_pin fixed (no nested function)
- FACE_THRESHOLD = 160 for QVGA CAM
- train_faces() loads face_model.yml first (216-photo model)
- verify_face() strict — rejects if no face detected in frame
- QR scanning: pyzbar (primary) + 7 OpenCV strategies
- QR generation: ERROR_CORRECT_M, box_size=10 (simpler, bigger)
- ensure_today_sessions() on every startup
- Full logging with face confidence and QR strategy used
"""

import os, io, json, hashlib, logging
import sqlite3, threading, time
from datetime import date, datetime

import numpy as np
import cv2
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import paho.mqtt.client as mqtt
import requests as req_lib

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("exam_system.log")
    ]
)
log = logging.getLogger("exam_system")

app = Flask(__name__)
CORS(app)

# ─── CONFIG ───────────────────────────────────────────────────
SERVER_IP       = "192.168.154.107"
SERVER_PORT     = 3000
DB_PATH         = "exam.db"
FACES_DIR       = "faces"
SNAPSHOTS_DIR   = "alarm_photos"
QR_DIR          = "qr_codes"
MQTT_BROKER     = "localhost"
MQTT_PORT       = 1883
FAST2SMS_KEY    = "YwsOCXEKJ6HWc9fibeUMz4u7LlPQovd8NymSGa5AgRxpnZh3DTAeU4NrC65GaYMTXxdLH3pKZF2sJkzS"
QR_SECRET       = "examhall_qr_secret_2026!!"
PIN_SECRET      = "examhall_pin_secret_2026!"
FACE_THRESHOLD  = 160   # raised for QVGA CAM compatibility
EXAM_START_DATE = "2026-03-16"
EXAM_END_DATE   = "2026-04-30"
SAFE_PIN_DIGITS = [1, 2, 4, 5, 7, 8]

os.makedirs(FACES_DIR,     exist_ok=True)
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
os.makedirs(QR_DIR,        exist_ok=True)

# ══════════════════════════════════════════════════════════════
# DATABASE SCHEMA
# ══════════════════════════════════════════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no        TEXT UNIQUE NOT NULL,
    name           TEXT NOT NULL,
    phone          TEXT,
    rfid_uid       TEXT UNIQUE,
    face_vector    BLOB,
    assigned_room  TEXT,
    seat_no        TEXT,
    enrolled_at    TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS faculty (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    faculty_id  TEXT UNIQUE NOT NULL,
    phone       TEXT NOT NULL,
    email       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS exam_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         TEXT NOT NULL,
    subject         TEXT,
    exam_date       TEXT NOT NULL DEFAULT (date('now')),
    faculty_id      INTEGER REFERENCES faculty(id),
    faculty_pin     TEXT NOT NULL,
    student_pin     TEXT,
    is_active       INTEGER DEFAULT 0,
    activated_at    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(room_id, exam_date)
);
CREATE TABLE IF NOT EXISTS access_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         TEXT NOT NULL,
    student_id      INTEGER REFERENCES students(id),
    faculty_id      INTEGER REFERENCES faculty(id),
    exam_date       TEXT DEFAULT (date('now')),
    event_type      TEXT NOT NULL,
    auth_method     TEXT,
    auth_status     TEXT NOT NULL,
    deny_reason     TEXT,
    fallbacks_used  INTEGER DEFAULT 0,
    entry_time      TEXT,
    exit_time       TEXT,
    photo_path      TEXT,
    rfid_uid_raw    TEXT,
    logged_at       TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS anomaly_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    description     TEXT,
    photo_path      TEXT,
    notified_admin  INTEGER DEFAULT 0,
    exam_date       TEXT DEFAULT (date('now')),
    occurred_at     TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS qr_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER REFERENCES students(id),
    qr_hash         TEXT UNIQUE NOT NULL,
    exam_start      TEXT NOT NULL DEFAULT '2026-03-16',
    exam_end        TEXT NOT NULL DEFAULT '2026-04-30',
    room_id         TEXT NOT NULL,
    last_used_date  TEXT,
    use_count       INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS admin_users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO admin_users (username, password)
    VALUES ('admin', 'exam@2026');
INSERT OR IGNORE INTO faculty (name, faculty_id, phone)
    VALUES ('Dr. S. Rajan', 'FAC001', '6361403469');
INSERT OR IGNORE INTO faculty (name, faculty_id, phone)
    VALUES ('Prof. K. Menon', 'FAC002', '9876543211');
INSERT OR IGNORE INTO students (roll_no, name, phone, assigned_room, seat_no)
    VALUES ('22BCS001', 'Rahul Sharma',  '9876500001', 'HALL-A', '1');
INSERT OR IGNORE INTO students (roll_no, name, phone, assigned_room, seat_no)
    VALUES ('22BCS002', 'Priya Reddy',   '9876500002', 'HALL-A', '2');
INSERT OR IGNORE INTO students (roll_no, name, phone, assigned_room, seat_no)
    VALUES ('22BCS003', 'Arjun Kumar',   '9876500003', 'HALL-A', '3');
INSERT OR IGNORE INTO students (roll_no, name, phone, assigned_room, seat_no)
    VALUES ('22BCS004', 'Sneha Patel',   '9876500004', 'HALL-B', '1');
INSERT OR IGNORE INTO students (roll_no, name, phone, assigned_room, seat_no)
    VALUES ('22BCS005', 'Vikram Singh',  '9876500005', 'HALL-B', '2');
"""

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    # Add columns if upgrading from old DB
    for col, default in [
        ("exam_start", "'2026-03-16'"),
        ("exam_end",   "'2026-04-30'")
    ]:
        try:
            conn.execute(f"ALTER TABLE qr_tokens ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
            conn.commit()
            log.info(f"Added column qr_tokens.{col}")
        except:
            pass
    conn.close()
    log.info("Database initialised!")

# ══════════════════════════════════════════════════════════════
# PIN HELPERS
# ══════════════════════════════════════════════════════════════
def is_valid_pin(pin: str) -> bool:
    """Only digits 1,2,4,5,7,8 allowed — no 3,6,9"""
    return all(c in "124578" for c in pin) and len(pin) >= 4

def validate_pin_digits(pin: str) -> tuple:
    if len(pin) < 4:
        return False, "PIN must be at least 4 digits"
    if len(pin) > 8:
        return False, "PIN must be at most 8 digits"
    forbidden = [c for c in pin if c in "369"]
    if forbidden:
        return False, "PIN cannot contain 3, 6, or 9"
    return True, "ok"

def generate_student_pin(session_id: int) -> str:
    raw      = f"{session_id}:{PIN_SECRET}:{date.today().isoformat()}"
    hash_val = int(hashlib.sha256(raw.encode()).hexdigest(), 16)
    pin, temp = "", hash_val
    for _ in range(6):
        pin += str(SAFE_PIN_DIGITS[temp % len(SAFE_PIN_DIGITS)])
        temp //= 10
    return pin

# ══════════════════════════════════════════════════════════════
# FACE RECOGNITION
# ══════════════════════════════════════════════════════════════
recognizer     = cv2.face.LBPHFaceRecognizer_create()
face_cascade   = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
face_labels    = {}
face_label_rev = {}
model_trained  = False

def train_faces():
    """
    Loads face_model.yml if it exists (pre-trained 216-photo model).
    Falls back to training from scratch if yml not found.
    """
    global model_trained, face_labels, face_label_rev

    # Load pre-saved model from capture_faces_v2.py
    if os.path.exists("face_model.yml"):
        try:
            recognizer.read("face_model.yml")
            label_id = 0
            for roll_no in sorted(os.listdir(FACES_DIR)):
                folder = os.path.join(FACES_DIR, roll_no)
                if not os.path.isdir(folder): continue
                face_labels[label_id]   = roll_no
                face_label_rev[roll_no] = label_id
                label_id += 1
            model_trained = True
            log.info(f"Face model loaded from face_model.yml "
                     f"— {label_id} students")
            return
        except Exception as e:
            log.warning(f"Could not load face_model.yml: {e} "
                        f"— retraining from scratch")

    # Train from scratch
    faces, labels = [], []
    label_id = 0
    if not os.path.exists(FACES_DIR):
        return
    for roll_no in sorted(os.listdir(FACES_DIR)):
        folder = os.path.join(FACES_DIR, roll_no)
        if not os.path.isdir(folder): continue
        face_labels[label_id]   = roll_no
        face_label_rev[roll_no] = label_id
        for f in os.listdir(folder):
            if not f.lower().endswith((".jpg",".jpeg",".png")):
                continue
            img = cv2.imread(
                os.path.join(folder, f), cv2.IMREAD_GRAYSCALE)
            if img is None: continue
            faces.append(cv2.resize(img, (200, 200)))
            labels.append(label_id)
        label_id += 1
    if faces:
        recognizer.train(faces, np.array(labels))
        model_trained = True
        log.info(f"Face model trained: {len(faces)} images, "
                 f"{label_id} students")
    else:
        log.warning("No face images found!")

def verify_face(img_bytes):
    """
    STRICT face detection:
    - Rejects frame if no face found (no false grants)
    - Uses largest detected face
    - Preprocessing matches capture_faces_v2.py
    """
    try:
        nparr = np.frombuffer(img_bytes, np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            log.error("verify_face: could not decode image")
            return False, "", 999.0

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Try standard detection first
        detected = face_cascade.detectMultiScale(
            gray, scaleFactor=1.1,
            minNeighbors=4, minSize=(40, 40))

        # Try more lenient if nothing found (QVGA small faces)
        if len(detected) == 0:
            detected = face_cascade.detectMultiScale(
                gray, scaleFactor=1.05,
                minNeighbors=3, minSize=(30, 30))

        if len(detected) == 0:
            # STRICT: reject — no face in frame
            log.warning("verify_face: NO FACE DETECTED — rejecting")
            return False, "", 999.0

        # Use largest face
        x, y, w, h = max(detected, key=lambda r: r[2] * r[3])
        roi = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
        log.info(f"Face detected: {w}x{h} at ({x},{y})")

        # Match preprocessing from capture_faces_v2.py
        roi = cv2.equalizeHist(roi)
        roi = cv2.GaussianBlur(roi, (3, 3), 0)

        if model_trained:
            label, conf = recognizer.predict(roi)
            roll  = face_labels.get(label, "")
            match = conf < FACE_THRESHOLD
            log.info(f"Face: label={label} roll={roll} "
                     f"conf={conf:.1f} "
                     f"{'MATCH' if match else 'NO MATCH'}")
            return match, roll, float(conf)

        log.warning("verify_face: model not trained!")
        return False, "", 999.0

    except Exception as e:
        log.error(f"Face verify error: {e}")
        return False, "", 999.0

# ══════════════════════════════════════════════════════════════
# QR CODE
# ══════════════════════════════════════════════════════════════
def make_qr_hash(roll_no, room_id):
    payload = (f"UUCMS:{roll_no}:{room_id}:"
               f"{EXAM_START_DATE}:{EXAM_END_DATE}")
    qr_hash = hashlib.sha256(
        f"{payload}:{QR_SECRET}".encode()).hexdigest()
    return payload, qr_hash

def verify_qr(qr_data, room_id):
    try:
        parts = qr_data.split(":")
        if len(parts) < 6:
            return False, "", False, "invalid_format"

        prefix, roll_no, qr_room = parts[0], parts[1], parts[2]
        exam_start, exam_end, qr_hash = parts[3], parts[4], parts[5]

        if prefix != "UUCMS":
            return False, "", False, "invalid_prefix"

        # Tamper check
        payload  = (f"UUCMS:{roll_no}:{qr_room}:"
                    f"{exam_start}:{exam_end}")
        expected = hashlib.sha256(
            f"{payload}:{QR_SECRET}".encode()).hexdigest()
        if qr_hash != expected:
            return False, "", False, "tampered"

        if qr_room != room_id:
            return False, roll_no, False, "wrong_room"

        today = date.today()
        try:
            start_dt = date.fromisoformat(exam_start)
            end_dt   = date.fromisoformat(exam_end)
        except:
            return False, roll_no, False, "invalid_dates"

        if today < start_dt:
            return False, roll_no, False, "exam_not_started"
        if today > end_dt:
            return False, roll_no, True,  "exam_series_ended"

        today_str = today.isoformat()
        conn = get_db()
        row  = conn.execute(
            "SELECT last_used_date FROM qr_tokens "
            "WHERE qr_hash=?", (qr_hash,)).fetchone()
        conn.close()

        if row and row["last_used_date"] == today_str:
            return False, roll_no, False, "already_used_today"

        return True, roll_no, False, "ok"

    except Exception as e:
        log.error(f"QR verify error: {e}")
        return False, "", False, str(e)

# ══════════════════════════════════════════════════════════════
# SMS
# ══════════════════════════════════════════════════════════════
def send_sms(phone: str, message: str):
    try:
        phone = phone.replace("+91", "").replace(" ", "").strip()
        if len(phone) != 10:
            log.warning(f"Invalid phone: {phone}")
            return False
        r = req_lib.post(
            "https://www.fast2sms.com/dev/bulkV2",
            json={"route": "q", "message": message,
                  "language": "english", "flash": 0,
                  "numbers": phone},
            headers={"authorization": FAST2SMS_KEY,
                     "Content-Type": "application/json"},
            timeout=8)
        result = r.json()
        if result.get("return"):
            log.info(f"SMS sent to {phone}")
            return True
        log.error(f"SMS failed: {result}")
        return False
    except Exception as e:
        log.error(f"SMS error: {e}")
        return False

# ══════════════════════════════════════════════════════════════
# MQTT
# ══════════════════════════════════════════════════════════════
mqtt_client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

def on_connect(client, userdata, flags, rc, props=None):
    log.info(f"MQTT connected rc={rc}")
    client.subscribe("exam/cam/command")
    client.subscribe("exam/face/result")
    client.subscribe("exam/qr/result")
    client.subscribe("exam/anomaly")

def on_message(client, userdata, msg):
    """
    Handles exam/cam/command messages.
    face_verify and snapshot → forwarded to ESP32-CAM.
    qr_scan → handled by laptop webcam directly.
    """
    try:
        data  = json.loads(msg.payload.decode())
        topic = msg.topic
        log.info(f"MQTT [{topic}]: {data}")

        if topic == "exam/cam/command":
            cmd        = data.get("cmd", "")
            student_id = data.get("student_id", "")
            room_id    = data.get("room_id", "HALL-A")

            if cmd == "qr_scan":
                # Launch webcam QR scanner in background thread
                log.info(f"[WEBCAM QR] Triggered for "
                         f"{student_id} room={room_id}")
                t = threading.Thread(
                    target=webcam_qr_scan,
                    args=(room_id, student_id),
                    daemon=True)
                t.start()

    except Exception as e:
        log.error(f"MQTT on_message error: {e}")

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

def start_mqtt():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT)
        mqtt_client.loop_forever()
    except Exception as e:
        log.error(f"MQTT error: {e}")

def publish(topic, payload):
    try:
        mqtt_client.publish(topic, json.dumps(payload))
    except:
        pass

# ══════════════════════════════════════════════════════════════
# WEBCAM QR SCANNER
# Triggered by MQTT exam/cam/command {cmd: qr_scan}
# Opens laptop webcam, scans until QR found or 20s timeout
# Publishes result to exam/qr/result
# ══════════════════════════════════════════════════════════════
webcam_qr_active = False
webcam_qr_lock   = threading.Lock()

def decode_qr_from_frame(frame):
    """Try all strategies to decode QR from a webcam frame."""
    qr_data = None
    det     = cv2.QRCodeDetector()

    # pyzbar first (best decoder)
    try:
        from pyzbar import pyzbar
        barcodes = pyzbar.decode(frame)
        if barcodes:
            return barcodes[0].data.decode('utf-8')
        gray     = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        barcodes = pyzbar.decode(gray)
        if barcodes:
            return barcodes[0].data.decode('utf-8')
    except:
        pass

    # OpenCV strategies
    qr_data, _, _ = det.detectAndDecode(frame)
    if qr_data: return qr_data

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    qr_data, _, _ = det.detectAndDecode(gray)
    if qr_data: return qr_data

    _, thresh = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    qr_data, _, _ = det.detectAndDecode(thresh)
    if qr_data: return qr_data

    kernel = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
    sharp  = cv2.filter2D(gray, -1, kernel)
    qr_data, _, _ = det.detectAndDecode(sharp)
    if qr_data: return qr_data

    up = cv2.resize(frame, None, fx=2, fy=2,
                    interpolation=cv2.INTER_CUBIC)
    qr_data, _, _ = det.detectAndDecode(up)
    if qr_data: return qr_data

    return None

def webcam_qr_scan(room_id, student_id):
    """
    Opens laptop webcam, shows live feed with overlay.
    Student holds QR to webcam.
    Scans for up to 20 seconds then times out.
    Publishes result to exam/qr/result via MQTT.
    """
    global webcam_qr_active
    with webcam_qr_lock:
        if webcam_qr_active:
            log.warning("[WEBCAM QR] Already scanning!")
            return
        webcam_qr_active = True

    log.info(f"[WEBCAM QR] Starting scan for {student_id} "
             f"room={room_id}")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        log.error("[WEBCAM QR] Cannot open webcam!")
        publish("exam/qr/result", {
            "valid": False, "reason": "webcam_unavailable"})
        webcam_qr_active = False
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    start_time = time.time()
    timeout    = 20  # seconds
    qr_data    = None
    found      = False

    while time.time() - start_time < timeout:
        ret, frame = cap.read()
        if not ret:
            break

        # Try to decode QR
        qr_data = decode_qr_from_frame(frame)

        # Draw overlay
        elapsed   = int(time.time() - start_time)
        remaining = timeout - elapsed
        display   = frame.copy()

        # Draw guide box in center
        h, w    = display.shape[:2]
        cx, cy  = w // 2, h // 2
        box_sz  = 220
        cv2.rectangle(display,
                      (cx - box_sz, cy - box_sz),
                      (cx + box_sz, cy + box_sz),
                      (0, 255, 0) if not qr_data else (0, 255, 0),
                      2)

        # Status text
        cv2.putText(display,
                    f"Hold QR inside the box",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 255), 2)
        cv2.putText(display,
                    f"Student: {student_id}",
                    (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (200, 200, 200), 1)
        cv2.putText(display,
                    f"Time left: {remaining}s",
                    (10, 90),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 200, 255), 2)

        if qr_data:
            cv2.putText(display,
                        "QR DETECTED!",
                        (cx - 80, cy - box_sz - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, (0, 255, 0), 2)
            found = True

        cv2.imshow("SENTINEL — QR Scanner (press Q to cancel)",
                   display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == 27:
            log.info("[WEBCAM QR] Cancelled by user")
            break

        if found:
            # Flash green for 1 second to confirm
            for _ in range(10):
                ret2, f2 = cap.read()
                if ret2:
                    cv2.putText(f2, "QR ACCEPTED! Processing...",
                                (50, h // 2),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                1.0, (0, 255, 0), 3)
                    cv2.imshow(
                        "SENTINEL — QR Scanner (press Q to cancel)",
                        f2)
                    cv2.waitKey(100)
            break

    cap.release()
    cv2.destroyAllWindows()

    if not qr_data:
        log.warning("[WEBCAM QR] No QR detected — timeout")
        publish("exam/qr/result", {
            "valid":  False,
            "reason": "no_qr_detected"
        })
        webcam_qr_active = False
        return

    # Verify the decoded QR
    log.info(f"[WEBCAM QR] Decoded: {qr_data[:60]}")
    valid, roll_no, expired, reason = verify_qr(qr_data, room_id)
    log.info(f"[WEBCAM QR] valid={valid} roll={roll_no} "
             f"reason={reason}")

    if valid:
        # Mark QR as used today
        qr_hash   = qr_data.split(":")[-1]
        today_str = date.today().isoformat()
        conn      = get_db()
        conn.execute("""
            UPDATE qr_tokens
            SET last_used_date=?, use_count=use_count+1
            WHERE qr_hash=?
        """, (today_str, qr_hash))
        conn.commit()

        # Log access
        student = conn.execute(
            "SELECT * FROM students WHERE roll_no=?",
            (roll_no,)).fetchone()
        conn.close()
        if student:
            log_access(room_id,
                       student_id=student["id"],
                       event_type="entry",
                       method="qr_webcam",
                       status="SUCCESS",
                       fallbacks=1)
        log.info(f"[WEBCAM QR] Entry granted: {roll_no}")

    publish("exam/qr/result", {
        "valid":      valid,
        "expired":    expired,
        "student_id": roll_no,
        "reason":     reason
    })
    webcam_qr_active = False

# ══════════════════════════════════════════════════════════════
# LOG HELPERS
# ══════════════════════════════════════════════════════════════
def log_access(room_id, student_id=None, faculty_id=None,
               event_type="entry", method="rfid+face",
               status="SUCCESS", deny_reason=None,
               fallbacks=0, rfid_uid=None, photo_path=None):
    conn = get_db()
    conn.execute("""
        INSERT INTO access_logs
        (room_id,student_id,faculty_id,event_type,
         auth_method,auth_status,deny_reason,
         fallbacks_used,entry_time,rfid_uid_raw,photo_path)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (room_id, student_id, faculty_id, event_type,
          method, status, deny_reason, fallbacks,
          datetime.now().isoformat(), rfid_uid, photo_path))
    conn.commit()
    conn.close()

def log_anomaly(room_id, event_type, description,
                photo_path=None):
    conn = get_db()
    conn.execute("""
        INSERT INTO anomaly_events
        (room_id,event_type,description,photo_path)
        VALUES (?,?,?,?)
    """, (room_id, event_type, description, photo_path))
    conn.commit()
    conn.close()
    publish("exam/anomaly/alert", {
        "room_id":     room_id,
        "event_type":  event_type,
        "description": description,
        "timestamp":   datetime.now().isoformat()
    })

# ══════════════════════════════════════════════════════════════
# API ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":     "ok",
        "mqtt":       mqtt_client.is_connected(),
        "face_model": model_trained,
        "threshold":  FACE_THRESHOLD,
        "time":       datetime.now().isoformat()
    })

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    try:
        data = request.json
        conn = get_db()
        row  = conn.execute(
            "SELECT * FROM admin_users "
            "WHERE username=? AND password=?",
            (data.get("username",""),
             data.get("password",""))).fetchone()
        conn.close()
        if row:
            return jsonify({"success": True,
                            "token": "admin_authenticated",
                            "username": data["username"]})
        return jsonify({"success": False,
                        "error": "Invalid credentials"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/admin/validate-pin", methods=["POST"])
def validate_pin_route():
    try:
        pin   = request.json.get("pin", "")
        valid, reason = validate_pin_digits(pin)
        return jsonify({"valid": valid, "reason": reason,
                        "safe_digits": "1,2,4,5,7,8"})
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)})

@app.route("/api/admin/set-faculty-pin", methods=["POST"])
def set_faculty_pin():
    try:
        data    = request.json
        room_id = data.get("room_id", "")
        pin     = data.get("pin", "")
        today   = date.today().isoformat()
        valid, reason = validate_pin_digits(pin)
        if not valid:
            return jsonify({"success": False, "reason": reason})
        conn = get_db()
        conn.execute(
            "UPDATE exam_sessions SET faculty_pin=? "
            "WHERE room_id=? AND exam_date=?",
            (pin, room_id, today))
        conn.commit()
        conn.close()
        return jsonify({"success": True,
                        "room_id": room_id, "pin": pin})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/student/rfid-check", methods=["POST"])
def rfid_check():
    try:
        data     = request.json
        rfid_uid = data.get("rfid_uid", "").upper().strip()
        room_id  = data.get("room_id", "")
        if not rfid_uid:
            return jsonify({"found": False, "reason": "no_rfid"})
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        conn.close()
        if not student:
            log.warning(f"[RFID] Unknown: {rfid_uid}")
            return jsonify({"found": False,
                            "reason": "not_registered",
                            "rfid_uid": rfid_uid})
        correct_room = student["assigned_room"] == room_id
        if not correct_room:
            log_access(room_id, student_id=student["id"],
                       event_type="entry", method="rfid",
                       status="DENY", deny_reason="wrong_room",
                       rfid_uid=rfid_uid)
        return jsonify({
            "found":         True,
            "correct_room":  correct_room,
            "student_name":  student["name"],
            "roll_no":       student["roll_no"],
            "assigned_room": student["assigned_room"],
            "seat_no":       student["seat_no"],
            "current_room":  room_id,
            "message": (
                f"Welcome {student['name']}! "
                f"Seat {student['seat_no']}. Look at camera."
                if correct_room else
                f"Wrong room! Go to {student['assigned_room']}."
            )
        })
    except Exception as e:
        return jsonify({"found": False, "error": str(e)})

@app.route("/faculty/auth", methods=["POST"])
def faculty_auth():
    try:
        data    = request.json
        room_id = data.get("room_id", "")
        pin     = data.get("pin", "")
        today   = date.today().isoformat()
        valid, reason = validate_pin_digits(pin)
        if not valid:
            return jsonify({"success": False, "reason": reason})
        conn    = get_db()
        session = conn.execute("""
            SELECT es.*, f.name, f.phone, f.faculty_id
            FROM exam_sessions es
            JOIN faculty f ON f.id=es.faculty_id
            WHERE es.room_id=? AND es.exam_date=?
        """, (room_id, today)).fetchone()
        if not session:
            conn.close()
            return jsonify({"success": False,
                            "reason": "no_session_today"})
        if session["faculty_pin"] != pin:
            log_access(room_id,
                       faculty_id=session["faculty_id"],
                       event_type="faculty",
                       method="faculty_pin",
                       status="DENY",
                       deny_reason="wrong_pin")
            conn.close()
            return jsonify({"success": False,
                            "reason": "invalid_pin"})
        student_pin = generate_student_pin(session["id"])
        conn.execute("""
            UPDATE exam_sessions
            SET is_active=1, activated_at=?, student_pin=?
            WHERE id=?
        """, (datetime.now().isoformat(),
              student_pin, session["id"]))
        conn.commit()
        conn.close()
        log_access(room_id,
                   faculty_id=session["faculty_id"],
                   event_type="faculty",
                   method="faculty_pin",
                   status="SUCCESS")
        sms_msg = (
            f"MFA Exam System\n"
            f"Welcome {session['name']}!\n"
            f"Room {room_id} | {session['subject']}\n"
            f"Student Emergency PIN: {student_pin}\n"
            f"Valid today only. Keep confidential."
        )
        threading.Thread(
            target=send_sms,
            args=(session["phone"], sms_msg),
            daemon=True).start()
        log.info(f"[FACULTY] {room_id} by {session['name']} "
                 f"| Student PIN: {student_pin}")
        return jsonify({
            "success":      True,
            "faculty_name": session["name"],
            "faculty_id":   session["faculty_id"],
            "student_pin":  student_pin,
            "room_id":      room_id,
            "subject":      session["subject"],
            "sms_sent_to":  session["phone"]
        })
    except Exception as e:
        log.error(f"Faculty auth error: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/student/auth", methods=["POST"])
def student_auth():
    try:
        room_id    = request.form.get("room_id", "HALL-A")
        rfid_uid   = request.form.get("rfid_uid","").upper().strip()
        face_image = request.files.get("face_image")
        today      = date.today().isoformat()
        conn    = get_db()
        session = conn.execute(
            "SELECT * FROM exam_sessions "
            "WHERE room_id=? AND exam_date=? AND is_active=1",
            (room_id, today)).fetchone()
        if not session:
            conn.close()
            return jsonify({"success": False,
                            "reason": "session_not_active"})
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        conn.close()
        if not student:
            log_access(room_id, event_type="entry",
                       method="rfid+face", status="DENY",
                       deny_reason="rfid_not_found",
                       rfid_uid=rfid_uid)
            return jsonify({"success": False,
                            "reason": "rfid_not_found"})
        if student["assigned_room"] != room_id:
            log_access(room_id, student_id=student["id"],
                       event_type="entry", method="rfid+face",
                       status="DENY", deny_reason="wrong_room",
                       rfid_uid=rfid_uid)
            return jsonify({"success": False,
                            "reason": "wrong_room",
                            "assigned_room":
                                student["assigned_room"]})
        if not face_image:
            return jsonify({"success": False,
                            "reason": "no_face_image"})
        img_bytes = face_image.read()
        match, matched_roll, conf = verify_face(img_bytes)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        photo_path = os.path.join(
            SNAPSHOTS_DIR,
            f"entry_{student['roll_no']}_{ts}.jpg")
        with open(photo_path, "wb") as f:
            f.write(img_bytes)
        if match and matched_roll == student["roll_no"]:
            log_access(room_id, student_id=student["id"],
                       event_type="entry", method="rfid+face",
                       status="SUCCESS", rfid_uid=rfid_uid,
                       photo_path=photo_path)
            log.info(f"[ENTRY] {student['name']} conf={conf:.1f}")
            return jsonify({"success": True,
                            "method": "rfid+face",
                            "student_name": student["name"],
                            "roll_no": student["roll_no"],
                            "seat_no": student["seat_no"],
                            "confidence": conf})
        else:
            log_access(room_id, student_id=student["id"],
                       event_type="entry", method="rfid+face",
                       status="DENY",
                       deny_reason="face_mismatch",
                       rfid_uid=rfid_uid)
            return jsonify({"success": False,
                            "reason": "face_mismatch",
                            "confidence": conf})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/student/auth/rfid-qr", methods=["POST"])
def auth_rfid_qr():
    try:
        room_id  = request.form.get("room_id", "HALL-A")
        rfid_uid = request.form.get("rfid_uid","").upper().strip()
        qr_image = request.files.get("qr_image")
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        conn.close()
        if not student:
            return jsonify({"success": False,
                            "reason": "rfid_not_found"})
        if student["assigned_room"] != room_id:
            return jsonify({"success": False,
                            "reason": "wrong_room",
                            "assigned_room":
                                student["assigned_room"]})
        if not qr_image:
            return jsonify({"success": False,
                            "reason": "no_qr_image"})
        nparr = np.frombuffer(qr_image.read(), np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        det   = cv2.QRCodeDetector()
        qr_data, _, _ = det.detectAndDecode(img)
        if not qr_data:
            return jsonify({"success": False,
                            "reason": "qr_not_detected"})
        valid, roll_no, expired, reason = verify_qr(
            qr_data, room_id)
        if valid and roll_no == student["roll_no"]:
            qr_hash   = qr_data.split(":")[-1]
            today_str = date.today().isoformat()
            conn      = get_db()
            conn.execute("""
                UPDATE qr_tokens
                SET last_used_date=?, use_count=use_count+1
                WHERE qr_hash=?
            """, (today_str, qr_hash))
            conn.commit()
            conn.close()
            log_access(room_id, student_id=student["id"],
                       event_type="entry", method="rfid+qr",
                       status="SUCCESS", fallbacks=1,
                       rfid_uid=rfid_uid)
            return jsonify({"success": True,
                            "method": "rfid+qr",
                            "student_name": student["name"],
                            "roll_no": student["roll_no"]})
        log_access(room_id, student_id=student["id"],
                   event_type="entry", method="rfid+qr",
                   status="DENY", deny_reason=reason,
                   fallbacks=1, rfid_uid=rfid_uid)
        return jsonify({"success": False,
                        "reason": reason, "expired": expired})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/student/auth/face-qr", methods=["POST"])
def auth_face_qr():
    try:
        room_id    = request.form.get("room_id", "HALL-A")
        face_image = request.files.get("face_image")
        qr_image   = request.files.get("qr_image")
        if not face_image or not qr_image:
            return jsonify({"success": False,
                            "reason": "missing_images"})
        match, roll_no, conf = verify_face(face_image.read())
        if not match:
            return jsonify({"success": False,
                            "reason": "face_failed",
                            "confidence": conf})
        nparr = np.frombuffer(qr_image.read(), np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        det   = cv2.QRCodeDetector()
        qr_data, _, _ = det.detectAndDecode(img)
        if not qr_data:
            return jsonify({"success": False,
                            "reason": "qr_not_detected"})
        valid, qr_roll, expired, reason = verify_qr(
            qr_data, room_id)
        if valid and qr_roll == roll_no:
            conn    = get_db()
            student = conn.execute(
                "SELECT * FROM students WHERE roll_no=?",
                (roll_no,)).fetchone()
            conn.close()
            log_access(room_id,
                       student_id=student["id"] if student else None,
                       event_type="entry", method="face+qr",
                       status="SUCCESS", fallbacks=1)
            return jsonify({"success": True,
                            "method": "face+qr",
                            "roll_no": roll_no,
                            "confidence": conf})
        return jsonify({"success": False, "reason": reason})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/student/auth/pin", methods=["POST"])
def auth_pin():
    try:
        data     = request.json
        room_id  = data.get("room_id", "HALL-A")
        rfid_uid = data.get("rfid_uid", "").upper()
        pin      = data.get("pin", "")
        today    = date.today().isoformat()
        conn    = get_db()
        session = conn.execute(
            "SELECT * FROM exam_sessions "
            "WHERE room_id=? AND exam_date=? AND is_active=1",
            (room_id, today)).fetchone()
        if not session or session["student_pin"] != pin:
            conn.close()
            return jsonify({"success": False,
                            "reason": "invalid_pin"})
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        conn.close()
        if not student:
            return jsonify({"success": False,
                            "reason": "student_not_found"})
        log_access(room_id, student_id=student["id"],
                   event_type="entry", method="student_pin",
                   status="SUCCESS", fallbacks=3,
                   rfid_uid=rfid_uid)
        log.info(f"[PIN] {student['name']} via emergency PIN")
        return jsonify({"success": True,
                        "method": "student_pin",
                        "student_name": student["name"],
                        "roll_no": student["roll_no"]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/student/exit-check", methods=["POST"])
def student_exit_check():
    """
    Called when student taps RFID in exit mode.
    Returns how they entered today so WROOM knows
    which exit method to require.
    """
    try:
        data     = request.json
        rfid_uid = data.get("rfid_uid", "").upper().strip()
        room_id  = data.get("room_id", "")
        today    = date.today().isoformat()

        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        if not student:
            conn.close()
            return jsonify({"found": False,
                            "reason": "not_registered"})

        # Check if student already exited today
        already_exited = conn.execute("""
            SELECT * FROM access_logs
            WHERE student_id=? AND room_id=?
            AND exam_date=? AND event_type='exit'
            AND auth_status='SUCCESS'
        """, (student["id"], room_id, today)).fetchone()

        if already_exited:
            conn.close()
            return jsonify({"found": True,
                            "already_exited": True,
                            "student_name": student["name"]})

        # Find how they entered today
        entry_log = conn.execute("""
            SELECT auth_method FROM access_logs
            WHERE student_id=? AND room_id=?
            AND exam_date=? AND event_type='entry'
            AND auth_status='SUCCESS'
            ORDER BY logged_at DESC LIMIT 1
        """, (student["id"], room_id, today)).fetchone()
        conn.close()

        if not entry_log:
            return jsonify({"found": True,
                            "already_exited": False,
                            "student_name": student["name"],
                            "roll_no": student["roll_no"],
                            "auth_method": "rfid+face",
                            "note": "no entry found — defaulting to rfid+face"})

        return jsonify({
            "found":          True,
            "already_exited": False,
            "student_name":   student["name"],
            "roll_no":        student["roll_no"],
            "seat_no":        student["seat_no"],
            "auth_method":    entry_log["auth_method"]
        })

    except Exception as e:
        return jsonify({"found": False, "error": str(e)})

@app.route("/student/exit", methods=["POST"])
def student_exit():
    try:
        data        = request.json
        room_id     = data.get("room_id", "HALL-A")
        rfid_uid    = data.get("rfid_uid", "").upper()
        exit_method = data.get("exit_method", "rfid")
        today       = date.today().isoformat()
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        if not student:
            conn.close()
            return jsonify({"success": False,
                            "reason": "not_found"})
        conn.execute("""
            UPDATE access_logs SET exit_time=?
            WHERE student_id=? AND room_id=?
            AND exam_date=? AND event_type='entry'
            AND exit_time IS NULL
        """, (datetime.now().isoformat(),
              student["id"], room_id, today))
        conn.execute("""
            INSERT INTO access_logs
            (room_id,student_id,event_type,
             auth_method,auth_status)
            VALUES (?,?,'exit',?,?)
        """, (room_id, student["id"], exit_method, "SUCCESS"))
        conn.commit()
        conn.close()
        log.info(f"[EXIT] {student['name']} "
                 f"from {room_id} via {exit_method}")
        return jsonify({"success":      True,
                        "student_name": student["name"],
                        "roll_no":      student["roll_no"],
                        "exit_method":  exit_method})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/face/verify", methods=["POST"])
def face_verify_cam():
    try:
        img_file    = request.files.get("image")
        meta        = json.loads(request.form.get("meta","{}"))
        room_id     = meta.get("room_id", "HALL-A")
        auth_method = meta.get("auth_method", "rfid+face")
        if not img_file:
            return jsonify({"match": False, "confidence": 0,
                            "error": "no_image"})
        img_bytes = img_file.read()
        match, matched_roll, conf = verify_face(img_bytes)
        if not match:
            return jsonify({"match": False,
                            "confidence": conf,
                            "student_id": matched_roll,
                            "correct_room": False,
                            "reason": "face_mismatch"})
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE roll_no=?",
            (matched_roll,)).fetchone()
        conn.close()
        if not student:
            return jsonify({"match": True,
                            "confidence": conf,
                            "student_id": matched_roll,
                            "correct_room": False,
                            "reason": "student_not_in_db"})
        correct_room = student["assigned_room"] == room_id
        if correct_room:
            log_access(room_id, student_id=student["id"],
                       event_type="entry",
                       method=auth_method, status="SUCCESS")
        else:
            log_access(room_id, student_id=student["id"],
                       event_type="entry",
                       method=auth_method, status="DENY",
                       deny_reason="wrong_room")
            log.warning(f"[WRONG ROOM] {student['name']} "
                        f"in {room_id} assigned to "
                        f"{student['assigned_room']}")
        return jsonify({
            "match":         True,
            "confidence":    conf,
            "student_id":    matched_roll,
            "student_name":  student["name"],
            "seat_no":       student["seat_no"],
            "assigned_room": student["assigned_room"],
            "current_room":  room_id,
            "correct_room":  correct_room,
            "message": (
                f"Welcome {student['name']}! "
                f"Seat {student['seat_no']}."
                if correct_room else
                f"Wrong room! Go to {student['assigned_room']}."
            )
        })
    except Exception as e:
        return jsonify({"match": False, "confidence": 0,
                        "error": str(e)})

@app.route("/api/qr/scan", methods=["POST"])
def qr_scan_cam():
    """
    QR scanning with pyzbar (primary) + 7 OpenCV strategies.
    Handles printed paper, laminated QR, phone screens.
    """
    try:
        img_file = request.files.get("image")
        meta     = json.loads(request.form.get("meta", "{}"))
        room_id  = meta.get("room_id", "HALL-A")
        if not img_file:
            return jsonify({"valid": False, "error": "no_image"})

        img_bytes = img_file.read()
        nparr     = np.frombuffer(img_bytes, np.uint8)
        img       = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({"valid": False,
                            "error": "decode_failed"})

        qr_data = None

        # Strategy 0: pyzbar (best for blurry/angled)
        try:
            from pyzbar import pyzbar
            barcodes = pyzbar.decode(img)
            if barcodes:
                qr_data = barcodes[0].data.decode('utf-8')
                log.info(f"[QR] pyzbar: '{qr_data[:50]}'")
            if not qr_data:
                gray     = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                barcodes = pyzbar.decode(gray)
                if barcodes:
                    qr_data = barcodes[0].data.decode('utf-8')
                    log.info(f"[QR] pyzbar gray: '{qr_data[:50]}'")
        except Exception as e:
            log.info(f"[QR] pyzbar unavailable: {e}")

        det = cv2.QRCodeDetector()

        # Strategy 1: raw
        if not qr_data:
            qr_data, _, _ = det.detectAndDecode(img)
            if qr_data: log.info(f"[QR] S1 raw: OK")

        # Strategy 2: grayscale
        if not qr_data:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            qr_data, _, _ = det.detectAndDecode(gray)
            if qr_data: log.info(f"[QR] S2 gray: OK")

        # Strategy 3: CLAHE contrast
        if not qr_data:
            gray     = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            clahe    = cv2.createCLAHE(
                clipLimit=2.0, tileGridSize=(8,8))
            enhanced = clahe.apply(gray)
            qr_data, _, _ = det.detectAndDecode(enhanced)
            if qr_data: log.info(f"[QR] S3 CLAHE: OK")

        # Strategy 4: Otsu threshold
        if not qr_data:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(
                gray, 0, 255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            qr_data, _, _ = det.detectAndDecode(thresh)
            if qr_data: log.info(f"[QR] S4 threshold: OK")

        # Strategy 5: sharpen
        if not qr_data:
            gray   = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            kernel = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
            sharp  = cv2.filter2D(gray, -1, kernel)
            qr_data, _, _ = det.detectAndDecode(sharp)
            if qr_data: log.info(f"[QR] S5 sharpen: OK")

        # Strategy 6: 2x upscale
        if not qr_data:
            up = cv2.resize(img, None, fx=2, fy=2,
                            interpolation=cv2.INTER_CUBIC)
            qr_data, _, _ = det.detectAndDecode(up)
            if qr_data: log.info(f"[QR] S6 upscale: OK")

        # Strategy 7: upscale + sharpen
        if not qr_data:
            gray  = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            up    = cv2.resize(gray, None, fx=2, fy=2,
                               interpolation=cv2.INTER_CUBIC)
            kern  = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
            sharp = cv2.filter2D(up, -1, kern)
            qr_data, _, _ = det.detectAndDecode(sharp)
            if qr_data: log.info(f"[QR] S7 upscale+sharpen: OK")

        if not qr_data:
            log.warning("[QR] All strategies failed")
            return jsonify({"valid": False,
                            "error": "no_qr_detected"})

        log.info(f"[QR] Decoded: {qr_data[:60]}")
        valid, roll_no, expired, reason = verify_qr(
            qr_data, room_id)
        log.info(f"[QR] valid={valid} roll={roll_no} "
                 f"reason={reason}")
        return jsonify({"valid":      valid,
                        "expired":    expired,
                        "student_id": roll_no,
                        "reason":     reason})
    except Exception as e:
        log.error(f"QR scan error: {e}")
        return jsonify({"valid": False, "error": str(e)})

@app.route("/api/anomaly/snapshot", methods=["POST"])
def anomaly_snapshot():
    try:
        img_data = request.data
        reason   = request.headers.get("X-Reason", "trespass")
        room_id  = request.headers.get("X-Room", "HALL-A")
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(
            SNAPSHOTS_DIR, f"alarm_{room_id}_{ts}.jpg")
        with open(filepath, "wb") as f:
            f.write(img_data)
        log_anomaly(room_id, "unauthorized_entry",
                    f"Trespass: {reason}", filepath)
        log.warning(f"[ALARM] Snapshot: {filepath}")
        return jsonify({"saved": True, "path": filepath})
    except Exception as e:
        return jsonify({"saved": False, "error": str(e)})

@app.route("/enroll/face", methods=["POST"])
def enroll_face():
    try:
        roll_no    = request.form.get("roll_no", "")
        face_image = request.files.get("face_image")
        if not roll_no or not face_image:
            return jsonify({"status": "missing_data"})
        img_bytes = face_image.read()
        nparr     = np.frombuffer(img_bytes, np.uint8)
        img       = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        detected  = face_cascade.detectMultiScale(img, 1.3, 5)
        if len(detected) == 0:
            return jsonify({"status": "no_face_detected"})
        folder = os.path.join(FACES_DIR, roll_no)
        os.makedirs(folder, exist_ok=True)
        count    = len(os.listdir(folder))
        x,y,w,h  = detected[0]
        roi      = cv2.resize(img[y:y+h, x:x+w], (200,200))
        cv2.imwrite(os.path.join(folder,f"{count+1}.jpg"), roi)
        train_faces()
        return jsonify({"status": "enrolled",
                        "roll_no": roll_no,
                        "photo_no": count+1})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

@app.route("/enroll/rfid", methods=["POST"])
def enroll_rfid():
    try:
        data = request.json
        conn = get_db()
        conn.execute(
            "UPDATE students SET rfid_uid=?,updated_at=? "
            "WHERE roll_no=?",
            (data.get("rfid_uid","").upper(),
             datetime.now().isoformat(),
             data.get("roll_no","")))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

@app.route("/admin/generate-qr/<roll_no>", methods=["GET"])
def generate_qr(roll_no):
    try:
        import qrcode
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE roll_no=?",
            (roll_no,)).fetchone()
        if not student:
            conn.close()
            return jsonify({"error": "not_found"}), 404
        room_id = student["assigned_room"]
        if not room_id:
            conn.close()
            return jsonify({"error": "no_room"}), 400
        payload, qr_hash = make_qr_hash(roll_no, room_id)
        full = f"{payload}:{qr_hash}"
        conn.execute("""
            INSERT OR IGNORE INTO qr_tokens
            (student_id, qr_hash, exam_start, exam_end, room_id)
            VALUES (?,?,?,?,?)
        """, (student["id"], qr_hash,
              EXAM_START_DATE, EXAM_END_DATE, room_id))
        conn.commit()
        conn.close()
        # Simpler QR — bigger squares, less dense
        # Easier for ESP32-CAM to scan
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10, border=4)
        qr.add_data(full)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black",
                            back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png",
                         as_attachment=True,
                         download_name=f"qr_{roll_no}.png")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/logs/<room_id>", methods=["GET"])
def get_logs(room_id):
    try:
        limit = request.args.get("limit", 100)
        today = date.today().isoformat()
        conn  = get_db()
        rows  = conn.execute("""
            SELECT a.logged_at,s.name,s.roll_no,s.seat_no,
                   a.event_type,a.auth_method,a.auth_status,
                   a.deny_reason,a.fallbacks_used,
                   a.entry_time,a.exit_time
            FROM access_logs a
            LEFT JOIN students s ON s.id=a.student_id
            WHERE a.room_id=? AND a.exam_date=?
            ORDER BY a.logged_at DESC LIMIT ?
        """, (room_id, today, limit)).fetchall()
        conn.close()
        return jsonify({"logs": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/anomalies/<room_id>", methods=["GET"])
def get_anomalies(room_id):
    try:
        today = date.today().isoformat()
        conn  = get_db()
        rows  = conn.execute(
            "SELECT * FROM anomaly_events "
            "WHERE room_id=? AND exam_date=? "
            "ORDER BY occurred_at DESC",
            (room_id, today)).fetchall()
        conn.close()
        return jsonify({"anomalies": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/absentees/<room_id>", methods=["GET"])
def get_absentees(room_id):
    try:
        today = date.today().isoformat()
        conn  = get_db()
        rows  = conn.execute("""
            SELECT s.roll_no,s.name,s.seat_no
            FROM students s
            LEFT JOIN access_logs a
                ON a.student_id=s.id
                AND a.exam_date=?
                AND a.auth_status='SUCCESS'
                AND a.event_type='entry'
            WHERE s.assigned_room=? AND a.id IS NULL
            ORDER BY s.seat_no
        """, (today, room_id)).fetchall()
        conn.close()
        return jsonify({"room_id": room_id, "date": today,
                        "absentees": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/students", methods=["GET"])
def get_students():
    try:
        conn = get_db()
        rows = conn.execute("""
            SELECT id,roll_no,name,phone,rfid_uid,
                   assigned_room,seat_no,enrolled_at,
                   CASE WHEN face_vector IS NOT NULL
                        THEN 1 ELSE 0 END AS face_enrolled
            FROM students
            ORDER BY assigned_room,seat_no
        """).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/students", methods=["POST"])
def add_student():
    try:
        data = request.json
        conn = get_db()
        conn.execute("""
            INSERT INTO students
            (roll_no,name,phone,assigned_room,seat_no)
            VALUES (?,?,?,?,?)
        """, (data["roll_no"], data["name"],
              data.get("phone",""),
              data.get("assigned_room",""),
              data.get("seat_no","")))
        conn.commit()
        conn.close()
        return jsonify({"status": "created"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/rooms", methods=["GET"])
def get_rooms():
    try:
        today = date.today().isoformat()
        conn  = get_db()
        rows  = conn.execute("""
            SELECT es.room_id,es.subject,es.is_active,
                   es.activated_at,f.name AS faculty_name,
                   es.student_pin,es.faculty_pin
            FROM exam_sessions es
            LEFT JOIN faculty f ON f.id=es.faculty_id
            WHERE es.exam_date=?
        """, (today,)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════
# AUTO SESSION CREATION
# ══════════════════════════════════════════════════════════════
def ensure_today_sessions():
    """Creates today's exam sessions if missing — runs on startup"""
    conn  = get_db()
    today = date.today().isoformat()
    rooms = [
        ('HALL-A', 'Data Structures',   1, '1245'),
        ('HALL-B', 'Computer Networks', 2, '4578'),
    ]
    for room_id, subject, fac_id, pin in rooms:
        conn.execute("""
            INSERT OR IGNORE INTO exam_sessions
            (room_id,subject,faculty_id,faculty_pin,exam_date)
            VALUES (?,?,?,?,?)
        """, (room_id, subject, fac_id, pin, today))
    conn.commit()
    conn.close()
    log.info(f"Sessions ensured for {today}")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 52)
    print("  SENTINEL — MFA EXAM SECURITY SYSTEM (WEBCAM QR)")
    print("=" * 52)
    print(f"  Server IP      : {SERVER_IP}")
    print(f"  Port           : {SERVER_PORT}")
    print(f"  Face threshold : {FACE_THRESHOLD}")
    print(f"  Safe PIN digits: 1, 2, 4, 5, 7, 8")
    print(f"  QR dates       : {EXAM_START_DATE} → {EXAM_END_DATE}")
    print("=" * 52)

    init_db()
    ensure_today_sessions()
    train_faces()

    mqtt_thread = threading.Thread(
        target=start_mqtt, daemon=True)
    mqtt_thread.start()
    print("  MQTT broker    : started")
    print(f"  Running on     : http://{SERVER_IP}:{SERVER_PORT}")
    print("=" * 52)

    app.run(host="0.0.0.0", port=SERVER_PORT,
            debug=False, threaded=True)
