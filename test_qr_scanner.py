#!/usr/bin/env python3
"""
SENTINEL — QR Scanner Test
============================
Tests QR scanning via laptop webcam independently.
Run this to verify QR scanning works before integrating.

Usage: python test_qr_scanner.py
"""

import cv2
import json
import hashlib
import numpy as np
from datetime import date

QR_SECRET       = "examhall_qr_secret_2026!!"
EXAM_START_DATE = "2026-03-16"
EXAM_END_DATE   = "2026-04-30"

def decode_qr(frame):
    """Try multiple strategies to decode QR"""
    det     = cv2.QRCodeDetector()
    qr_data = None

    # Try pyzbar first
    try:
        from pyzbar import pyzbar
        barcodes = pyzbar.decode(frame)
        if barcodes:
            return barcodes[0].data.decode('utf-8')
    except:
        pass

    # Strategy 1: raw
    qr_data, _, _ = det.detectAndDecode(frame)
    if qr_data: return qr_data

    # Strategy 2: grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    qr_data, _, _ = det.detectAndDecode(gray)
    if qr_data: return qr_data

    # Strategy 3: threshold
    _, thresh = cv2.threshold(
        gray, 0, 255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    qr_data, _, _ = det.detectAndDecode(thresh)
    if qr_data: return qr_data

    # Strategy 4: sharpen
    kernel = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
    sharp  = cv2.filter2D(gray, -1, kernel)
    qr_data, _, _ = det.detectAndDecode(sharp)
    if qr_data: return qr_data

    # Strategy 5: upscale
    up = cv2.resize(frame, None, fx=2, fy=2,
                    interpolation=cv2.INTER_CUBIC)
    qr_data, _, _ = det.detectAndDecode(up)
    if qr_data: return qr_data

    return None

def verify_qr(qr_data, room_id="HALL-A"):
    """Verify decoded QR data"""
    try:
        parts = qr_data.split(":")
        if len(parts) < 6:
            return False, "invalid_format"

        prefix     = parts[0]
        roll_no    = parts[1]
        qr_room    = parts[2]
        exam_start = parts[3]
        exam_end   = parts[4]
        qr_hash    = parts[5]

        if prefix != "UUCMS":
            return False, "invalid_prefix"

        # Tamper check
        payload  = (f"UUCMS:{roll_no}:{qr_room}:"
                    f"{exam_start}:{exam_end}")
        expected = hashlib.sha256(
            f"{payload}:{QR_SECRET}".encode()
        ).hexdigest()

        if qr_hash != expected:
            return False, "tampered"

        if qr_room != room_id:
            return False, f"wrong_room (QR={qr_room})"

        today = date.today()
        start = date.fromisoformat(exam_start)
        end   = date.fromisoformat(exam_end)

        if today < start:
            return False, "exam_not_started"
        if today > end:
            return False, "exam_series_ended"

        return True, f"VALID — {roll_no} for {qr_room}"

    except Exception as e:
        return False, str(e)

def main():
    print("\nSENTINEL — QR Scanner Test")
    print("=" * 40)
    print("Hold your QR code to the webcam")
    print("Press Q to quit")
    print("=" * 40)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: Cannot open webcam!")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    last_result = ""
    result_time = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        display = frame.copy()
        h, w    = display.shape[:2]
        cx, cy  = w // 2, h // 2
        box     = 200

        # Draw guide box
        cv2.rectangle(display,
                      (cx-box, cy-box),
                      (cx+box, cy+box),
                      (0, 255, 0), 2)

        # Instructions
        cv2.putText(display,
                    "Hold QR inside the green box",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 255), 2)

        # Try decode
        qr_data = decode_qr(frame)

        if qr_data and qr_data != last_result:
            last_result = qr_data
            valid, msg  = verify_qr(qr_data)
            result_time = cv2.getTickCount()

            print(f"\n[QR DECODED]")
            print(f"  Data  : {qr_data[:80]}")
            print(f"  Result: {'✓ ' + msg if valid else '✗ ' + msg}")

        # Show result on screen
        if last_result:
            import time
            elapsed = (cv2.getTickCount() -
                       result_time) / cv2.getTickFrequency()
            if elapsed < 3:  # show for 3 seconds
                valid, msg = verify_qr(last_result)
                color  = (0, 255, 0) if valid else (0, 0, 255)
                status = "VALID!" if valid else "INVALID!"
                cv2.putText(display, status,
                            (cx-60, cy-box-20),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            1.2, color, 3)
                cv2.putText(display,
                            msg[:50],
                            (10, h-20),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, color, 2)
            else:
                last_result = ""

        cv2.imshow("SENTINEL QR Scanner — Q to quit",
                   display)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("\nDone!")

if __name__ == "__main__":
    main()
