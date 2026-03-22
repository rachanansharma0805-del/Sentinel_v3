#!/usr/bin/env python3
"""
SENTINEL — Capture Face Photos from ESP32-CAM
==============================================
Instead of using laptop webcam, this captures training
photos directly from the ESP32-CAM via MQTT.

This solves the mismatch between training photos (laptop)
and recognition photos (ESP32-CAM).

Usage: python capture_from_cam.py
"""

import cv2
import os
import numpy as np
import time
import json
import paho.mqtt.client as mqtt
import requests

# ── CONFIG ────────────────────────────────────────────────────
SERVER_IP   = "192.168.154.107"
SERVER_PORT = 3000
MQTT_BROKER = "localhost"
MQTT_PORT   = 1883
FACES_DIR   = "faces"
FACE_SIZE   = (200, 200)
TARGET      = 60   # photos to capture per student

# ── FACE CASCADE ─────────────────────────────────────────────
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades +
    "haarcascade_frontalface_default.xml")

# ── MQTT ─────────────────────────────────────────────────────
snapshot_received = False
snapshot_path     = None

def on_message(client, userdata, msg):
    global snapshot_received, snapshot_path
    if msg.topic == "exam/anomaly":
        try:
            data = json.loads(msg.payload.decode())
            if data.get("saved"):
                snapshot_path     = data.get("path")
                snapshot_received = True
        except:
            pass

mqtt_client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_message = on_message

def connect_mqtt():
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT)
    mqtt_client.subscribe("exam/anomaly")
    mqtt_client.loop_start()
    print("[MQTT] Connected")

def trigger_snapshot():
    """Tell ESP32-CAM to take a snapshot"""
    global snapshot_received, snapshot_path
    snapshot_received = False
    snapshot_path     = None
    mqtt_client.publish("exam/cam/command",
                        json.dumps({"cmd": "snapshot",
                                    "reason": "enrollment"}))

def wait_for_snapshot(timeout=5):
    """Wait for snapshot to arrive"""
    start = time.time()
    while not snapshot_received:
        if time.time() - start > timeout:
            return None
        time.sleep(0.1)
    return snapshot_path

def preprocess_face(roi_gray):
    roi = cv2.resize(roi_gray, FACE_SIZE)
    roi = cv2.equalizeHist(roi)
    roi = cv2.GaussianBlur(roi, (3, 3), 0)
    return roi

def capture_from_cam(roll_no: str, target: int = TARGET):
    folder = os.path.join(FACES_DIR, roll_no)
    os.makedirs(folder, exist_ok=True)

    existing = len([
        f for f in os.listdir(folder)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ])

    print(f"\n{'='*50}")
    print(f"ESP32-CAM Face Capture — {roll_no}")
    print(f"{'='*50}")
    print(f"Existing photos : {existing}")
    print(f"Capturing       : {target} more")
    print(f"Total after     : {existing + target}")
    print(f"\nTips:")
    print(f"  - Stand 20-30cm from the ESP32-CAM")
    print(f"  - Good lighting on your face")
    print(f"  - Follow angle prompts")
    print(f"  - Stay still when prompted")
    print(f"\nPress ENTER to capture each photo")
    print(f"Type 'q' to quit early")
    print(f"{'='*50}\n")

    # Angle guides
    guides = [
        "Look STRAIGHT at camera",
        "Turn slightly LEFT",
        "Turn slightly RIGHT",
        "Tilt slightly UP",
        "Tilt slightly DOWN",
        "Normal expression — SMILE",
    ]

    count      = 0
    rejected   = 0
    guide_idx  = 0
    per_guide  = target // len(guides)

    while count < target:
        guide = guides[min(guide_idx, len(guides)-1)]
        print(f"\n[{count+1}/{target}] >> {guide}")
        print(f"Press ENTER to capture (or 'q' to quit): ", end="")

        user_input = input().strip().lower()
        if user_input == 'q':
            break

        print("  Capturing from ESP32-CAM...", end=" ")
        trigger_snapshot()
        path = wait_for_snapshot(timeout=6)

        if not path or not os.path.exists(path):
            print("FAILED — no snapshot received")
            rejected += 1
            continue

        # Load and process the snapshot
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            print("FAILED — could not read image")
            rejected += 1
            continue

        # Detect face in snapshot
        detected = face_cascade.detectMultiScale(
            img, scaleFactor=1.1,
            minNeighbors=4, minSize=(40, 40))

        if len(detected) == 0:
            # Try more lenient
            detected = face_cascade.detectMultiScale(
                img, scaleFactor=1.05,
                minNeighbors=3, minSize=(30, 30))

        if len(detected) == 0:
            print("FAILED — no face detected in frame")
            print("  → Move closer to the CAM and try again")
            rejected += 1
            continue

        # Use largest face
        x, y, w, h = max(detected, key=lambda r: r[2]*r[3])
        roi = preprocess_face(img[y:y+h, x:x+w])

        # Save to faces folder
        num      = existing + count + 1
        savepath = os.path.join(folder, f"{num}.jpg")
        cv2.imwrite(savepath, roi)
        count += 1

        print(f"OK! Saved {savepath}")

        # Update guide
        if count > 0 and count % per_guide == 0:
            guide_idx += 1

    print(f"\n{'='*50}")
    print(f"DONE! {count} photos captured")
    print(f"Total for {roll_no}: {existing + count} photos")
    print(f"Rejected: {rejected}")
    print(f"{'='*50}\n")
    return count

def retrain_model():
    """Retrain LBPH model after new photos captured"""
    print("\nRetraining face model...")

    recognizer = cv2.face.LBPHFaceRecognizer_create(
        radius=2, neighbors=8, grid_x=8, grid_y=8)
    face_labels = {}
    faces, labels = [], []
    label_id = 0

    if not os.path.exists(FACES_DIR):
        print("No faces directory!")
        return

    for roll_no in sorted(os.listdir(FACES_DIR)):
        folder = os.path.join(FACES_DIR, roll_no)
        if not os.path.isdir(folder):
            continue
        face_labels[label_id] = roll_no
        count = 0
        for f in os.listdir(folder):
            if not f.lower().endswith(
                    (".jpg", ".jpeg", ".png")):
                continue
            img = cv2.imread(
                os.path.join(folder, f),
                cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
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
        print(f"\nModel trained: {len(faces)} images, "
              f"{label_id} students")
        print("Saved as face_model.yml")
        print("Restart server.py to use new model!")
    else:
        print("No faces found!")

if __name__ == "__main__":
    print("\nSENTINEL — ESP32-CAM Face Capture")
    print("=" * 40)
    print("Make sure:")
    print("  1. server.py is running")
    print("  2. ESP32-CAM is powered and online")
    print("  3. mosquitto broker is running")
    print("=" * 40)

    connect_mqtt()
    time.sleep(1)

    roll = input("\nEnter student roll number: ").strip()
    if not roll:
        print("No roll number entered!")
        exit()

    captured = capture_from_cam(roll)

    if captured > 0:
        retrain = input(
            "\nRetrain model now? (y/n): "
        ).strip().lower()
        if retrain == 'y':
            retrain_model()
    else:
        print("No photos captured — nothing to retrain")

    mqtt_client.loop_stop()
