import sqlite3
from datetime import date

conn  = sqlite3.connect('exam.db')
today = date.today().isoformat()

# Show current state BEFORE reset
print(f"BEFORE reset — sessions for {today}:")
rows = conn.execute(
    "SELECT room_id, is_active, student_pin, activated_at "
    "FROM exam_sessions WHERE exam_date=?",
    (today,)).fetchall()
for r in rows:
    print(f"  {r[0]}: is_active={r[1]} pin={r[2]} activated={r[3]}")

# Force reset
affected = conn.execute(
    "UPDATE exam_sessions "
    "SET is_active=0, student_pin=NULL, activated_at=NULL "
    "WHERE exam_date=?",
    (today,)).rowcount
conn.commit()
print(f"\nReset {affected} session(s)")

# Verify AFTER reset
print(f"\nAFTER reset — sessions for {today}:")
rows = conn.execute(
    "SELECT room_id, is_active, student_pin "
    "FROM exam_sessions WHERE exam_date=?",
    (today,)).fetchall()
for r in rows:
    print(f"  {r[0]}: is_active={r[1]} pin={r[2]}")

conn.close()
print("\nDone! NOW restart server.py, then press C on keypad.")