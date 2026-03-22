#!/usr/bin/env python3
"""
SENTINEL — Face Capture Script v3
===================================
Works with DeepFace — captures high quality reference photos.

Two modes:
1. LAPTOP WEBCAM mode  — use your laptop camera (quick setup)
2. ESP32-CAM mode      — captures via the actual exam camera
                         (recommended — eliminates camera mismatch)

For DeepFace you only need 5-10 good photos per student.
But 30-60 photos give better robustness.

Usage:
  python capture_faces_v3.py
"""

import cv2
import os
import numpy as np
import time
import json
import threading

# ── CONFIG ────────────────────────────────────────────────────
FACES_DIR      = "faces"
FACE_SIZE      = (200, 200)
BLUR_THRESHOLD = 80   # reject blurry frames

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades +
    "haarcascade_frontalface_default.xml")

ANGLE_GUIDES = [
    (5,  "Look STRAIGHT at camera"),
    (5,  "Turn slightly LEFT"),
    (5,  "Turn slightly RIGHT"),
    (5,  "Tilt slightly UP"),
    (5,  "Tilt slightly DOWN"),
    (5,  "Normal expression — SMILE"),
    (10, "Any angle — move naturally"),
]

# ── HELPERS ───────────────────────────────────────────────────
def is_blurry(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) \
           if len(image.shape) == 3 else image
    return cv2.Laplacian(gray, cv2.CV_64F).var() < BLUR_THRESHOLD

def preprocess_face(roi_gray):
    """
    For DeepFace: minimal preprocessing.
    DeepFace works better on natural images, not heavily processed.
    We only resize and apply mild histogram equalization.
    """
    roi = cv2.resize(roi_gray, FACE_SIZE)
    roi = cv2.equalizeHist(roi)
    return roi

def save_full_color(frame, folder, num):
    """
    Save full color image for DeepFace.
    DeepFace uses color channels unlike LBPH which is grayscale.
    """
    path = os.path.join(folder, f"{num}.jpg")
    cv2.imwrite(path, frame)
    return path

# ══════════════════════════════════════════════════════════════
# MODE 1: LAPTOP WEBCAM CAPTURE
# ══════════════════════════════════════════════════════════════
def capture_laptop_webcam(roll_no: str, target: int = 40):
    """
    Capture face photos using laptop webcam.
    Good for initial enrollment.
    Saves both color (for DeepFace) and grayscale (for LBPH fallback).
    """
    folder = os.path.join(FACES_DIR, roll_no)
    os.makedirs(folder, exist_ok=True)

    existing = len([
        f for f in os.listdir(folder)
        if f.lower().endswith((".jpg",".jpeg",".png"))])

    print(f"\n{'='*52}")
    print(f"SENTINEL Face Capture v3 — {roll_no}")
    print(f"Mode: Laptop Webcam")
    print(f"{'='*52}")
    print(f"Existing photos : {existing}")
    print(f"Capturing       : {target} more")
    print(f"\nTips for BEST results with DeepFace:")
    print(f"  - Good even lighting on your face")
    print(f"  - Face fills the green box")
    print(f"  - Vary your angles as instructed")
    print(f"  - Natural expressions work better")
    print(f"  - Avoid harsh shadows")
    print(f"\nPress Q to quit early")
    print(f"{'='*52}\n")
    time.sleep(2)

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    count      = 0
    rejected   = 0
    guide_idx  = 0
    guide_count= 0

    while count < target:
        ret, frame = cap.read()
        if not ret: break

        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray, scaleFactor=1.1,
            minNeighbors=5, minSize=(80,80))

        display = frame.copy()

        guide_text = (ANGLE_GUIDES[guide_idx][1]
                      if guide_idx < len(ANGLE_GUIDES)
                      else "Any angle")

        progress = int((count / target) * 38)
        bar = "[" + "█" * progress + "░" * (38-progress) + "]"

        cv2.putText(display, f"Roll: {roll_no}",
                    (10,25), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255,255,255), 2)
        cv2.putText(display, f"{count}/{target} {bar}",
                    (10,55), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,(0,255,0), 1)
        cv2.putText(display, f">> {guide_text}",
                    (10,85), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,(0,255,255), 2)
        cv2.putText(display,
                    f"Rejected(blurry): {rejected}",
                    (10,115), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,(0,100,255), 1)

        for (x,y,w,h) in faces:
            roi   = frame[y:y+h, x:x+w]
            blurry = is_blurry(roi)
            color  = (0,0,255) if blurry else (0,255,0)
            label  = "BLURRY" if blurry else "GOOD"

            cv2.rectangle(display, (x,y), (x+w,y+h), color, 2)
            cv2.putText(display, label,
                        (x, y-10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, color, 2)

            if not blurry and len(faces) == 1:
                num  = existing + count + 1
                path = os.path.join(folder, f"{num}.jpg")
                # Save COLOR image for DeepFace
                cv2.imwrite(path, frame)
                count += 1

                guide_count += 1
                if guide_idx < len(ANGLE_GUIDES):
                    if guide_count >= ANGLE_GUIDES[guide_idx][0]:
                        guide_count = 0
                        guide_idx  += 1
                        if guide_idx < len(ANGLE_GUIDES):
                            print(f"\n>> Next: "
                                  f"{ANGLE_GUIDES[guide_idx][1]}")

                print(f"  Captured {count}/{target} — {guide_text}")

                # Flash
                cv2.rectangle(display,(0,0),
                              (display.shape[1],display.shape[0]),
                              (255,255,255),3)
                cv2.imshow("Face Capture v3 — Q to quit", display)
                cv2.waitKey(120)
                continue
            elif len(faces) == 1 and blurry:
                rejected += 1

        cv2.imshow("Face Capture v3 — Q to quit", display)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

    total = existing + count
    print(f"\n{'='*52}")
    print(f"DONE! {count} photos captured")
    print(f"Total for {roll_no}: {total} photos")
    print(f"Rejected (blurry): {rejected}")
    print(f"Saved in: {folder}/")
    print(f"\nFor DeepFace: photos are ready to use!")
    print(f"For LBPH fallback: run retrain option below")
    print(f"{'='*52}\n")
    return count

# ══════════════════════════════════════════════════════════════
# MODE 2: ESP32-CAM CAPTURE (Recommended)
# ══════════════════════════════════════════════════════════════
def capture_from_espcam(roll_no: str, target: int = 30):
    """
    Capture training photos directly from ESP32-CAM via MQTT.
    This eliminates the camera mismatch problem completely.
    DeepFace will then compare live captures against photos
    taken by the same camera.

    Requires: server.py running, ESP32-CAM online, Mosquitto running
    """
    try:
        import paho.mqtt.client as mqtt_lib
    except ImportError:
        print("paho-mqtt not installed. Run: pip install paho-mqtt")
        return 0

    folder = os.path.join(FACES_DIR, roll_no)
    os.makedirs(folder, exist_ok=True)

    existing = len([
        f for f in os.listdir(folder)
        if f.lower().endswith((".jpg",".jpeg",".png"))])

    print(f"\n{'='*52}")
    print(f"SENTINEL Face Capture v3 — {roll_no}")
    print(f"Mode: ESP32-CAM via MQTT")
    print(f"{'='*52}")
    print(f"Existing photos : {existing}")
    print(f"Capturing       : {target} more")
    print(f"\nMake sure:")
    print(f"  - server.py is running")
    print(f"  - ESP32-CAM is powered and online")
    print(f"  - Mosquitto broker is running")
    print(f"  - Stand 20-30cm from the CAM")
    print(f"\nPress ENTER to capture each photo")
    print(f"Type 'q' to quit early")
    print(f"{'='*52}\n")

    # MQTT setup
    snapshot_event = threading.Event()
    snapshot_path  = [None]
    SNAPSHOTS_DIR  = "alarm_photos"

    def on_message(client, userdata, msg):
        # server.py publishes exam/anomaly/alert with full path
        if msg.topic == "exam/anomaly/alert":
            try:
                data = json.loads(msg.payload.decode())
                path = data.get("photo_path") or data.get("path")
                if path:
                    snapshot_path[0] = path
                    snapshot_event.set()
                    return
            except:
                pass
        # CAM publishes exam/anomaly with saved=true
        # We compute path ourselves from the timestamp pattern
        if msg.topic == "exam/anomaly":
            try:
                data = json.loads(msg.payload.decode())
                if data.get("saved"):
                    # Path pattern: alarm_photos/alarm_HALL-A_YYYYMMDD_HHMMSS.jpg
                    # Find the most recently created file in alarm_photos/
                    import glob, os as _os
                    files = glob.glob("alarm_photos/alarm_*.jpg")
                    if files:
                        latest = max(files, key=_os.path.getmtime)
                        snapshot_path[0] = latest
                        snapshot_event.set()
            except:
                pass

    client = mqtt_lib.Client(
        callback_api_version=mqtt_lib.CallbackAPIVersion.VERSION2)
    client.on_message = on_message

    try:
        client.connect("localhost", 1883)
        client.subscribe("exam/anomaly")
        client.subscribe("exam/anomaly/alert")
        client.loop_start()
        print("[MQTT] Connected to broker")
    except Exception as e:
        print(f"[MQTT] Cannot connect: {e}")
        print("Make sure Mosquitto is running!")
        return 0

    guides = [g[1] for g in ANGLE_GUIDES]
    per_guide = max(1, target // len(guides))

    count     = 0
    rejected  = 0
    guide_idx = 0

    while count < target:
        guide = guides[min(guide_idx, len(guides)-1)]
        print(f"\n[{count+1}/{target}] >> {guide}")
        print(f"Stand still, then press ENTER to capture "
              f"(or 'q' to quit): ", end="")

        user_input = input().strip().lower()
        if user_input == 'q':
            break

        # Trigger snapshot from ESP32-CAM
        snapshot_event.clear()
        snapshot_path[0] = None
        client.publish("exam/cam/command",
                       json.dumps({"cmd":"snapshot",
                                   "reason":"enrollment"}))

        print("  Waiting for ESP32-CAM snapshot...", end=" ")
        got_snapshot = snapshot_event.wait(timeout=12)

        if not got_snapshot or not snapshot_path[0]:
            print("TIMEOUT — no snapshot received")
            print("  Check: Is ESP32-CAM online? "
                  "Is server.py running?")
            rejected += 1
            continue

        path = snapshot_path[0]
        if not os.path.exists(path):
            print(f"FAILED — file not found: {path}")
            rejected += 1
            continue

        # Check face is in snapshot
        img = cv2.imread(path)
        if img is None:
            print("FAILED — could not read image")
            rejected += 1
            continue

        gray     = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        detected = face_cascade.detectMultiScale(
            gray, 1.1, 4, minSize=(40,40))
        if len(detected) == 0:
            detected = face_cascade.detectMultiScale(
                gray, 1.05, 3, minSize=(30,30))

        if len(detected) == 0:
            print("FAILED — no face in frame")
            print("  Move closer to the CAM and try again")
            rejected += 1
            continue

        # Copy to faces folder with cam_ prefix
        num      = existing + count + 1
        savepath = os.path.join(folder, f"cam_{num}.jpg")
        import shutil
        shutil.copy2(path, savepath)
        count += 1

        print(f"OK → saved as cam_{num}.jpg")

        # Update guide
        if count > 0 and count % per_guide == 0:
            guide_idx += 1

    client.loop_stop()
    client.disconnect()

    print(f"\n{'='*52}")
    print(f"DONE! {count} ESP32-CAM photos captured")
    print(f"Total for {roll_no}: {existing+count} photos")
    print(f"Rejected: {rejected}")
    print(f"Photos saved with 'cam_' prefix")
    print(f"{'='*52}\n")
    return count

# ══════════════════════════════════════════════════════════════
# LBPH RETRAIN (fallback model)
# ══════════════════════════════════════════════════════════════
def retrain_lbph():
    """Retrain LBPH model as fallback for when DeepFace is unavailable."""
    print("\nRetraining LBPH fallback model...")

    recognizer  = cv2.face.LBPHFaceRecognizer_create(
        radius=2, neighbors=8, grid_x=8, grid_y=8)
    face_labels = {}
    faces, labels = [], []
    label_id = 0

    if not os.path.exists(FACES_DIR):
        print("No faces directory found!")
        return

    for roll_no in sorted(os.listdir(FACES_DIR)):
        folder = os.path.join(FACES_DIR, roll_no)
        if not os.path.isdir(folder): continue

        face_labels[label_id] = roll_no
        count = 0

        for f in os.listdir(folder):
            if not f.lower().endswith(
                    (".jpg",".jpeg",".png")): continue
            img = cv2.imread(
                os.path.join(folder, f),
                cv2.IMREAD_GRAYSCALE)
            if img is None: continue
            img = cv2.resize(img, FACE_SIZE)
            img = cv2.equalizeHist(img)
            faces.append(img)
            labels.append(label_id)
            count += 1

        print(f"  {roll_no}: {count} photos loaded")
        label_id += 1

    if faces:
        recognizer.train(faces, np.array(labels))
        recognizer.save("face_model.yml")
        print(f"\nLBPH model trained: {len(faces)} images, "
              f"{label_id} students")
        print("Saved as face_model.yml")
        print("Restart server.py to use new model!")
    else:
        print("No face images found!")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\nSENTINEL Face Capture v3")
    print("=" * 40)
    print("DeepFace Edition — Color photos, higher accuracy")
    print("=" * 40)

    roll = input("\nEnter student roll number: ").strip()
    if not roll:
        print("No roll number entered!")
        exit()

    print("\nCapture mode:")
    print("  1 = Laptop webcam (quick, good for testing)")
    print("  2 = ESP32-CAM via MQTT (recommended for deployment)")
    mode = input("Choose (1 or 2): ").strip()

    if mode == "2":
        captured = capture_from_espcam(roll, target=30)
    else:
        captured = capture_laptop_webcam(roll, target=40)

    if captured > 0:
        retrain = input(
            "\nRetrain LBPH fallback model? (y/n): "
        ).strip().lower()
        if retrain == 'y':
            retrain_lbph()
        print("\nFor DeepFace: no retraining needed!")
        print("Just restart server.py — it will use "
              "your new photos automatically.")
    else:
        print("No photos captured.")