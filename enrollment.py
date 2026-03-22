#!/usr/bin/env python3
"""
MFA EXAM HALL — Enrollment Scripts
====================================
Run these ONCE per semester during enrollment day.

Usage:
  python enrollment.py faces     — capture faces from laptop camera
  python enrollment.py rfid      — import RFID from rfid_assignments.csv
  python enrollment.py seating   — import from seating_arrangement.csv
  python enrollment.py qr        — generate QR codes for all students
  python enrollment.py check     — run validation checks
  python enrollment.py addstudent — add single student interactively
"""

import os
import sys
import csv
import cv2
import json
import hashlib
import sqlite3
import numpy as np
from datetime import date, datetime, timedelta

# ─── DATABASE ─────────────────────────────────────────────
DB_PATH   = "exam.db"
FACES_DIR = "faces"
QR_DIR    = "qr_codes"
QR_SECRET = os.getenv("QR_SECRET",
            "examhall_qr_secret_2026!!")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades +
    "haarcascade_frontalface_default.xml"
)

# ══════════════════════════════════════════════════════════
# 1. CAPTURE FACES FROM LAPTOP CAMERA
# ══════════════════════════════════════════════════════════
def capture_faces(roll_no: str,
                  target: int = 30):
    """
    Opens laptop camera and auto-captures
    face photos for a student.
    """
    folder = os.path.join(FACES_DIR, roll_no)
    os.makedirs(folder, exist_ok=True)

    existing = len([
        f for f in os.listdir(folder)
        if f.endswith(".jpg")
    ])
    print(f"\nCapturing faces for {roll_no}")
    print(f"Already have {existing} photos.")
    print(f"Will capture {target} more.")
    print("Press Q to quit early.")

    cap   = cv2.VideoCapture(0)
    count = 0

    while count < target:
        ret, frame = cap.read()
        if not ret:
            break

        gray  = cv2.cvtColor(
            frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray, 1.3, 5)

        display = frame.copy()
        for (x, y, w, h) in faces:
            cv2.rectangle(
                display,
                (x, y), (x+w, y+h),
                (0, 255, 0), 2)
            cv2.putText(
                display,
                f"{roll_no} — {count}/{target}",
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (0, 255, 0), 2)

        cv2.imshow(
            "Face Capture — Q to quit",
            display)

        if len(faces) > 0:
            (x, y, w, h) = faces[0]
            roi = gray[y:y+h, x:x+w]
            roi = cv2.resize(roi, (200, 200))
            num  = existing + count + 1
            path = os.path.join(
                folder, f"{num}.jpg")
            cv2.imwrite(path, roi)
            count += 1
            print(f"  Captured {count}/{target}")
            cv2.waitKey(150)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    print(
        f"Done! {count} photos saved for "
        f"{roll_no} in {folder}/")
    print("Run 'python server.py' to retrain "
          "the face model.")

# ══════════════════════════════════════════════════════════
# 2. IMPORT RFID FROM CSV
# ══════════════════════════════════════════════════════════
def import_rfid(csv_file: str =
                "rfid_assignments.csv"):
    """
    CSV format: roll_no,rfid_uid
    Example:
      22BCS001,A3F209BE
      22BCS002,B1C340DE
    """
    if not os.path.exists(csv_file):
        # Create sample CSV
        with open(csv_file, "w") as f:
            f.write("roll_no,rfid_uid\n")
            f.write("22BCS001,A3F209BE\n")
            f.write("22BCS002,B1C340DE\n")
        print(
            f"Sample {csv_file} created. "
            f"Fill it in and run again.")
        return

    print(f"\n=== IMPORT RFID === "
          f"from {csv_file}\n")
    conn = get_db()
    ok, fail = 0, 0

    with open(csv_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            roll = row["roll_no"].strip()
            uid  = row["rfid_uid"].strip().upper()
            c    = conn.execute(
                "UPDATE students SET rfid_uid=?, "
                "updated_at=? WHERE roll_no=?",
                (uid,
                 datetime.now().isoformat(),
                 roll))
            if c.rowcount:
                print(f"  OK  {roll} → {uid}")
                ok += 1
            else:
                print(f"  NOT FOUND: {roll}")
                fail += 1

    conn.commit()
    conn.close()
    print(
        f"\nDone: {ok} updated, "
        f"{fail} not found\n")

# ══════════════════════════════════════════════════════════
# 3. IMPORT SEATING ARRANGEMENT
# ══════════════════════════════════════════════════════════
def import_seating(csv_file: str =
                   "seating_arrangement.csv"):
    """
    CSV format: roll_no,hall,seat_no
    Example:
      22BCS001,HALL-A,1
      22BCS002,HALL-A,2
    """
    if not os.path.exists(csv_file):
        with open(csv_file, "w") as f:
            f.write("roll_no,hall,seat_no\n")
            f.write("22BCS001,HALL-A,1\n")
            f.write("22BCS002,HALL-A,2\n")
        print(
            f"Sample {csv_file} created. "
            f"Fill it in and run again.")
        return

    print(f"\n=== IMPORT SEATING === "
          f"from {csv_file}\n")
    conn = get_db()
    ok   = 0

    with open(csv_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            conn.execute(
                "UPDATE students SET "
                "assigned_room=?, seat_no=?, "
                "updated_at=? WHERE roll_no=?",
                (row["hall"].strip(),
                 row["seat_no"].strip(),
                 datetime.now().isoformat(),
                 row["roll_no"].strip()))
            ok += 1

    conn.commit()
    conn.close()
    print(f"Done: {ok} students assigned\n")

# ══════════════════════════════════════════════════════════
# 4. GENERATE QR CODES
# ══════════════════════════════════════════════════════════
def generate_qr_batch():
    """
    Generate QR PNG for every student
    with a room assignment.
    """
    try:
        import qrcode
    except ImportError:
        print("Run: pip install qrcode[pil]")
        return

    os.makedirs(QR_DIR, exist_ok=True)
    today = date.today().isoformat()

    conn     = get_db()
    students = conn.execute("""
        SELECT id, roll_no, assigned_room
        FROM students
        WHERE assigned_room IS NOT NULL
    """).fetchall()

    print(
        f"\n=== GENERATE QR === "
        f"{len(students)} students\n")

    for s in students:
        roll_no = s["roll_no"]
        room_id = s["assigned_room"]

        payload  = (
            f"UUCMS:{roll_no}:"
            f"{room_id}:{today}")
        qr_hash  = hashlib.sha256(
            f"{payload}:{QR_SECRET}".encode()
        ).hexdigest()
        full_payload = f"{payload}:{qr_hash}"

        # Store token
        expires = (
            datetime.now() + timedelta(hours=24)
        ).isoformat()
        conn.execute("""
            INSERT OR IGNORE INTO qr_tokens
            (student_id, qr_hash, exam_date,
             room_id, expires_at)
            VALUES (?,?,?,?,?)
        """, (s["id"], qr_hash, today,
              room_id, expires))

        # Generate image
        qr = qrcode.QRCode(
            error_correction=
                qrcode.constants.ERROR_CORRECT_H,
            box_size=8, border=2)
        qr.add_data(full_payload)
        qr.make(fit=True)
        img = qr.make_image(
            fill_color="black",
            back_color="white")
        out = os.path.join(
            QR_DIR, f"qr_{roll_no}.png")
        img.save(out)
        print(f"  OK  {roll_no} → {out}")

    conn.commit()
    conn.close()
    print(
        f"\nDone! QR codes saved in "
        f"./{QR_DIR}/\n"
        f"Print and laminate on student "
        f"ID cards or admit cards.\n")

# ══════════════════════════════════════════════════════════
# 5. ADD STUDENT INTERACTIVELY
# ══════════════════════════════════════════════════════════
def add_student_interactive():
    print("\n=== ADD STUDENT ===\n")
    roll_no = input(
        "Roll number (e.g. 22BCS001): ").strip()
    name    = input("Full name: ").strip()
    phone   = input("Phone number: ").strip()
    room    = input(
        "Assigned room (e.g. HALL-A): ").strip()
    seat    = input("Seat number: ").strip()

    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO students
            (roll_no, name, phone,
             assigned_room, seat_no)
            VALUES (?,?,?,?,?)
        """, (roll_no, name, phone, room, seat))
        conn.commit()
        print(f"\nStudent {name} ({roll_no}) "
              f"added successfully!")
        print(f"Now run: "
              f"python enrollment.py faces "
              f"to capture their face photos.")
    except sqlite3.IntegrityError:
        print(f"Roll number {roll_no} "
              f"already exists!")
    conn.close()

# ══════════════════════════════════════════════════════════
# 6. VALIDATION CHECKS
# ══════════════════════════════════════════════════════════
def check_enrollment():
    print("\n=== ENROLLMENT VALIDATION ===\n")
    conn   = get_db()
    issues = 0

    # Total students
    total = conn.execute(
        "SELECT COUNT(*) FROM students"
    ).fetchone()[0]
    print(f"Total students in DB: {total}")

    # No RFID
    no_rfid = conn.execute(
        "SELECT COUNT(*) FROM students "
        "WHERE rfid_uid IS NULL"
    ).fetchone()[0]
    if no_rfid:
        print(
            f"  No RFID assigned: {no_rfid} students")
        issues += no_rfid
    else:
        print("  All students have RFID")

    # No face photos
    no_face = 0
    for row in conn.execute(
            "SELECT roll_no FROM students"
            ).fetchall():
        folder = os.path.join(
            FACES_DIR, row["roll_no"])
        if not os.path.exists(folder) or \
           len(os.listdir(folder)) < 5:
            no_face += 1
    if no_face:
        print(
            f"  No/few face photos: "
            f"{no_face} students "
            f"(need ≥5 each)")
        issues += no_face
    else:
        print("  All students have face photos")

    # No room
    no_room = conn.execute(
        "SELECT COUNT(*) FROM students "
        "WHERE assigned_room IS NULL"
    ).fetchone()[0]
    if no_room:
        print(
            f"  No room assigned: "
            f"{no_room} students")
    else:
        print("  All students have rooms")

    # Duplicate RFID
    dup = conn.execute("""
        SELECT rfid_uid, COUNT(*) c
        FROM students
        WHERE rfid_uid IS NOT NULL
        GROUP BY rfid_uid HAVING c > 1
    """).fetchall()
    if dup:
        print(
            f"  DUPLICATE RFID UIDs: {len(dup)}")
        for d in dup:
            print(
                f"    UID {d['rfid_uid']} "
                f"used {d['c']} times!")
        issues += len(dup)
    else:
        print("  No duplicate RFID UIDs")

    conn.close()

    if issues == 0:
        print(
            "\nAll checks passed! "
            "System ready for exam day.\n")
    else:
        print(
            f"\n{issues} issue(s) found. "
            f"Fix before exam day.\n")

# ─── CLI ──────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 \
          else "help"

    if cmd == "faces":
        roll = input(
            "Enter student roll number: ").strip()
        capture_faces(roll)
    elif cmd == "rfid":
        import_rfid()
    elif cmd == "seating":
        import_seating()
    elif cmd == "qr":
        generate_qr_batch()
    elif cmd == "check":
        check_enrollment()
    elif cmd == "addstudent":
        add_student_interactive()
    else:
        print(__doc__)