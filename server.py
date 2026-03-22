"""
SENTINEL — MFA EXAM HALL SECURITY SYSTEM
Flask Backend Server — DeepFace Edition
=========================================
Major upgrades from previous version:
- DeepFace (ArcFace model) replaces LBPH face recognition
  → State-of-the-art accuracy, no training needed
  → Works with just 1-3 reference photos per student
  → Handles lighting, angle, expression variations
- Hall ticket PDF generation endpoint
- Webcam QR scanner (laptop webcam, not ESP32-CAM)
- All previous fixes retained
"""

import os, io, json, hashlib, logging
import sqlite3, threading, time, shutil
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

# Suppress TensorFlow/DeepFace startup noise
os.environ["TF_CPP_MIN_LOG_LEVEL"]  = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

app = Flask(__name__)
CORS(app)

# ─── CONFIG ───────────────────────────────────────────────────
SERVER_IP       = "192.168.154.107"
SERVER_PORT     = 3000
DB_PATH         = "exam.db"
FACES_DIR       = "faces"
SNAPSHOTS_DIR   = "alarm_photos"
QR_DIR          = "qr_codes"
HALL_TICKETS_DIR= "hall_tickets"
MQTT_BROKER     = "localhost"
MQTT_PORT       = 1883
FAST2SMS_KEY    = "YwsOCXEKJ6HWc9fibeUMz4u7LlPQovd8NymSGa5AgRxpnZh3DTAeU4NrC65GaYMTXxdLH3pKZF2sJkzS"
QR_SECRET       = "examhall_qr_secret_2026!!"
PIN_SECRET      = "examhall_pin_secret_2026!"
EXAM_START_DATE = "2026-03-16"
EXAM_END_DATE   = "2026-04-30"
SAFE_PIN_DIGITS = [1, 2, 4, 5, 7, 8]

# DeepFace config
# distance_metric: cosine (default), euclidean, euclidean_l2
# model_name: ArcFace (best), Facenet512, VGG-Face, DeepFace
DEEPFACE_MODEL    = "ArcFace"
DEEPFACE_METRIC   = "cosine"
DEEPFACE_THRESHOLD = 0.40   # lower = stricter. ArcFace cosine: 0.68 default, 0.40 is strict

os.makedirs(FACES_DIR,      exist_ok=True)
os.makedirs(SNAPSHOTS_DIR,  exist_ok=True)
os.makedirs(QR_DIR,         exist_ok=True)
os.makedirs(HALL_TICKETS_DIR, exist_ok=True)

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
    for col, default in [
        ("exam_start", "'2026-03-16'"),
        ("exam_end",   "'2026-04-30'")
    ]:
        try:
            conn.execute(
                f"ALTER TABLE qr_tokens ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT {default}")
            conn.commit()
        except:
            pass
    conn.close()
    log.info("Database initialised!")

# ══════════════════════════════════════════════════════════════
# PIN HELPERS
# ══════════════════════════════════════════════════════════════
def is_valid_pin(pin: str) -> bool:
    return all(c in "124578" for c in pin) and len(pin) >= 4

def validate_pin_digits(pin: str) -> tuple:
    if len(pin) < 4:
        return False, "PIN must be at least 4 digits"
    if len(pin) > 8:
        return False, "PIN must be at most 8 digits"
    if any(c in "369" for c in pin):
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
# DEEPFACE RECOGNITION
# ══════════════════════════════════════════════════════════════
deepface_ready = False

def init_deepface():
    """Pre-load DeepFace model on startup to avoid delay on first scan."""
    global deepface_ready
    try:
        from deepface import DeepFace
        import numpy as np
        # Warm up the model with a dummy image
        dummy = np.zeros((112, 112, 3), dtype=np.uint8)
        dummy_path = os.path.join(SNAPSHOTS_DIR, "_warmup.jpg")
        cv2.imwrite(dummy_path, dummy)
        try:
            DeepFace.represent(
                img_path=dummy_path,
                model_name=DEEPFACE_MODEL,
                enforce_detection=False)
        except:
            pass
        finally:
            if os.path.exists(dummy_path):
                os.remove(dummy_path)
        deepface_ready = True
        log.info(f"DeepFace model '{DEEPFACE_MODEL}' loaded and ready")
    except Exception as e:
        log.error(f"DeepFace init failed: {e}")
        deepface_ready = False

def get_reference_photo(roll_no: str):
    """
    Get the best reference photo for a student.
    Prefers photos captured via ESP32-CAM (capture_from_cam.py)
    which are stored in faces/<roll_no>/cam_*.jpg
    Falls back to any photo in the folder.
    """
    folder = os.path.join(FACES_DIR, roll_no)
    if not os.path.isdir(folder):
        return None

    files = [f for f in os.listdir(folder)
             if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    if not files:
        return None

    # Prefer CAM-captured photos
    cam_files = [f for f in files if f.startswith("cam_")]
    if cam_files:
        return os.path.join(folder, sorted(cam_files)[-1])

    # Fall back to latest photo
    return os.path.join(folder, sorted(files)[-1])

def verify_face_deepface(img_bytes: bytes):
    """
    Verify face using DeepFace ArcFace model.
    
    Returns: (match: bool, roll_no: str, distance: float)
    
    Distance is cosine distance (0=identical, 1=completely different)
    Threshold: 0.40 means faces must be at least 60% similar
    
    DeepFace compares the captured image against ALL student
    reference photos and returns the best match.
    """
    try:
        from deepface import DeepFace

        # Save incoming image to temp file
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        tmp_path = os.path.join(SNAPSHOTS_DIR, f"_tmp_{ts}.jpg")
        with open(tmp_path, "wb") as f:
            f.write(img_bytes)

        # Check face is actually present
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades +
            "haarcascade_frontalface_default.xml")
        tmp_img  = cv2.imread(tmp_path, cv2.IMREAD_GRAYSCALE)
        detected = face_cascade.detectMultiScale(
            tmp_img, 1.1, 4, minSize=(40, 40))
        if len(detected) == 0:
            detected = face_cascade.detectMultiScale(
                tmp_img, 1.05, 3, minSize=(30, 30))
        if len(detected) == 0:
            log.warning("[DeepFace] No face detected in frame")
            os.remove(tmp_path)
            return False, "", 1.0

        best_roll     = ""
        best_distance = 1.0
        best_match    = False

        # Compare against each enrolled student
        for roll_no in sorted(os.listdir(FACES_DIR)):
            folder = os.path.join(FACES_DIR, roll_no)
            if not os.path.isdir(folder):
                continue

            ref_photo = get_reference_photo(roll_no)
            if not ref_photo:
                continue

            try:
                result = DeepFace.verify(
                    img1_path       = tmp_path,
                    img2_path       = ref_photo,
                    model_name      = DEEPFACE_MODEL,
                    distance_metric = DEEPFACE_METRIC,
                    enforce_detection = False,
                    silent          = True
                )
                distance = result["distance"]
                verified = result["verified"]

                log.info(f"[DeepFace] {roll_no}: "
                         f"distance={distance:.3f} "
                         f"verified={verified}")

                if distance < best_distance:
                    best_distance = distance
                    best_roll     = roll_no
                    best_match    = (
                        distance < DEEPFACE_THRESHOLD)

            except Exception as e:
                log.warning(f"[DeepFace] Error comparing "
                            f"with {roll_no}: {e}")
                continue

        os.remove(tmp_path)

        log.info(f"[DeepFace] Best match: {best_roll} "
                 f"distance={best_distance:.3f} "
                 f"match={best_match}")
        return best_match, best_roll, best_distance

    except Exception as e:
        log.error(f"[DeepFace] verify error: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False, "", 1.0

def verify_face(img_bytes: bytes):
    """
    Main face verification function.
    Uses DeepFace if available, falls back to LBPH.
    Returns: (match, roll_no, confidence/distance)
    """
    if deepface_ready:
        return verify_face_deepface(img_bytes)
    else:
        log.warning("[Face] DeepFace not ready — using LBPH fallback")
        return verify_face_lbph(img_bytes)

# ── LBPH FALLBACK ─────────────────────────────────────────────
recognizer     = cv2.face.LBPHFaceRecognizer_create()
face_cascade   = cv2.CascadeClassifier(
    cv2.data.haarcascades +
    "haarcascade_frontalface_default.xml")
face_labels    = {}
face_label_rev = {}
model_trained  = False
LBPH_THRESHOLD = 160

def train_faces():
    global model_trained, face_labels, face_label_rev
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
            log.info(f"LBPH model loaded — {label_id} students")
            return
        except Exception as e:
            log.warning(f"LBPH load failed: {e}")
    faces, labels = [], []
    label_id = 0
    if not os.path.exists(FACES_DIR): return
    for roll_no in sorted(os.listdir(FACES_DIR)):
        folder = os.path.join(FACES_DIR, roll_no)
        if not os.path.isdir(folder): continue
        face_labels[label_id]   = roll_no
        face_label_rev[roll_no] = label_id
        for f in os.listdir(folder):
            if not f.lower().endswith(
                    (".jpg",".jpeg",".png")): continue
            img = cv2.imread(
                os.path.join(folder, f),
                cv2.IMREAD_GRAYSCALE)
            if img is None: continue
            faces.append(cv2.resize(img, (200, 200)))
            labels.append(label_id)
        label_id += 1
    if faces:
        recognizer.train(faces, np.array(labels))
        model_trained = True
        log.info(f"LBPH trained: {len(faces)} images, "
                 f"{label_id} students")

def verify_face_lbph(img_bytes: bytes):
    try:
        nparr = np.frombuffer(img_bytes, np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return False, "", 999.0
        gray     = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        detected = face_cascade.detectMultiScale(
            gray, 1.1, 4, minSize=(40,40))
        if len(detected) == 0:
            detected = face_cascade.detectMultiScale(
                gray, 1.05, 3, minSize=(30,30))
        if len(detected) == 0:
            log.warning("[LBPH] No face detected")
            return False, "", 999.0
        x,y,w,h = max(detected, key=lambda r: r[2]*r[3])
        roi = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
        roi = cv2.equalizeHist(roi)
        roi = cv2.GaussianBlur(roi, (3,3), 0)
        if model_trained:
            label, conf = recognizer.predict(roi)
            roll  = face_labels.get(label, "")
            match = conf < LBPH_THRESHOLD
            log.info(f"[LBPH] {roll} conf={conf:.1f} "
                     f"{'MATCH' if match else 'NO MATCH'}")
            return match, roll, float(conf)
        return False, "", 999.0
    except Exception as e:
        log.error(f"[LBPH] error: {e}")
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
        payload  = (f"UUCMS:{roll_no}:{qr_room}:"
                    f"{exam_start}:{exam_end}")
        expected = hashlib.sha256(
            f"{payload}:{QR_SECRET}".encode()).hexdigest()
        if qr_hash != expected:
            return False, "", False, "tampered"
        if qr_room != room_id:
            return False, roll_no, False, "wrong_room"
        today = date.today()
        start = date.fromisoformat(exam_start)
        end   = date.fromisoformat(exam_end)
        if today < start: return False, roll_no, False, "exam_not_started"
        if today > end:   return False, roll_no, True,  "exam_series_ended"
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
# HALL TICKET PDF GENERATION
# ══════════════════════════════════════════════════════════════
def generate_hall_ticket_pdf(student, session, qr_img_bytes):
    """
    Generates a professional hall ticket PDF with:
    - College header
    - Student details (name, roll, room, seat)
    - Exam details (subject, date, time)
    - QR code for entry scanning
    - Instructions for student
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer,
            Table, TableStyle, Image as RLImage)
        from reportlab.lib.styles import (
            getSampleStyleSheet, ParagraphStyle)
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        import tempfile

        roll_no  = student["roll_no"]
        filename = f"hall_ticket_{roll_no}.pdf"
        filepath = os.path.join(HALL_TICKETS_DIR, filename)

        doc = SimpleDocTemplate(
            filepath,
            pagesize=A4,
            rightMargin=2*cm, leftMargin=2*cm,
            topMargin=2*cm,   bottomMargin=2*cm)

        styles = getSampleStyleSheet()

        # Custom styles
        title_style = ParagraphStyle(
            "Title",
            parent=styles["Heading1"],
            fontSize=16,
            textColor=colors.HexColor("#1a237e"),
            spaceAfter=4,
            alignment=TA_CENTER,
            fontName="Helvetica-Bold")

        subtitle_style = ParagraphStyle(
            "Subtitle",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#424242"),
            spaceAfter=2,
            alignment=TA_CENTER)

        section_style = ParagraphStyle(
            "Section",
            parent=styles["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#212121"),
            fontName="Helvetica")

        bold_style = ParagraphStyle(
            "Bold",
            parent=styles["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1a237e"))

        warning_style = ParagraphStyle(
            "Warning",
            parent=styles["Normal"],
            fontSize=8,
            textColor=colors.HexColor("#b71c1c"),
            alignment=TA_CENTER)

        story = []

        # ── HEADER ────────────────────────────────────────
        story.append(Paragraph(
            "SENTINEL EXAM SECURITY SYSTEM", title_style))
        story.append(Paragraph(
            "Official Hall Admission Ticket", subtitle_style))
        story.append(Paragraph(
            f"Academic Year 2025-26", subtitle_style))

        # Horizontal line
        story.append(Spacer(1, 0.3*cm))
        line_table = Table([[""]],
                           colWidths=[17*cm],
                           rowHeights=[0.05*cm])
        line_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1),
             colors.HexColor("#1a237e")),
            ("LINEABOVE",  (0,0), (-1,-1), 1,
             colors.HexColor("#1a237e")),
        ]))
        story.append(line_table)
        story.append(Spacer(1, 0.3*cm))

        # ── STUDENT + QR side by side ──────────────────
        # Save QR to temp file for ReportLab
        tmp_qr = tempfile.NamedTemporaryFile(
            suffix=".png", delete=False)
        tmp_qr.write(qr_img_bytes)
        tmp_qr.close()

        qr_image = RLImage(tmp_qr.name, width=4*cm, height=4*cm)

        exam_date_str = (session["exam_date"]
                         if session else date.today().isoformat())
        subject_str   = (session["subject"]
                         if session else "Examination")
        faculty_str   = (session["faculty_name"]
                         if session else "—")

        student_info = [
            [Paragraph("<b>STUDENT DETAILS</b>", bold_style), ""],
            [Paragraph("Name:", section_style),
             Paragraph(f"<b>{student['name']}</b>",
                       ParagraphStyle("v", parent=section_style,
                                      fontName="Helvetica-Bold",
                                      fontSize=11))],
            [Paragraph("Roll Number:", section_style),
             Paragraph(student["roll_no"], section_style)],
            [Paragraph("Exam Hall:", section_style),
             Paragraph(f"<b>{student['assigned_room']}</b>",
                       ParagraphStyle("v", parent=section_style,
                                      fontName="Helvetica-Bold",
                                      textColor=colors.HexColor(
                                          "#1b5e20")))],
            [Paragraph("Seat Number:", section_style),
             Paragraph(str(student["seat_no"]), section_style)],
            ["", ""],
            [Paragraph("<b>EXAM DETAILS</b>", bold_style), ""],
            [Paragraph("Subject:", section_style),
             Paragraph(subject_str, section_style)],
            [Paragraph("Date:", section_style),
             Paragraph(exam_date_str, section_style)],
            [Paragraph("Invigilator:", section_style),
             Paragraph(faculty_str, section_style)],
        ]

        info_table = Table(student_info,
                           colWidths=[4*cm, 8*cm])
        info_table.setStyle(TableStyle([
            ("VALIGN",      (0,0), (-1,-1), "TOP"),
            ("TOPPADDING",  (0,0), (-1,-1), 3),
            ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ]))

        # Main layout: info on left, QR on right
        main_table = Table(
            [[info_table, qr_image]],
            colWidths=[13*cm, 4.5*cm])
        main_table.setStyle(TableStyle([
            ("VALIGN",  (0,0), (-1,-1), "TOP"),
            ("ALIGN",   (1,0), (1,0),   "CENTER"),
            ("BOX",     (0,0), (-1,-1), 0.5,
             colors.HexColor("#9e9e9e")),
            ("BACKGROUND", (0,0), (-1,-1),
             colors.HexColor("#f8f9fa")),
            ("PADDING", (0,0), (-1,-1), 8),
        ]))
        story.append(main_table)
        story.append(Spacer(1, 0.2*cm))

        # QR label
        story.append(Paragraph(
            "Scan the QR code above at the exam hall entrance",
            ParagraphStyle("qrlabel",
                           parent=styles["Normal"],
                           fontSize=8,
                           textColor=colors.HexColor("#616161"),
                           alignment=TA_CENTER)))

        story.append(Spacer(1, 0.4*cm))

        # ── VALIDITY BANNER ───────────────────────────
        validity_table = Table(
            [[Paragraph(
                f"QR VALID: {EXAM_START_DATE} to {EXAM_END_DATE} "
                f"| Usable ONCE per exam day",
                ParagraphStyle("valid",
                               parent=styles["Normal"],
                               fontSize=8,
                               textColor=colors.white,
                               alignment=TA_CENTER))]],
            colWidths=[17*cm])
        validity_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1),
             colors.HexColor("#1b5e20")),
            ("PADDING",    (0,0), (-1,-1), 6),
        ]))
        story.append(validity_table)
        story.append(Spacer(1, 0.4*cm))

        # ── INSTRUCTIONS ──────────────────────────────
        instructions = [
            "INSTRUCTIONS FOR STUDENTS",
            "1. Carry this hall ticket to every examination.",
            "2. At the entrance, tap your RFID card on the reader.",
            "3. Look directly at the camera for face verification.",
            "4. If face scan fails, show this QR code to the webcam.",
            "5. Emergency PIN entry available through invigilator only.",
            "6. This hall ticket is non-transferable.",
            "7. Loss of hall ticket must be reported to the exam office immediately.",
        ]

        inst_data = [[Paragraph(
            instructions[0],
            ParagraphStyle("inst_h",
                           parent=styles["Normal"],
                           fontSize=9,
                           fontName="Helvetica-Bold",
                           textColor=colors.HexColor("#1a237e")))]]
        for inst in instructions[1:]:
            inst_data.append([Paragraph(
                inst,
                ParagraphStyle("inst",
                               parent=styles["Normal"],
                               fontSize=8,
                               textColor=colors.HexColor(
                                   "#212121")))])

        inst_table = Table(inst_data, colWidths=[17*cm])
        inst_table.setStyle(TableStyle([
            ("BOX",        (0,0), (-1,-1), 0.5,
             colors.HexColor("#9e9e9e")),
            ("BACKGROUND", (0,0), (-1,0),
             colors.HexColor("#e8eaf6")),
            ("BACKGROUND", (0,1), (-1,-1),
             colors.HexColor("#fafafa")),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ]))
        story.append(inst_table)
        story.append(Spacer(1, 0.3*cm))

        # ── FOOTER ────────────────────────────────────
        story.append(Paragraph(
            f"Generated by SENTINEL MFA Exam Security System | "
            f"{datetime.now().strftime('%d-%m-%Y %H:%M')}",
            ParagraphStyle("footer",
                           parent=styles["Normal"],
                           fontSize=7,
                           textColor=colors.HexColor("#9e9e9e"),
                           alignment=TA_CENTER)))

        story.append(Paragraph(
            "⚠ This is a system-generated document. "
            "Tampering with the QR code is a punishable offence.",
            warning_style))

        doc.build(story)

        # Cleanup temp QR file
        os.unlink(tmp_qr.name)

        log.info(f"Hall ticket generated: {filepath}")
        return filepath

    except Exception as e:
        log.error(f"Hall ticket generation error: {e}")
        raise

# ══════════════════════════════════════════════════════════════
# SMS
# ══════════════════════════════════════════════════════════════
def send_sms(phone: str, message: str):
    try:
        phone = phone.replace("+91","").replace(" ","").strip()
        if len(phone) != 10:
            log.warning(f"Invalid phone: {phone}")
            return False
        r = req_lib.post(
            "https://www.fast2sms.com/dev/bulkV2",
            json={"route":"q","message":message,
                  "language":"english","flash":0,
                  "numbers":phone},
            headers={"authorization":FAST2SMS_KEY,
                     "Content-Type":"application/json"},
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
webcam_qr_active  = False
webcam_qr_lock    = threading.Lock()
webcam_qr_pending = []   # queue for concurrent requests

def decode_qr_from_frame(frame):
    det     = cv2.QRCodeDetector()
    qr_data = None
    try:
        from pyzbar import pyzbar
        barcodes = pyzbar.decode(frame)
        if barcodes:
            return barcodes[0].data.decode('utf-8')
    except: pass
    qr_data, _, _ = det.detectAndDecode(frame)
    if qr_data: return qr_data
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    qr_data, _, _ = det.detectAndDecode(gray)
    if qr_data: return qr_data
    _, thresh = cv2.threshold(
        gray, 0, 255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    qr_data, _, _ = det.detectAndDecode(thresh)
    if qr_data: return qr_data
    kernel = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
    sharp  = cv2.filter2D(gray, -1, kernel)
    qr_data, _, _ = det.detectAndDecode(sharp)
    if qr_data: return qr_data
    up = cv2.resize(frame, None, fx=2, fy=2,
                    interpolation=cv2.INTER_CUBIC)
    qr_data, _, _ = det.detectAndDecode(up)
    return qr_data

def webcam_qr_scan(room_id, student_id):
    global webcam_qr_active, webcam_qr_pending
    with webcam_qr_lock:
        if webcam_qr_active:
            webcam_qr_pending.append((room_id, student_id))
            log.info(f"[WEBCAM QR] Queued {student_id} — scanner busy")
            publish("exam/qr/result",{
                "valid":False,
                "reason":"scanner_busy_please_wait",
                "student_id":student_id})
            return
        webcam_qr_active = True
    log.info(f"[WEBCAM QR] Starting for {student_id} room={room_id}")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        publish("exam/qr/result",
                {"valid":False,"reason":"webcam_unavailable"})
        webcam_qr_active = False
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    start   = time.time()
    timeout = 20
    qr_data = None
    while time.time() - start < timeout:
        ret, frame = cap.read()
        if not ret: break
        display   = frame.copy()
        h, w      = display.shape[:2]
        cx, cy    = w//2, h//2
        box_sz    = 220
        remaining = int(timeout - (time.time() - start))
        cv2.rectangle(display,
                      (cx-box_sz, cy-box_sz),
                      (cx+box_sz, cy+box_sz),
                      (0,255,0), 2)
        cv2.putText(display, "Hold QR inside the box",
                    (10,30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255,255,255), 2)
        cv2.putText(display, f"Student: {student_id}",
                    (10,60), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (200,200,200), 1)
        cv2.putText(display, f"Time left: {remaining}s",
                    (10,90), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0,200,255), 2)
        qr_data = decode_qr_from_frame(frame)
        if qr_data:
            cv2.putText(display, "QR DETECTED!",
                        (cx-80, cy-box_sz-10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, (0,255,0), 2)
        cv2.imshow("SENTINEL QR Scanner (Q=cancel)", display)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), 27): break
        if qr_data:
            time.sleep(0.5)
            break
    cap.release()
    cv2.destroyAllWindows()
    if not qr_data:
        publish("exam/qr/result",
                {"valid":False,"reason":"no_qr_detected"})
        webcam_qr_active = False
        return
    valid, roll_no, expired, reason = verify_qr(qr_data, room_id)
    if valid:
        qr_hash   = qr_data.split(":")[-1]
        today_str = date.today().isoformat()
        conn      = get_db()
        conn.execute("""
            UPDATE qr_tokens
            SET last_used_date=?, use_count=use_count+1
            WHERE qr_hash=?
        """, (today_str, qr_hash))
        conn.commit()
        student = conn.execute(
            "SELECT * FROM students WHERE roll_no=?",
            (roll_no,)).fetchone()
        conn.close()
        if student:
            log_access(room_id, student_id=student["id"],
                       event_type="entry",
                       method="qr_webcam",
                       status="SUCCESS", fallbacks=1)
    publish("exam/qr/result",
            {"valid":valid,"expired":expired,
             "student_id":roll_no,"reason":reason})
    webcam_qr_active = False

    # FIX: Process next queued request if any
    with webcam_qr_lock:
        if webcam_qr_pending:
            next_room, next_student = webcam_qr_pending.pop(0)
            log.info(f"[WEBCAM QR] Processing queued: {next_student}")
            threading.Thread(
                target=webcam_qr_scan,
                args=(next_room, next_student),
                daemon=True).start()

def on_connect(client, userdata, flags, rc, props=None):
    log.info(f"MQTT connected rc={rc}")
    client.subscribe("exam/cam/command")

def on_message(client, userdata, msg):
    try:
        data  = json.loads(msg.payload.decode())
        topic = msg.topic
        log.info(f"MQTT [{topic}]: {data}")
        if topic == "exam/cam/command":
            cmd        = data.get("cmd","")
            student_id = data.get("student_id","")
            room_id    = data.get("room_id","HALL-A")
            if cmd == "qr_scan":
                threading.Thread(
                    target=webcam_qr_scan,
                    args=(room_id, student_id),
                    daemon=True).start()
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
    except: pass

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
    """, (room_id,student_id,faculty_id,event_type,
          method,status,deny_reason,fallbacks,
          datetime.now().isoformat(),rfid_uid,photo_path))
    conn.commit()
    conn.close()

def log_anomaly(room_id, event_type, description, photo_path=None):
    conn = get_db()
    conn.execute("""
        INSERT INTO anomaly_events
        (room_id,event_type,description,photo_path)
        VALUES (?,?,?,?)
    """, (room_id,event_type,description,photo_path))
    conn.commit()
    conn.close()
    publish("exam/anomaly/alert",{
        "room_id":room_id,"event_type":event_type,
        "description":description,
        "timestamp":datetime.now().isoformat()})

# ══════════════════════════════════════════════════════════════
# API ROUTES
# ══════════════════════════════════════════════════════════════
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":       "ok",
        "mqtt":         mqtt_client.is_connected(),
        "face_engine":  "DeepFace/" + DEEPFACE_MODEL if deepface_ready else "LBPH",
        "deepface_ready": deepface_ready,
        "time":         datetime.now().isoformat()
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
            return jsonify({"success":True,
                            "token":"admin_authenticated",
                            "username":data["username"]})
        return jsonify({"success":False,
                        "error":"Invalid credentials"})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)})

@app.route("/api/admin/validate-pin", methods=["POST"])
def validate_pin_route():
    try:
        pin   = request.json.get("pin","")
        valid, reason = validate_pin_digits(pin)
        return jsonify({"valid":valid,"reason":reason,
                        "safe_digits":"1,2,4,5,7,8"})
    except Exception as e:
        return jsonify({"valid":False,"error":str(e)})

@app.route("/api/admin/set-faculty-pin", methods=["POST"])
def set_faculty_pin():
    try:
        data    = request.json
        room_id = data.get("room_id","")
        pin     = data.get("pin","")
        today   = date.today().isoformat()
        valid, reason = validate_pin_digits(pin)
        if not valid:
            return jsonify({"success":False,"reason":reason})
        conn = get_db()
        conn.execute(
            "UPDATE exam_sessions SET faculty_pin=? "
            "WHERE room_id=? AND exam_date=?",
            (pin,room_id,today))
        conn.commit()
        conn.close()
        return jsonify({"success":True,"room_id":room_id,"pin":pin})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)})

@app.route("/student/rfid-check", methods=["POST"])
def rfid_check():
    try:
        data     = request.json
        rfid_uid = data.get("rfid_uid","").upper().strip()
        room_id  = data.get("room_id","")
        if not rfid_uid:
            return jsonify({"found":False,"reason":"no_rfid"})
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        conn.close()
        if not student:
            return jsonify({"found":False,
                            "reason":"not_registered",
                            "rfid_uid":rfid_uid})
        correct_room = student["assigned_room"] == room_id
        if not correct_room:
            log_access(room_id,student_id=student["id"],
                       event_type="entry",method="rfid",
                       status="DENY",deny_reason="wrong_room",
                       rfid_uid=rfid_uid)
        return jsonify({
            "found":True,"correct_room":correct_room,
            "student_name":student["name"],
            "roll_no":student["roll_no"],
            "assigned_room":student["assigned_room"],
            "seat_no":student["seat_no"],
            "current_room":room_id,
            "message":(
                f"Welcome {student['name']}! "
                f"Seat {student['seat_no']}. Look at camera."
                if correct_room else
                f"Wrong room! Go to {student['assigned_room']}.")
        })
    except Exception as e:
        return jsonify({"found":False,"error":str(e)})

@app.route("/faculty/auth", methods=["POST"])
def faculty_auth():
    try:
        data    = request.json
        room_id = data.get("room_id","")
        pin     = data.get("pin","")
        today   = date.today().isoformat()
        valid, reason = validate_pin_digits(pin)
        if not valid:
            return jsonify({"success":False,"reason":reason})
        conn    = get_db()
        session = conn.execute("""
            SELECT es.*, f.name, f.phone, f.faculty_id
            FROM exam_sessions es
            JOIN faculty f ON f.id=es.faculty_id
            WHERE es.room_id=? AND es.exam_date=?
        """, (room_id,today)).fetchone()
        if not session:
            conn.close()
            return jsonify({"success":False,
                            "reason":"no_session_today"})
        if session["faculty_pin"] != pin:
            log_access(room_id,faculty_id=session["faculty_id"],
                       event_type="faculty",method="faculty_pin",
                       status="DENY",deny_reason="wrong_pin")
            conn.close()
            return jsonify({"success":False,"reason":"invalid_pin"})

        # FIX: If session already active, return existing student_pin
        # instead of generating a new one — prevents double-submit
        # issue where second press generates different PIN
        if session["is_active"] and session["student_pin"]:
            existing_pin = session["student_pin"]
            conn.close()
            log.info(f"[FACULTY] {room_id} re-auth by "
                     f"{session['name']} — returning existing PIN")
            return jsonify({
                "success":True,
                "faculty_name":session["name"],
                "faculty_id":session["faculty_id"],
                "student_pin":existing_pin,
                "room_id":room_id,
                "subject":session["subject"],
                "sms_sent_to":session["phone"],
                "note":"session_already_active"})

        # First time activation — generate fresh PIN
        student_pin = generate_student_pin(session["id"])
        conn.execute("""
            UPDATE exam_sessions
            SET is_active=1,activated_at=?,student_pin=?
            WHERE id=?
        """, (datetime.now().isoformat(),student_pin,session["id"]))
        conn.commit()
        conn.close()
        log_access(room_id,faculty_id=session["faculty_id"],
                   event_type="faculty",method="faculty_pin",
                   status="SUCCESS")
        sms_msg = (
            f"SENTINEL Exam System\n"
            f"Welcome {session['name']}!\n"
            f"Room {room_id} | {session['subject']}\n"
            f"Student Emergency PIN: {student_pin}\n"
            f"Valid today only. Keep confidential.")
        threading.Thread(target=send_sms,
                         args=(session["phone"],sms_msg),
                         daemon=True).start()
        log.info(f"[FACULTY] {room_id} by {session['name']} "
                 f"| Student PIN: {student_pin}")
        return jsonify({
            "success":True,
            "faculty_name":session["name"],
            "faculty_id":session["faculty_id"],
            "student_pin":student_pin,
            "room_id":room_id,
            "subject":session["subject"],
            "sms_sent_to":session["phone"]})
    except Exception as e:
        log.error(f"Faculty auth error: {e}")
        return jsonify({"success":False,"error":str(e)})

@app.route("/student/auth/pin", methods=["POST"])
def auth_pin():
    try:
        data     = request.json
        room_id  = data.get("room_id","HALL-A")
        rfid_uid = data.get("rfid_uid","").upper()
        pin      = data.get("pin","")
        today    = date.today().isoformat()
        conn    = get_db()

        # Validate session is active
        session = conn.execute(
            "SELECT * FROM exam_sessions "
            "WHERE room_id=? AND exam_date=? AND is_active=1",
            (room_id,today)).fetchone()
        if not session:
            conn.close()
            return jsonify({"success":False,
                            "reason":"session_not_active"})

        # Validate PIN
        if session["student_pin"] != pin:
            conn.close()
            return jsonify({"success":False,"reason":"invalid_pin"})

        # Validate student exists and belongs to this room
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        if not student:
            conn.close()
            return jsonify({"success":False,
                            "reason":"student_not_found"})

        # FIX: Room check for PIN entry
        if student["assigned_room"] != room_id:
            conn.close()
            return jsonify({"success":False,
                            "reason":"wrong_room",
                            "assigned_room":student["assigned_room"]})

        # FIX: Check if student already entered today
        # Prevents PIN being used twice for same student
        already_entered = conn.execute("""
            SELECT id FROM access_logs
            WHERE student_id=? AND room_id=?
            AND exam_date=? AND event_type='entry'
            AND auth_status='SUCCESS'
        """, (student["id"],room_id,today)).fetchone()
        if already_entered:
            conn.close()
            return jsonify({"success":False,
                            "reason":"already_entered_today",
                            "student_name":student["name"]})

        conn.close()
        log_access(room_id,student_id=student["id"],
                   event_type="entry",method="student_pin",
                   status="SUCCESS",fallbacks=3,rfid_uid=rfid_uid)
        log.info(f"[PIN ENTRY] {student['name']} "
                 f"room={room_id} method=emergency_pin")
        return jsonify({"success":True,"method":"student_pin",
                        "student_name":student["name"],
                        "roll_no":student["roll_no"],
                        "seat_no":student["seat_no"]})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)})

@app.route("/student/exit-check", methods=["POST"])
def student_exit_check():
    try:
        data     = request.json
        rfid_uid = data.get("rfid_uid","").upper().strip()
        room_id  = data.get("room_id","")
        today    = date.today().isoformat()
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        if not student:
            conn.close()
            return jsonify({"found":False,"reason":"not_registered"})

        # Check already exited
        already_exited = conn.execute("""
            SELECT * FROM access_logs
            WHERE student_id=? AND room_id=?
            AND exam_date=? AND event_type='exit'
            AND auth_status='SUCCESS'
        """, (student["id"],room_id,today)).fetchone()
        if already_exited:
            conn.close()
            return jsonify({"found":True,"already_exited":True,
                            "student_name":student["name"]})

        # Find entry method
        entry_log = conn.execute("""
            SELECT auth_method FROM access_logs
            WHERE student_id=? AND room_id=?
            AND exam_date=? AND event_type='entry'
            AND auth_status='SUCCESS'
            ORDER BY logged_at DESC LIMIT 1
        """, (student["id"],room_id,today)).fetchone()
        conn.close()

        # FIX: If no entry found, don't default to rfid+face
        # Instead return a clear flag so WROOM can handle gracefully
        if not entry_log:
            log.warning(f"[EXIT] {student['name']} has no entry "
                        f"log for today in {room_id}")
            return jsonify({
                "found":True,"already_exited":False,
                "student_name":student["name"],
                "roll_no":student["roll_no"],
                "seat_no":student["seat_no"],
                "auth_method":"no_entry_today",
                "warning":"Student has no entry record today"})

        return jsonify({
            "found":True,"already_exited":False,
            "student_name":student["name"],
            "roll_no":student["roll_no"],
            "seat_no":student["seat_no"],
            "auth_method":entry_log["auth_method"]})
    except Exception as e:
        return jsonify({"found":False,"error":str(e)})

@app.route("/student/exit", methods=["POST"])
def student_exit():
    try:
        data        = request.json
        room_id     = data.get("room_id","HALL-A")
        rfid_uid    = data.get("rfid_uid","").upper()
        exit_method = data.get("exit_method","rfid")
        today       = date.today().isoformat()
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE rfid_uid=?",
            (rfid_uid,)).fetchone()
        if not student:
            conn.close()
            return jsonify({"success":False,"reason":"not_found"})
        conn.execute("""
            UPDATE access_logs SET exit_time=?
            WHERE student_id=? AND room_id=?
            AND exam_date=? AND event_type='entry'
            AND exit_time IS NULL
        """, (datetime.now().isoformat(),
              student["id"],room_id,today))
        conn.execute("""
            INSERT INTO access_logs
            (room_id,student_id,event_type,auth_method,auth_status)
            VALUES (?,?,'exit',?,?)
        """, (room_id,student["id"],exit_method,"SUCCESS"))
        conn.commit()
        conn.close()
        log.info(f"[EXIT] {student['name']} "
                 f"from {room_id} via {exit_method}")
        return jsonify({"success":True,
                        "student_name":student["name"],
                        "roll_no":student["roll_no"],
                        "exit_method":exit_method})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)})

@app.route("/api/face/verify", methods=["POST"])
def face_verify_cam():
    try:
        img_file    = request.files.get("image")
        meta        = json.loads(request.form.get("meta","{}"))
        room_id     = meta.get("room_id","HALL-A")
        auth_method = meta.get("auth_method","rfid+face")
        if not img_file:
            return jsonify({"match":False,"confidence":0,
                            "error":"no_image"})
        img_bytes = img_file.read()
        match, matched_roll, dist = verify_face(img_bytes)
        if not match:
            return jsonify({"match":False,"confidence":dist,
                            "student_id":matched_roll,
                            "correct_room":False,
                            "reason":"face_mismatch"})
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE roll_no=?",
            (matched_roll,)).fetchone()
        conn.close()
        if not student:
            return jsonify({"match":True,"confidence":dist,
                            "student_id":matched_roll,
                            "correct_room":False,
                            "reason":"student_not_in_db"})
        correct_room = student["assigned_room"] == room_id
        if correct_room:
            log_access(room_id,student_id=student["id"],
                       event_type="entry",method=auth_method,
                       status="SUCCESS")
        else:
            log_access(room_id,student_id=student["id"],
                       event_type="entry",method=auth_method,
                       status="DENY",deny_reason="wrong_room")
        return jsonify({
            "match":True,"confidence":dist,
            "student_id":matched_roll,
            "student_name":student["name"],
            "seat_no":student["seat_no"],
            "assigned_room":student["assigned_room"],
            "current_room":room_id,
            "correct_room":correct_room,
            "message":(
                f"Welcome {student['name']}! Seat {student['seat_no']}."
                if correct_room else
                f"Wrong room! Go to {student['assigned_room']}.")})
    except Exception as e:
        return jsonify({"match":False,"confidence":0,"error":str(e)})

@app.route("/api/qr/scan", methods=["POST"])
def qr_scan_cam():
    try:
        img_file = request.files.get("image")
        meta     = json.loads(request.form.get("meta","{}"))
        room_id  = meta.get("room_id","HALL-A")
        if not img_file:
            return jsonify({"valid":False,"error":"no_image"})
        img_bytes = img_file.read()
        nparr     = np.frombuffer(img_bytes, np.uint8)
        img       = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({"valid":False,"error":"decode_failed"})
        qr_data = decode_qr_from_frame(img)
        if not qr_data:
            return jsonify({"valid":False,"error":"no_qr_detected"})
        valid, roll_no, expired, reason = verify_qr(qr_data, room_id)
        return jsonify({"valid":valid,"expired":expired,
                        "student_id":roll_no,"reason":reason})
    except Exception as e:
        return jsonify({"valid":False,"error":str(e)})

@app.route("/api/anomaly/snapshot", methods=["POST"])
def anomaly_snapshot():
    try:
        img_data = request.data
        reason   = request.headers.get("X-Reason","trespass")
        room_id  = request.headers.get("X-Room","HALL-A")
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(
            SNAPSHOTS_DIR, f"alarm_{room_id}_{ts}.jpg")
        with open(filepath,"wb") as f: f.write(img_data)
        log_anomaly(room_id,"unauthorized_entry",
                    f"Trespass: {reason}",filepath)
        return jsonify({"saved":True,"path":filepath})
    except Exception as e:
        return jsonify({"saved":False,"error":str(e)})

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
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"status":"error","error":str(e)})

@app.route("/enroll/face", methods=["POST"])
def enroll_face():
    """Save a face photo for a student (used by capture_from_cam.py)"""
    try:
        roll_no    = request.form.get("roll_no","")
        face_image = request.files.get("face_image")
        photo_type = request.form.get("type","laptop")
        if not roll_no or not face_image:
            return jsonify({"status":"missing_data"})
        folder = os.path.join(FACES_DIR, roll_no)
        os.makedirs(folder, exist_ok=True)
        count  = len([f for f in os.listdir(folder)
                      if f.lower().endswith(
                          (".jpg",".jpeg",".png"))])
        prefix = "cam_" if photo_type == "cam" else ""
        path   = os.path.join(folder, f"{prefix}{count+1}.jpg")
        face_image.save(path)
        return jsonify({"status":"enrolled","roll_no":roll_no,
                        "photo_no":count+1,"path":path})
    except Exception as e:
        return jsonify({"status":"error","error":str(e)})

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
            return jsonify({"error":"not_found"}),404
        room_id = student["assigned_room"]
        if not room_id:
            conn.close()
            return jsonify({"error":"no_room"}),400
        payload, qr_hash = make_qr_hash(roll_no, room_id)
        full = f"{payload}:{qr_hash}"
        conn.execute("""
            INSERT OR IGNORE INTO qr_tokens
            (student_id,qr_hash,exam_start,exam_end,room_id)
            VALUES (?,?,?,?,?)
        """, (student["id"],qr_hash,
              EXAM_START_DATE,EXAM_END_DATE,room_id))
        conn.commit()
        conn.close()
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10, border=4)
        qr.add_data(full)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black",back_color="white")
        buf = io.BytesIO()
        img.save(buf,format="PNG")
        buf.seek(0)
        return send_file(buf,mimetype="image/png",
                         as_attachment=True,
                         download_name=f"qr_{roll_no}.png")
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.route("/admin/generate-hall-ticket/<roll_no>",
           methods=["GET"])
def generate_hall_ticket(roll_no):
    """
    Generate and download a complete hall ticket PDF
    with student details + QR code embedded.
    """
    try:
        import qrcode as qrcode_lib
        conn    = get_db()
        student = conn.execute(
            "SELECT * FROM students WHERE roll_no=?",
            (roll_no,)).fetchone()
        if not student:
            conn.close()
            return jsonify({"error":"not_found"}),404

        room_id = student["assigned_room"]
        today   = date.today().isoformat()

        # Get today's session for this student's room
        session = conn.execute("""
            SELECT es.*, f.name AS faculty_name
            FROM exam_sessions es
            LEFT JOIN faculty f ON f.id=es.faculty_id
            WHERE es.room_id=? AND es.exam_date=?
        """, (room_id, today)).fetchone()
        conn.close()

        # Generate QR
        payload, qr_hash = make_qr_hash(roll_no, room_id)
        full = f"{payload}:{qr_hash}"

        # Save QR token
        conn2 = get_db()
        conn2.execute("""
            INSERT OR IGNORE INTO qr_tokens
            (student_id,qr_hash,exam_start,exam_end,room_id)
            VALUES (?,?,?,?,?)
        """, (student["id"],qr_hash,
              EXAM_START_DATE,EXAM_END_DATE,room_id))
        conn2.commit()
        conn2.close()

        # Generate QR image bytes
        qr = qrcode_lib.QRCode(
            error_correction=qrcode_lib.constants.ERROR_CORRECT_M,
            box_size=10, border=4)
        qr.add_data(full)
        qr.make(fit=True)
        qr_img     = qr.make_image(fill_color="black",
                                    back_color="white")
        qr_buf     = io.BytesIO()
        qr_img.save(qr_buf, format="PNG")
        qr_img_bytes = qr_buf.getvalue()

        # Generate PDF
        pdf_path = generate_hall_ticket_pdf(
            dict(student),
            dict(session) if session else None,
            qr_img_bytes)

        return send_file(
            pdf_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"hall_ticket_{roll_no}.pdf")

    except Exception as e:
        log.error(f"Hall ticket error: {e}")
        return jsonify({"error":str(e)}),500

@app.route("/admin/logs/<room_id>", methods=["GET"])
def get_logs(room_id):
    try:
        limit = request.args.get("limit",100)
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
        """, (room_id,today,limit)).fetchall()
        conn.close()
        return jsonify({"logs":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.route("/admin/anomalies/<room_id>", methods=["GET"])
def get_anomalies(room_id):
    try:
        today = date.today().isoformat()
        conn  = get_db()
        rows  = conn.execute(
            "SELECT * FROM anomaly_events "
            "WHERE room_id=? AND exam_date=? "
            "ORDER BY occurred_at DESC",
            (room_id,today)).fetchall()
        conn.close()
        return jsonify({"anomalies":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.route("/admin/absentees/<room_id>", methods=["GET"])
def get_absentees(room_id):
    try:
        today = date.today().isoformat()
        conn  = get_db()
        rows  = conn.execute("""
            SELECT s.roll_no,s.name,s.seat_no
            FROM students s
            LEFT JOIN access_logs a
                ON a.student_id=s.id AND a.exam_date=?
                AND a.auth_status='SUCCESS'
                AND a.event_type='entry'
            WHERE s.assigned_room=? AND a.id IS NULL
            ORDER BY s.seat_no
        """, (today,room_id)).fetchall()
        conn.close()
        return jsonify({"room_id":room_id,"date":today,
                        "absentees":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.route("/admin/students", methods=["GET"])
def get_students():
    try:
        conn = get_db()
        rows = conn.execute("""
            SELECT id,roll_no,name,phone,rfid_uid,
                   assigned_room,seat_no,enrolled_at,
                   CASE WHEN face_vector IS NOT NULL
                        THEN 1 ELSE 0 END AS face_enrolled
            FROM students ORDER BY assigned_room,seat_no
        """).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.route("/admin/students", methods=["POST"])
def add_student():
    try:
        data = request.json
        conn = get_db()
        conn.execute("""
            INSERT INTO students
            (roll_no,name,phone,assigned_room,seat_no)
            VALUES (?,?,?,?,?)
        """, (data["roll_no"],data["name"],
              data.get("phone",""),
              data.get("assigned_room",""),
              data.get("seat_no","")))
        conn.commit()
        conn.close()
        return jsonify({"status":"created"})
    except Exception as e:
        return jsonify({"error":str(e)}),500

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
        return jsonify({"error":str(e)}),500

def ensure_today_sessions():
    conn  = get_db()
    today = date.today().isoformat()
    rooms = [
        ('HALL-A','Data Structures',  1,'1245'),
        ('HALL-B','Computer Networks',2,'4578'),
    ]
    for room_id,subject,fac_id,pin in rooms:
        conn.execute("""
            INSERT OR IGNORE INTO exam_sessions
            (room_id,subject,faculty_id,faculty_pin,exam_date)
            VALUES (?,?,?,?,?)
        """, (room_id,subject,fac_id,pin,today))
    conn.commit()
    conn.close()
    log.info(f"Sessions ensured for {today}")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 56)
    print("  SENTINEL — MFA EXAM SECURITY SYSTEM")
    print("  DeepFace Edition")
    print("=" * 56)
    print(f"  Server IP    : {SERVER_IP}")
    print(f"  Port         : {SERVER_PORT}")
    print(f"  Face engine  : DeepFace / {DEEPFACE_MODEL}")
    print(f"  Face threshold: {DEEPFACE_THRESHOLD} (cosine distance)")
    print(f"  QR dates     : {EXAM_START_DATE} → {EXAM_END_DATE}")
    print("=" * 56)

    init_db()
    ensure_today_sessions()
    train_faces()   # load LBPH as fallback

    print("  Loading DeepFace model (first time may take 30s)...")
    threading.Thread(target=init_deepface, daemon=False).start()

    mqtt_thread = threading.Thread(
        target=start_mqtt, daemon=True)
    mqtt_thread.start()

    print(f"  Running on : http://{SERVER_IP}:{SERVER_PORT}")
    print("=" * 56)

    app.run(host="0.0.0.0", port=SERVER_PORT,
            debug=False, threaded=True)