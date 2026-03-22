import cv2
import os

def capture_faces(student_id):
    # Create folder
    folder = f"faces/{student_id}"
    os.makedirs(folder, exist_ok=True)
    
    cap = cv2.VideoCapture(0)
    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades +
        'haarcascade_frontalface_default.xml'
    )
    
    count = 0
    print(f"Capturing faces for {student_id}")
    print("Press SPACE to capture, Q to quit")
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        gray = cv2.cvtColor(frame,
                            cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray, 1.3, 5)
        
        # Draw rectangle around face
        for (x, y, w, h) in faces:
            cv2.rectangle(frame,
                         (x, y),
                         (x+w, y+h),
                         (0, 255, 0), 2)
            cv2.putText(frame,
                       f"Photos: {count}/30",
                       (x, y-10),
                       cv2.FONT_HERSHEY_SIMPLEX,
                       0.5, (0,255,0), 1)
        
        cv2.imshow('Capture Faces - '
                   'SPACE=capture Q=quit', frame)
        
        key = cv2.waitKey(1) & 0xFF
        
        # Auto capture when face detected
        if len(faces) > 0:
            (x, y, w, h) = faces[0]
            face_roi = gray[y:y+h, x:x+w]
            face_roi = cv2.resize(face_roi,
                                  (200, 200))
            path = f"{folder}/{count}.jpg"
            cv2.imwrite(path, face_roi)
            count += 1
            print(f"Captured {count}/30")
            cv2.waitKey(100)
        
        if key == ord('q') or count >= 30:
            break
    
    cap.release()
    cv2.destroyAllWindows()
    print(f"Done! {count} photos saved "
          f"for {student_id}")

# Capture for each student
student_id = input("Enter student UUCMS ID: ")
capture_faces(student_id)
