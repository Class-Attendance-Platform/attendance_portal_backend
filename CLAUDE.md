# Class Attendance Portal: backend

Django 5 + DRF REST API for the HSTU class attendance app. The frontend is the sibling repo
`attendance_portal_frontend` (Expo). Live on the owner's VPS: https://api.attendanceportal.sakibkx.tech
(steps in `deploy/README.md`; the owner runs them). Development happens on the owner's Windows laptop.
`README.md` is the original design plan (long; read only if needed).

## Working with the owner (always)
- Ask before assuming; questions as multiple choice, recommended option first, marked "(Recommended)".
- Give a short plan and wait for an explicit **"Go"** (answering questions is not approval).
- UI changes: show 2–3 mockups first and let the owner choose.
- Work sequentially; multi-agent workflows only for big steps (owner allowed this).
- After work: run tests, check UI changes in a local copy, report honestly (counts, skips, old failures).
- Plain, short language. Lead with the result or the decision needed.

## Safety rules (never break)
- The laptop `.env` points at the **production Neon database**. Never edit it and never run
  `migrate`/`makemigrations`/tests with default settings. Always add `--settings=config.settings.test`
  (local SQLite `db.sqlite3` + in-memory cache; `config/settings/__init__.py` skips the dev settings then).
- Migrations are append-only: never edit one that has been applied.
- Never run anything on the owner's servers; give commands instead. Server data changes = management
  command, dry run by default, `--apply` to write.
- Deploy loop: push → owner deploys → owner says "check now" → check the live site read-only and report.
  Each hand-off states the commits, whether migrate/rebuild is needed, and one command per code block.
- Never handle real passwords/tokens; only the seeded demo logins on localhost.
- Never force-push, never change global git config (use `git -c safe.directory=<path>`), never hard-delete
  files (rename/move aside and say so). Files here use LF line endings; keep them.
- The repo is public. Never commit secrets.

## Commands (run from repo root)
```
python -m venv venv && venv/bin/pip install -r requirements.txt     # Windows: venv\Scripts\...
python manage.py test --settings=config.settings.test                # all tests
python manage.py check --settings=config.settings.test
python manage.py makemigrations --check --dry-run --settings=config.settings.test
# Local preview with demo data (SQLite only; the command refuses anything else):
python manage.py migrate --settings=config.settings.test
python manage.py seed_local_demo --settings=config.settings.test --apply   # password: Demo-pass-2026
python manage.py runserver --settings=config.settings.test
# Face attendance models (once, ~290 MB download, ~180 MB kept in face_models/, gitignored):
python manage.py download_face_models                    # or --zip <path to buffalo_l.zip>
python manage.py check_face_engine <photo.jpg> --settings=config.settings.test
```
Demo logins: `admin@demo.local`, `teacher@demo.local`, `student2302001@demo.local` (…002, …003).
Admins are never created by sign-up: use `createsuperuser` (role `ADMIN`).

## Code map
- `config/settings/` `base.py` (Postgres + Redis from `.env` via python-decouple), `development.py`
  (default), `production.py` (server: env `ALLOWED_HOSTS`/`CORS_ALLOWED_ORIGINS`/`CSRF_TRUSTED_ORIGINS`,
  whitenoise for admin static files, HTTPS via nginx's `X-Forwarded-Proto`), `test.py`. `.env.example`
  lists the server's `.env` keys. `config/urls.py` mounts every app under `/api/`.
- `apps/users/` User (UUID pk, email login, `role` STUDENT/TEACHER/ADMIN), Student/Teacher/AdminProfile,
  DeviceBinding. `permissions.py`: role classes + `can_manage_course(user, course_info)` (admin, or the
  course's own teacher) + `not_your_course_response()`. Views: `auth` (login/register/me/refresh),
  `admin` (CRUD students/teachers), `student` (semesters summary), `config` (enum lists).
- `apps/academic/` Semester, Course, Classroom, StudentClassroom (enrolment), CourseInfo (course + teacher
  + semester + classroom = one taught class). `views/teacher.py`: teacher's courses, course detail with
  attendance stats, bulk "history-session" save/delete by date. `views/admin.py`: admin CRUD.
- `apps/attendance/` AttendanceSession (live QR/fingerprint session) and AttendanceLog (one row per
  student per class; unique per session+student).
  - Live flow: `start` writes the Redis entry first, then the session row → check-ins go to Redis
    (`redis_service.py`, locked read-modify-write; `SessionBusy` → HTTP 503) →
    `services.finalize_session()` saves PRESENT/ABSENT logs exactly once. It runs on stop, on a status
    poll after the timer, and best-effort via `finalize_expired_sessions()` / `finalize_ended_sessions()`
    (teacher course list and detail, history, export, student's active-session lookup, next start).
    Check-ins close at `end_time`; Redis data is kept 7 days. With no Redis data, `session_has_ended()`
    trusts the database timer (`started_at + duration_seconds`).
  - Students can only check in for themselves, only if enrolled; teachers can only mark enrolled students.
- `apps/hardware/` ESP32 fingerprint devices (`X-Hardware-Key` header auth), enrolment requests.
- `apps/reports/` export csv/xlsx/pdf/docx (`generators.py`).
- `apps/faces/` face attendance (`/api/faces/`). `engines/`: `get_engine()` picks `settings.FACE_ENGINE`;
  `insightface_onnx.py` runs InsightFace buffalo_l (SCRFD detector + ArcFace) with onnxruntime + OpenCV
  (no `insightface` package). `services.py`: `register_student_faces` (3 poses, one face each, same person,
  duplicate-face block → 409), `recognize_class` (1–3 photos, only enrolled students compared, one face per
  student; present ≥ `FACE_MATCH_THRESHOLD`, unsure ≥ `FACE_UNSURE_THRESHOLD`, `no_face` → unsure; nothing
  saved), `save_face_attendance` (finished FACE session + logs). `StudentFace` keeps a 112px crop + 512-float
  embedding per pose; class photos are never stored. Thresholds are settings (tunable via env). Models are
  non-commercial/academic use only. Tests use a fake engine (`apps/faces/tests/test_faces.py`).
- `offline_server/` separate FastAPI app for offline QR on the teacher's laptop (calls this API).
- Tests: `apps/*/tests/`; factories in `apps/attendance/tests/helpers.py`. CI: `.github/workflows/tests.yml`
  (Python 3.14 like the server; checks, migrations check, tests).
- `deploy/` server files: `attendanceportal-api.service` (systemd, gunicorn on a unix socket as
  www-data), `nginx/` (API proxy with 32 MB uploads; static web app with SPA fallback), `README.md`
  (one command per block: database + Neon copy, backend, web build, nginx/certbot, app releases).

## Conventions
- Class-based `APIView`s returning `{'success': bool, ...}`; errors as `{'success': False, 'message'|'errors'}`.
- Frontend expects some camelCase keys (`userName`, `attendanceMap`, `presentStudents`): don't rename.
- Teacher-facing course endpoints must check `can_manage_course` and return `not_your_course_response()`.
- Times: use `timezone.localdate()` / `timezone.localtime()` (TIME_ZONE is Asia/Dhaka).
- One class = one date: stats, student summary and exports count a student present on a date if any
  session that day marked them present (several sessions per day are possible). Saving face attendance
  again for the same course and date replaces that date's earlier FACE session.

## Next
Tune face thresholds with real classroom photos. Azure Face can be added as another engine in
`apps/faces/engines/` if Microsoft approves identification access (currently not approved).
