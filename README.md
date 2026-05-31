# Class Attendance Portal — Backend Master Plan

---

## 1. Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Framework | Django 5.x + Django REST Framework |
| Database | PostgreSQL 16 |
| Auth | JWT via `djangorestframework-simplejwt` |
| Cache / Sessions | Redis (live attendance sessions) |
| Offline QR Server | Separate minimal FastAPI app (runs on teacher's device) |
| Export | `openpyxl` (xlsx), `reportlab` (pdf), `python-docx` (docx), `csv` (built-in) |
| Task Queue | Celery + Redis (optional, for export jobs) |
| Environment | `python-decouple` / `.env` |

---

## 2. Project Structure

```
attendance_portal/
├── config/                  # Django project settings
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py
│   └── wsgi.py
├── apps/
│   ├── users/               # User model, auth, profiles
│   ├── academic/            # Semester, Course, Classroom, CourseInfo
│   ├── attendance/          # AttendanceLog, Session, DeviceBinding
│   ├── hardware/            # ESP32 webhook, fingerprint registration
│   └── reports/             # Export (xlsx, pdf, docx, csv)
├── offline_server/          # Minimal FastAPI server (separate, for QR fallback)
│   ├── main.py
│   └── requirements.txt
├── manage.py
├── requirements.txt
└── .env
```

---

## 3. Database Schema (Normalized — Django ORM)

### 3.1 Users App

```
User (AbstractUser)
├── id (UUID, PK)
├── username
├── email (unique)
├── password (Django standard hashing — PBKDF2+SHA256)
├── role: ENUM [STUDENT, TEACHER, ADMIN]
├── faculty (str)
├── department (str)
├── is_verified (bool, default False — admin must verify)
└── is_active

StudentProfile
├── id (UUID, PK)
├── user (OneToOne → User)
├── student_id (int, unique, e.g. 2302001)
├── current_level (str, e.g. "Third")
├── current_semester (str, e.g. "I")
└── hardware_finger_id (int, nullable, unique) ← set by hardware enrollment

TeacherProfile
├── id (UUID, PK)
├── user (OneToOne → User)
├── employee_id (str, unique)
├── faculty (str)
└── department (str)

AdminProfile
├── id (UUID, PK)
└── user (OneToOne → User)

DeviceBinding  ← QR attendance device lock
├── id (UUID, PK)
├── student (FK → StudentProfile)
├── mac_address (str, unique)
├── bound_at (datetime)
└── is_active (bool)  ← admin can deactivate to unbind
```

### 3.2 Academic App

```
Semester
├── id (UUID, PK)
├── level (str, e.g. "Third")
├── semester (str, e.g. "I")
├── start_date (date, nullable)
├── end_date (date, nullable)
├── is_active (bool)
└── deleted (bool, default False)

Course
├── id (UUID, PK)
├── code (str, unique, e.g. "CSE 301")
├── title (str)
├── content (str, nullable)
├── credits (ENUM: CREDIT_1_00 / CREDIT_1_50 / CREDIT_2_00 / CREDIT_3_00)
├── faculty (str)
├── department (str)
└── deleted (bool)

Classroom  ← a batch of students for a semester
├── id (UUID, PK)
├── name (str)
├── semester (FK → Semester)
└── deleted (bool)

StudentClassroom  ← join: which students are in which classroom
├── student (FK → StudentProfile)
└── classroom (FK → Classroom)
   PK: (student, classroom)

CourseInfo  ← one course, one teacher, one semester instance
├── id (UUID, PK)
├── course (FK → Course)
├── teacher (FK → TeacherProfile)
├── semester (FK → Semester)
├── classroom (FK → Classroom)
└── deleted (bool)
```

### 3.3 Attendance App

```
AttendanceSession  ← teacher starts this to open a window
├── id (UUID, PK)
├── course_info (FK → CourseInfo)
├── date (date)
├── mode: ENUM [FINGERPRINT, QR_ONLINE, QR_OFFLINE]
├── started_at (datetime)
├── ended_at (datetime, nullable)
├── is_active (bool)
├── duration_seconds (int)
└── qr_token (str, nullable)  ← signed token embedded in QR

AttendanceLog  ← one row per student per session
├── id (UUID, PK)
├── session (FK → AttendanceSession)
├── student (FK → StudentProfile)
├── course_info (FK → CourseInfo)
├── date (date)
├── time (time)
├── status: ENUM [PRESENT, ABSENT, LATE]
├── source: ENUM [HARDWARE, QR_ONLINE, QR_OFFLINE, MANUAL]
├── is_modified_by_teacher (bool, default False)
├── notes (str, nullable)
└── UNIQUE: (session, student)
```

### 3.4 Hardware App

```
HardwareDevice
├── id (UUID, PK)
├── device_name (str)
├── api_key (str, unique)  ← ESP32 authenticates with this
├── last_ping (datetime)
└── is_active (bool)

FingerprintEnrollmentRequest  ← student initiates, hardware fulfills
├── id (UUID, PK)
├── student (FK → StudentProfile)
├── hardware_device (FK → HardwareDevice)
├── status: ENUM [PENDING, COMPLETED, FAILED]
├── created_at (datetime)
└── completed_at (datetime, nullable)
```

---

## 4. API Routes (Complete)

### Auth
```
POST   /api/auth/login/                          Public
POST   /api/auth/refresh/                        Public
POST   /api/auth/register/                       Public
GET    /api/auth/me/                             Authenticated
```

### Config (static enum maps)
```
GET    /api/config/credits/                      Public
GET    /api/config/faculties/                    Public
GET    /api/config/departments/                  Public
```

### Admin — User Management
```
GET    /api/admin/users/pending/                 Admin
PATCH  /api/admin/users/{uuid}/verify/           Admin
GET    /api/admin/students/                      Admin
POST   /api/admin/students/                      Admin
PUT    /api/admin/students/{uuid}/               Admin
DELETE /api/admin/students/{uuid}/               Admin (logical)
GET    /api/admin/teachers/                      Admin
POST   /api/admin/teachers/                      Admin
PUT    /api/admin/teachers/{uuid}/               Admin
DELETE /api/admin/teachers/{uuid}/               Admin (logical)
DELETE /api/admin/devices/{uuid}/unbind/         Admin  ← unbind device
```

### Admin — Academic Structure
```
GET    /api/admin/semesters/                     Admin
POST   /api/admin/semesters/                     Admin
PUT    /api/admin/semesters/{uuid}/              Admin
DELETE /api/admin/semesters/{uuid}/              Admin (logical)
GET    /api/admin/courses/                       Admin
POST   /api/admin/courses/                       Admin
PUT    /api/admin/courses/{uuid}/                Admin
DELETE /api/admin/courses/{uuid}/                Admin (logical)
GET    /api/admin/classrooms/                    Admin
POST   /api/admin/classrooms/                    Admin
PUT    /api/admin/classrooms/{uuid}/             Admin
DELETE /api/admin/classrooms/{uuid}/             Admin (logical)
POST   /api/admin/classrooms/{uuid}/promote/     Admin  ← bulk promote students
GET    /api/admin/classrooms/{uuid}/students/    Admin  ← list students in classroom
POST   /api/admin/classrooms/{uuid}/students/    Admin  ← add students to classroom
DELETE /api/admin/classrooms/{uuid}/students/    Admin  ← remove students from classroom
POST   /api/admin/course-info/                   Admin  ← assign teacher+course to semester
GET    /api/admin/course-info/                   Admin
PUT    /api/admin/course-info/{uuid}/            Admin
DELETE /api/admin/course-info/{uuid}/            Admin (logical)
```

### Student Dashboard
```
GET    /api/student/{uuid}/semesters/            Student (own)
GET    /api/student/{uuid}/attendance/           Student (own)
```

### Teacher Dashboard
```
GET    /api/teacher/{uuid}/courses/              Teacher (own)
GET    /api/teacher/course-info/{uuid}/          Teacher
POST   /api/teacher/course-info/{uuid}/mark/     Teacher  ← manual mark single student
POST   /api/teacher/course-info/{uuid}/history-session/   Teacher  ← save full day roster
DELETE /api/teacher/course-info/{uuid}/history-session/{date}/  Teacher
```

### Attendance Sessions
```
POST   /api/sessions/start/                      Teacher
POST   /api/sessions/{uuid}/stop/                Teacher
GET    /api/sessions/{uuid}/status/              Teacher
POST   /api/sessions/{uuid}/checkin/             Student (QR online)
```

### Hardware (ESP32)
```
POST   /api/hardware/sync/                       API Key auth ← fingerprint scan → log attendance
POST   /api/hardware/enroll/start/               Student (initiates fingerprint enrollment)
POST   /api/hardware/enroll/complete/            API Key auth ← hardware confirms enrollment done
GET    /api/hardware/enroll/status/{uuid}/       Student ← poll enrollment status
```

### Reports & Export
```
GET    /api/teacher/course-info/{uuid}/export/   Teacher
       ?format=pdf|csv|xlsx|docx
       &date=YYYY-MM-DD (optional, filters single day)
```

### Offline QR Server (FastAPI — runs on teacher's device)
```
POST   /offline/session/start/     ← teacher starts, gets QR payload
GET    /offline/session/status/    ← live submissions list
POST   /offline/checkin/           ← student app hits this (sends mac + student_id)
                                      → calls real backend to verify → logs locally
POST   /offline/session/stop/      ← commits all logs to real backend
```

---

## 5. Attendance Flow Diagrams

### Flow A: Hardware (Fingerprint)
```
Teacher starts session (mode=FINGERPRINT)
    → AttendanceSession created, is_active=True
ESP32 scans fingerprint
    → Matches hardware_finger_id → gets student_id
    → POST /api/hardware/sync/ {api_key, student_id, course_info_id, timestamp}
    → Backend verifies session is active
    → Creates AttendanceLog (source=HARDWARE)
Teacher stops session
    → is_active=False, absent logs auto-generated for missing students
```

### Flow B: QR Online
```
Teacher starts session (mode=QR_ONLINE)
    → AttendanceSession created with signed qr_token
    → QR code (containing deep link + token) pushed to each student's app
Student opens app → attendance screen auto-shows
    → App sends: POST /api/sessions/{uuid}/checkin/
      { student_id, mac_address, qr_token }
    → Backend verifies: token valid + session active + mac bound to student
    → Creates AttendanceLog (source=QR_ONLINE)
```

### Flow C: QR Offline (Local WiFi)
```
Teacher starts offline session on their device
    → FastAPI offline server generates QR (contains local IP + session token)
    → QR pushed to student apps
Student app scans QR → hits teacher's local IP
    → POST /offline/checkin/ { student_id, mac_address }
    → Offline server calls real backend: GET /api/student/{id}/verify-device/
    → If valid: logs locally in memory
Teacher stops session
    → Offline server bulk-POSTs all logs to real backend
    → Real backend creates AttendanceLog rows (source=QR_OFFLINE)
```

### Flow D: Fingerprint Enrollment
```
Student opens profile → clicks "Register Fingerprint"
    → Sees list of available HardwareDevices
    → Selects one → POST /api/hardware/enroll/start/
      { hardware_device_id }
    → FingerprintEnrollmentRequest created (status=PENDING)
    → Hardware device display shows: "Ready for Student 2302001"
Student walks to device → places finger
    → ESP32 scans, stores template locally, sends:
      POST /api/hardware/enroll/complete/ { api_key, enrollment_request_id, finger_id }
    → Backend sets StudentProfile.hardware_finger_id = finger_id
    → Status = COMPLETED
Student's app (polling) sees COMPLETED → shows success
```

---

## 6. Device Binding Rules

- First time a student submits QR attendance, their MAC is stored in `DeviceBinding`
- Subsequent submissions: MAC must match stored binding
- If MAC doesn't match → rejected (proxy attempt)
- If student has no binding yet → binding created on first submission
- Only one active binding per student
- Admin can set `DeviceBinding.is_active = False` to unbind
- One MAC cannot be bound to two different students (unique constraint)

---

## 7. Setup Commands

### Prerequisites
- Python 3.12
- PostgreSQL 16
- Redis
- Git

### Step 1 — Clone & Virtual Environment
```bash
git clone <your-repo>
cd attendance_portal

python -m venv venv
# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate
```

### Step 2 — Install Dependencies
```bash
pip install django djangorestframework djangorestframework-simplejwt \
  psycopg2-binary redis django-redis python-decouple \
  openpyxl reportlab python-docx Pillow \
  django-cors-headers django-filter \
  celery qrcode[pil]
```

### Step 3 — Create Django Project
```bash
django-admin startproject config .

python manage.py startapp users
python manage.py startapp academic
python manage.py startapp attendance
python manage.py startapp hardware
python manage.py startapp reports
```

### Step 4 — PostgreSQL Setup
```sql
-- Run in psql
CREATE DATABASE attendance_portal;
CREATE USER attendance_user WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE attendance_portal TO attendance_user;
```

### Step 5 — .env File
```env
SECRET_KEY=your-django-secret-key
DEBUG=True
DB_NAME=attendance_portal
DB_USER=attendance_user
DB_PASSWORD=yourpassword
DB_HOST=localhost
DB_PORT=5432
REDIS_URL=redis://localhost:6379/0
```

### Step 6 — Migrate & Run
```bash
python manage.py makemigrations users academic attendance hardware reports
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### Step 7 — Offline Server Setup (FastAPI)
```bash
cd offline_server
pip install fastapi uvicorn httpx qrcode[pil]

# Run on teacher's device (replace 0.0.0.0 to expose on local network)
uvicorn main:app --host 0.0.0.0 --port 8001
```

---

## 8. Redis — Live Session Storage

Active attendance sessions are stored in Redis with TTL:
```
Key:   session:{session_uuid}
Value: { active: true, submissions: [2302001, 2302061], end_time: ... }
TTL:   duration_seconds + 60 buffer
```

This avoids database polling for every student check-in during a live session.
When session stops, Redis data is committed to PostgreSQL `AttendanceLog`.

---

## 9. JWT Configuration (SimpleJWT)

```python
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
}
```

Role is embedded in the JWT payload so the frontend can route without an extra API call.

---

## 10. What We Build (in order)

1. Project scaffold + settings + .env
2. `users` app — models, serializers, JWT auth endpoints
3. `academic` app — models, admin CRUD endpoints
4. `attendance` app — session model, start/stop/checkin flows
5. `hardware` app — ESP32 sync + fingerprint enrollment
6. `reports` app — export endpoint (xlsx, pdf, docx, csv)
7. `offline_server` — FastAPI minimal server