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
Demo logins: `admin@demo.local`, `teacher@demo.local`, `teacher2@demo.local`,
`student2302001@demo.local` … `student2302012@demo.local` (012 joined late); waiting for approval:
`pending.student@demo.local`, `pending.teacher@demo.local`; soft-deleted: `student2302013@demo.local`.
Admins are never created by sign-up: use `createsuperuser` (role `ADMIN`).

## Code map
- `config/settings/` `base.py` (Postgres + Redis from `.env` via python-decouple), `development.py`
  (default), `production.py` (server: env `ALLOWED_HOSTS`/`CORS_ALLOWED_ORIGINS`/`CSRF_TRUSTED_ORIGINS`,
  whitenoise for admin static files, HTTPS via nginx's `X-Forwarded-Proto`), `test.py`. `.env.example`
  lists the server's `.env` keys (also email, `WEB_URL`, app versions, throttle rates).
  `config/urls.py` mounts every app under `/api/` (JSON 404/500). `config/errors.py`: exception
  handler + `error_response()` / `validation_error_response()` (every error has a readable `message`).
- API contract for the redesign: `docs/api-v2.md` (source of truth; change it with the code).
- `apps/users/` User (UUID pk, email login, `role` STUDENT/TEACHER/ADMIN), Student/Teacher/AdminProfile,
  DeviceBinding. Sign-up waits for admin approval (`is_verified`; admins never wait). Commands:
  `seed_local_demo` (local SQLite only; dry run, `--apply`: the demo logins, an active semester with
  3 courses and ~10 past class dates of every method, a finished one, pending and deleted accounts;
  dates counted back from today; safe to run again), `remove_user <email>` (dry run; `--apply` blocks its login
  tokens, then deletes; refuses the last active admin), `cleanup_broken_signups` (accounts without a
  profile; dry run, `--apply`). `permissions.py`: role classes + `can_manage_course(user, course_info)`
  (admin, or the course's own teacher) + `not_your_course_response()`. `throttles.py` (login and register per IP +
  email with a wide per-IP cap; password forgot per IP; check-in per user). `services.py` (tokens,
  blocking tokens, reset emails, temporary passwords). `jwt.py`: tokens carry a password fingerprint
  (`CHECK_REVOKE_TOKEN`): a password change/reset ends other devices' access tokens at once; the refresh
  serializer gives older refresh tokens the fingerprint. `importing.py` (admin CSV/XLSX student import). Views: `auth` (login, register,
  logout, me, password change/forgot/reset), `admin` (approvals, students/teachers with partial PATCH,
  restore, import), `student` (semesters summary), `config` (`app/` settings + enum lists).
- `apps/academic/` Semester (`session`, `label`, `sort_semesters()`), Course, Classroom (one hidden
  "Main" class group per semester), StudentClassroom (membership `joined_at` null = from the start,
  `left_at` null = current; queryset `current()`, `enrolled_on(day)`, `active_accounts()`,
  `class_list(semester)` = current members, or everyone once the semester is finished; never delete
  rows: history), CourseInfo (course + teacher + semester + classroom = one taught class).
  `services.py`: `main_classroom`, `create_semester`, `join_date` (null before the semester's first
  class, else today), `leave_date` (today, or tomorrow when the class group held or holds a class
  today), `add_members`/`remove_members`, `promote` (finishes the source). On the join day only
  classes that logged the student count (`StudentClassroom.counts_on`). `stats.py`:
  `course_numbers()` (held/attended/percent per student inside their membership, class count,
  average, below-min) and `semester_numbers()`. Command `backfill_join_dates` (dry run, `--apply`):
  join dates for students added mid-semester under the old app, guessed from their first log.
  `views/teacher.py`: teacher's courses (current/previous with numbers, face counts, live session), course detail, one student's days, live lookup,
  single-student correction (PUT `attendance/`), roll call (GET = who is enrolled that date + a
  `version`; POST refuses while a live session runs that day, and with a stale `version`), delete a date.
  No new live or face sessions for finished semesters (`semester_finished`); roll call and corrections stay. `views/admin.py`: overview,
  semesters (finish/reopen/restore, roster, courses taught, promote), courses, course-info list and
  reassign; older classroom endpoints.
- `apps/attendance/` AttendanceSession (live session; `delivery` IN_CLASS/ONLINE; `qr_token` = the
  code secret, never sent out), AttendanceLog (one row per student per class; unique per
  session+student; `method` QR/CODE/FACE/TEACHER/FINGERPRINT or '' = not checked in; `changed_by`/
  `changed_at` = last correction) and AttendanceChange (correction trail).
  - Live flow: `start` writes the Redis entry first, then the session row → check-ins go to Redis
    (`redis_service.py`, locked read-modify-write; submissions keep method, time and device id, plus a
    device → student map: one phone, one student per session; `SessionBusy` → HTTP 503) →
    `services.finalize_session()` saves PRESENT/ABSENT logs exactly once. It runs on stop, on a status
    poll after the timer, and best-effort via `finalize_expired_sessions()` / `finalize_ended_sessions()`
    / `live_sessions()` (teacher course list and detail, history, export, live lookups, next start).
    Check-ins close at `end_time` (extend moves it; 30 min total); Redis data is kept 7 days. With no
    Redis data, `session_has_ended()` trusts the database timer (`started_at + duration_seconds`).
    Cancel deletes an unsaved session. `codes.py`: the rotating 6-digit code (30 s windows, current +
    previous accepted) and the QR link `{WEB_URL}/check-in?s=&c=`.
  - Students check in only for themselves (`/sessions/check-in/`), only as current members enrolled
    on the session's date; teachers can only mark such students.
  - `records.py`: reading by day (day status, method, `classes_needed`, student days, the student's
    semesters summary and course detail); `services.py` also has `correct_attendance` and `roll_call`
    (change only differing logs, keep their method, record AttendanceChange rows).
- `apps/hardware/` ESP32 fingerprint devices (`X-Hardware-Key` header auth), enrolment requests.
- `apps/reports/` export `?format=csv|xlsx|pdf|docx&date=` (`views/reports.py` reads `format` itself:
  DRF would treat it as a renderer). The file name is in `Content-Disposition`; `base.py`'s
  `CORS_EXPOSE_HEADERS` lets the web app (another origin) read it. `generators.py`: `build_report()`
  (class list, one column per class date, blank outside a student's membership, numbers from
  `course_numbers()`, Dhaka time); PDF/DOCX split the dates into page-wide parts (`date_chunks()`)
  that repeat ID, name and %.
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
- `deploy/` server files: `attendanceportal-api.service` (systemd, gunicorn on a unix socket as its
  own user `attendanceportal`, group www-data for nginx; only it can read `.env`), `nginx/` (API proxy with 32 MB uploads; static web app with SPA fallback), `README.md`
  (one command per block: database + Neon copy, backend, web build, nginx/certbot, app releases).

## Conventions
- API v2 (`docs/api-v2.md`) is the contract: change it in the same commit as the code.
- Class-based `APIView`s returning `{'success': bool, ...}`; errors via `config/errors.py` helpers:
  `{'success': False, 'message', 'code'?, 'errors'?}` (`message` always present, one plain sentence).
- Frontend expects some camelCase keys (`userName`, `attendanceMap`, `presentStudents`): don't rename.
  New keys are snake_case. Dates `YYYY-MM-DD` (local), date-times ISO 8601 with offset.
- Students are counted only inside their membership (`joined_at <= date < left_at`) and on the class
  list; soft-deleted accounts (`user.deleted`) are left out of lists and numbers. Never hard-delete
  students, semesters or courses from the API: soft delete / `left_at`, history stays.
- Teacher-facing course endpoints must check `can_manage_course` and return `not_your_course_response()`.
- Times: use `timezone.localdate()` / `timezone.localtime()` (TIME_ZONE is Asia/Dhaka).
- One class = one date: stats, student summary and exports count a student present on a date if any
  session that day marked them present (several sessions per day are possible). Saving face attendance
  again for the same course and date replaces that date's earlier FACE session.

## Next
Tune face thresholds with real classroom photos. Azure Face can be added as another engine in
`apps/faces/engines/` if Microsoft approves identification access (currently not approved).
