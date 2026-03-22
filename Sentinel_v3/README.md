# MFA Exam Hall Security System – Sentinel_v3

## 📌 Overview

The **MFA Exam Hall Security System** is a multi-factor authentication based exam entry and exit monitoring system designed to replace manual attendance with a fully automated and secure solution.

The system uses **ESP32 WROOM-32 as the main controller, ESP32-CAM for face recognition and QR scanning, Flask backend with SQLite database, and a React dashboard for live monitoring**.

Each student entry and exit is verified using multiple authentication methods and stored with timestamp and photo evidence.

---

## 🎯 Objectives

* Prevent impersonation in exams
* Automate attendance logging
* Track entry and exit of students
* Detect trespassing
* Provide live monitoring dashboard
* Store authentication logs securely

---

## 🧠 System Architecture

Components used:

* ESP32 WROOM-32 (Main controller)
* ESP32-CAM (Face + QR scanning)
* RFID RC522 reader
* 4×3 Keypad
* OLED SSD1306 display
* HC-SR04 ultrasonic sensor
* Buzzer + LED indicators
* Flask backend server
* SQLite database
* React dashboard

Communication:

* HTTP over WiFi
* MQTT for camera events
* SQLite local database
* React → Flask API → Database

---

## 🔐 Authentication Methods

Primary authentication:

* RFID + Face recognition

Fallback methods:

* RFID + QR
* Face + QR
* PIN verification

Faculty authentication:

* PIN activation

Exit logging:

* Student exit request → DB update

Trespass detection:

* Ultrasonic sensor trigger → snapshot → anomaly log

---

## 📂 Project Structure

```
exam_backend/
│
├── Testing codes/
├── exam_dashboard/
├── hall_tickets/
├── data/
├── faces/
├── rfid_uid_registration/
├── esp32_cam_newfinal/
├── WROOM_v7_smartexit/
│
├── server.py
├── capture_faces_v3.py
├── reset_session.py
├── test_qr_scanner.py
│
└── README.md
```

---

## 🗄 Database

Database file:

```
exam.db
```

Tables created automatically:

* students
* faculty
* exam_sessions
* access_logs
* anomaly_events
* qr_tokens
* admin_users

Database is created on first run of `server.py`.

---

## ⚙️ Setup Instructions

### 1. Clone repository

```
git clone https://github.com/rachanansharma0805-del/Sentinel_v3.git
cd exam_backend
```

### 2. Start backend

```
python server.py
```

Database will be created automatically.

---

### 3. Start dashboard

```
cd exam_dashboard
npm install
npm run dev
```

---

### 4. Upload ESP32 code

Open `.ino` files in Arduino IDE and upload to:

* ESP32 WROOM-32
* ESP32-CAM

Update:

* WiFi SSID
* Password
* Server IP

---

### 5. Start MQTT (if used)

```
net start mosquitto
```

---

## 🔄 Authentication Flow

Faculty:

```
Key C → Enter PIN → Activate session
```

Student:

```
Key A → RFID → Face scan → Entry allowed
```

Fallback:

```
RFID + QR
Face + QR
PIN
```

Trespass:

```
Ultrasonic detect → Snapshot → Log anomaly
```

Exit:

```
Student exit → DB update exit_time
```

---

## 📡 API Endpoints

Examples:

* /student/auth
* /student/rfid-check
* /student/exit
* /faculty/auth
* /api/face/verify
* /api/qr/scan
* /api/anomaly/snapshot
* /admin/logs
* /admin/students
* /admin/anomalies

Backend: Flask
Database: SQLite
Frontend: React

---

## 🧪 Demo Steps

1. Start backend
2. Start dashboard
3. Power ESP32
4. Scan RFID
5. Face verification
6. Check dashboard logs
7. Test trespass detection

---

## 👩‍💻 Author

Rachana Sharma
MFA Exam Security System
Sentinel_v3 – 2026

---
