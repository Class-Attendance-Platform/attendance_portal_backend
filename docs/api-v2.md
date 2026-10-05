# API v2: the contract for the redesign

The redesigned frontend (web, Android, desktop) is built against this document. The backend and the
new screens are developed at the same time, so **this file is the source of truth**. If code and this
file disagree, fix one of them in the same change.

Owner decisions behind it: one department (CSE), English only, light theme, sign-up needs admin
approval, password help by email (Gmail) plus admin reset, Present/Absent only, one class group per
semester, late joiners counted from the day they joined, no lock on finished semesters, fingerprint
hardware hidden, one date = one class.

## Conventions

- Base path `/api/`. JSON. Auth: `Authorization: Bearer <access>`; refresh tokens rotate (the old one
  is blacklisted after use).
- Every response has `success: bool`. Errors: `{"success": false, "message": "<one readable
  sentence>", "code": "<machine_code>"?, "errors": {"field": ["msg"]}?}`. **`message` is always
  present on errors** (for field errors it repeats the first one in plain words, never "field: msg").
- Existing keys keep their names (some are camelCase, e.g. `userName`, `presentStudents`). **New keys
  are snake_case.**
- Dates `YYYY-MM-DD` (Asia/Dhaka local date). Date-times ISO 8601 with offset. The app formats them
  for people ("05 Oct 2026").
- "Student id" means the university roll number (`student_id`, an integer like 2302001). Database
  ids are UUIDs and are named `id`, `user_id`, `profile_id`, `course_info_id`, `session_id`.
- Lists the admin pages show are returned whole (one department is small: hundreds of rows). Search
  and filters run on the server with query parameters where listed.
- Throttles (DRF, per IP unless noted): login 10/min, register 5/hour, password forgot 5/hour,
  check-in 30/min per user. Throttled requests get 429 with a `message`.

## 1. Accounts and sign-in

### POST /auth/login/ (public)
Body `{email, password}`. **Email is case-insensitive.** 200: as today
`{success, access, refresh, user}`. Errors:
- 401 `code: "invalid_credentials"`, "Email or password is incorrect."
- 403 `code: "pending_approval"`, "Your account is waiting for admin approval." (correct password, not
  approved yet; no tokens)
- 403 `code: "account_disabled"`, "This account has been disabled. Contact the department office."

`user` (also returned by `/auth/me/`):
```
{ id, userName, first_name, last_name, email, role: "STUDENT"|"TEACHER"|"ADMIN",
  is_verified, date_joined,
  student_profile: { id, student_id, current_level, current_semester } | null,
  teacher_profile: { id, employee_id } | null }
```

### POST /auth/register/ (public)
Student: `{role: "STUDENT", email, password, first_name, last_name, student_id, current_level,
current_semester}`. Teacher: `{role: "TEACHER", email, password, first_name, last_name,
employee_id}`. Faculty and department are set by the server (CSE). Email stored lower case.
Atomic: a duplicate email / student id / employee id gives 400 with a field error (never 500, never a
half-made account). Password rules: Django validators (8+ chars, not common, not all digits).
201 `{success, status: "pending", message: "Account created. An admin will approve it soon."}`.
**No tokens.** ADMIN cannot register.

### POST /auth/logout/ (signed in)
Body `{refresh}`. Blacklists it. 200 `{success}` (also 200 if it was already invalid).

### PATCH /auth/me/ (signed in)
Body `{first_name?, last_name?}`. Users may change only their name. 200 `{success, user}`.

### POST /auth/password/change/ (signed in)
Body `{current_password, new_password}`. 400 `code: "wrong_password"` or field errors. On success
**all the user's refresh tokens are blacklisted** (other devices are signed out) and a fresh pair is
returned: `{success, tokens: {access, refresh}}`.

### POST /auth/password/forgot/ (public)
Body `{email}`. Always 200 `{success, message: "If an account exists for this email, we sent a link
to reset the password."}` when email is configured. If email is not configured on the server:
503 `code: "email_not_configured"`, "Password reset by email is not set up. Ask an admin to reset
your password." The email links to `{WEB_URL}/reset-password?uid=<uidb64>&token=<token>` (Django's
password-reset token, valid 1 day, one use). Inactive / pending users get no email.

### POST /auth/password/reset/ (public)
Body `{uid, token, new_password}`. 200 `{success, message}`; 400 `code: "invalid_link"` ("This link
is invalid or has expired.") or password field errors. Blacklists the user's refresh tokens.

### GET /config/app/ (public)
```
{ success, app_name: "HSTU Attendance Portal", faculty: "Computer Science and Engineering",
  department: "CSE", attendance_min_percent: 75, email_reset_enabled: bool,
  min_app_version: "1.0.0", latest_app_version: "1.0.0",
  download_url: "https://github.com/Class-Attendance-Platform/attendance_portal_frontend/releases/latest",
  levels: ["First","Second","Third","Fourth"], terms: ["I","II"] }
```
`min_app_version` / `latest_app_version` come from settings (`MIN_APP_VERSION`, `LATEST_APP_VERSION`
env). The Android app compares its version and blocks with "Please update" below the minimum.

Existing `/config/faculties/`, `/config/departments/`, `/config/credits/` stay.

## 2. Admin: people

All admin endpoints need role ADMIN.

### GET /admin/users/pending/
`{success, users: [{id, email, first_name, last_name, role, date_joined, student_id?,
current_level?, current_semester?, employee_id?}]}`, oldest first.

### POST /admin/users/<user_id>/verify/  → approve (exists; keep)
### POST /admin/users/<user_id>/reject/
Soft-deletes the account (`deleted=true`, `is_active=false`) and frees nothing else. 200.

### POST /admin/users/<user_id>/reset-password/
Body `{new_password}` (the admin's temporary password, validated). Blacklists the user's refresh
tokens. 200 `{success, message}`.

### Students: GET/POST /admin/students/, GET/PATCH/DELETE /admin/students/<profile_id>/
List query: `?search=` (name, email or student id), `?level=`, `?semester=` (term),
`?status=active|deleted` (default active). Row:
```
{ id (profile id), user_id, email, first_name, last_name, userName, student_id,
  current_level, current_semester, is_verified, is_active, deleted, last_login,
  face_registered: bool, semester: {id, label} | null   // current active semester they are in }
```
POST body `{email, first_name, last_name, student_id, current_level, current_semester, password}`
(password = temporary password; account is approved). PATCH is **partial**: any of `email,
first_name, last_name, student_id, current_level, current_semester` (uniqueness checked, 400 field
errors). DELETE = soft delete (also removes face data, as today). Unbinding devices is gone
(see 5).
### POST /admin/students/<profile_id>/restore/

### Teachers: GET/POST /admin/teachers/, GET/PATCH/DELETE /admin/teachers/<profile_id>/
Row `{id, user_id, email, first_name, last_name, userName, employee_id, is_verified, is_active,
deleted, last_login, course_count}`. POST `{email, first_name, last_name, employee_id, password}`.
PATCH partial: `email, first_name, last_name, employee_id`. Same search/status filters.
### POST /admin/teachers/<profile_id>/restore/

### POST /admin/students/import/ (multipart)
Fields: `file` (.csv or .xlsx), `apply` (`"true"` to write; anything else = dry run),
`semester_id` (optional: also add every new student to that semester). Columns (header row, any
order, case-insensitive): `student_id`, `name` (or `first_name` + `last_name`), `email`, `level`,
`term`. Response:
```
{ success, applied: bool,
  summary: {create: n, exists: n, error: n},
  rows: [{row: 2, student_id, name, email, level, term,
          status: "create"|"exists"|"error", errors: ["..."]}],
  created: [{student_id, email, temporary_password}]   // only when applied
}
```
"exists" = a student with that student id or email already exists (skipped). Imported accounts are
approved. Temporary passwords are random 10-character strings shown once.

## 3. Admin: semesters, courses, assignments

A semester has exactly one hidden class group ("Main" classroom), created with it. The UI never
shows classrooms.

Semester row:
```
{ id, level, semester, session /* "2025-26" */, label /* "Level 3 · Term I · 2025-26" */,
  start_date, end_date, is_active /* false = finished */, deleted,
  classroom_id, student_count, course_count }
```
- GET /admin/semesters/ `?status=active|finished|deleted|all` (default all not deleted), ordered:
  active first, then by session (newest), level, term.
- POST /admin/semesters/ `{level, semester, session, start_date?, end_date?}` → creates semester +
  its Main classroom. 400 if an **active** semester with the same level already exists
  (`code: "level_has_active_semester"`).
- PATCH /admin/semesters/<id>/ `{session?, start_date?, end_date?}`. DELETE = soft delete.
- POST /admin/semesters/<id>/finish/, POST /admin/semesters/<id>/reopen/, POST /admin/semesters/<id>/restore/.
- Roster: GET /admin/semesters/<id>/students/ →
  `{success, students: [{profile_id, student_id, name, email, joined_at, left_at}]}` (current members
  first; `?include_left=true` adds former ones).
  POST /admin/semesters/<id>/students/ `{profile_ids: [..]}` → adds (joined_at = today; re-adding a
  former member clears left_at and keeps the original joined_at).
  POST /admin/semesters/<id>/students/remove/ `{profile_ids: [..]}` → sets left_at = today (history
  stays visible).
- Courses taught in a semester: GET /admin/semesters/<id>/courses/ →
  `{success, courses: [{course_info_id, course: {id, code, title, credits}, teacher: {id, name} |
  null}]}`. POST `{course_id, teacher_id}` → creates the CourseInfo on the semester's classroom.
  PATCH /admin/course-info/<id>/ `{teacher_id}` (reassign; the new teacher sees all history).
  DELETE /admin/course-info/<id>/ = soft delete.
- Promote: POST /admin/semesters/<id>/promote/
  `{target: {level, semester, session, start_date?, end_date?} | null, target_semester_id?,
  profile_ids?: [..] /* default: all current members */}` → uses or creates the target semester,
  marks the moved students left_at = today on the source and joined_at = today on the target,
  updates their profile level/term, **finishes the source semester** (never deletes). 200
  `{success, target_semester_id, moved}`. The old `/admin/classrooms/<id>/promote/` stays but is unused.

Courses: GET/POST /admin/courses/, GET/PATCH/DELETE /admin/courses/<id>/ (as today, PATCH partial),
`?status=active|deleted`, POST /admin/courses/<id>/restore/. A soft-deleted course is hidden from
teachers and students too.

## 4. Admin: overview and attendance

### GET /admin/overview/
```
{ success,
  counts: {students, teachers, courses, active_semesters, pending_approvals},
  semesters: [{id, label, student_count, course_count, average_percent, below_min_count}],
  recent_sessions: [{session_id, course_info_id, course_code, course_title, date, delivery,
                     mode, present, total}]   // last 10 saved sessions
}
```
The admin opens any course's attendance through the teacher endpoints in section 6 (admins pass
`can_manage_course`); the admin UI is read-only there. List of all taught courses:
GET /admin/course-info/ (exists) with `?semester_id=`; row
`{id, course: {code, title}, teacher: {id, name} | null, semester: {id, label, is_active},
student_count, classes_held, average_percent}`.

## 5. Live attendance sessions (rotating code)

A session belongs to one course. The teacher picks the length and where the class is:
`delivery: "IN_CLASS" | "ONLINE"` (stored on the session; logs keep source `QR_ONLINE`).
Every 30 seconds there is a new 6-digit **code**: `HMAC_SHA256(session.qr_token, floor(unix/30))`
→ 6 digits (zero-padded). The server accepts the current and the previous window (so a code is
valid for 30–60 s). `qr_token` is never sent to students. The QR shows the URL
`{WEB_URL}/check-in?s=<session_id>&c=<code>` (the phone camera opens the web check-in page; the
in-app scanner reads `s` and `c` from the same URL).

**One phone, one student per session:** every check-in sends `device_id` (a random id the app keeps
per install/browser). If that device already checked in a *different* student in this session:
409 `code: "device_used"`. No permanent binding; DeviceBinding is no longer checked.

Teacher (owner of the course, or admin):
- POST /sessions/start/ `{course_info_id, delivery, duration_minutes: 2|5|10|15}` → 201
  `{success, session: {id, course_info_id, delivery, date, started_at, ends_at, time_left,
  code_period: 30}}`. 409 `code: "session_running"` with `session_id` if one is already live for
  this course.
- GET /sessions/<id>/code/ → `{success, code: "482913", check_in_url, expires_in /* s until it
  changes */, period: 30}`. Poll every few seconds.
- GET /sessions/<id>/status/ → live: `{success, active: true, session: {id, delivery, ends_at,
  time_left, total, checked_in: [{profile_id, student_id, name, time, method: "QR"|"CODE"|"TEACHER"}],
  not_checked_in: [{profile_id, student_id, name}]}}`; ended: `{success, active: false, session_id,
  saved: bool, total_present}` (status saves an expired session, as today).
- POST /sessions/<id>/extend/ `{minutes: 2}` → `{success, ends_at, time_left}` (max total 30 min).
- POST /sessions/<id>/mark/ `{profile_id}` while live → adds the student to the live check-ins with
  method TEACHER (saved with the session). After the session ended, use section 6 corrections.
- POST /sessions/<id>/stop/ → saves now (exists).
- POST /sessions/<id>/cancel/ → discards the session and its check-ins; nothing is saved; 200.
- GET /teacher/course-info/<id>/live/ → `{success, session: <same as start> | null}` (reopen after a
  reload).

Student:
- GET /student/live/ → `{success, sessions: [{session_id, course_info_id, course: {code, title},
  delivery, ends_at, checked_in: bool}]}` for the student's own current courses (for the "Live now"
  banner; poll every 20–30 s). **No token or code.**
- POST /sessions/check-in/ `{code, session_id?, device_id}` → if `session_id` is missing (typed code),
  the server finds the student's live session whose current code matches. 200
  `{success, message: "Checked in to CSE 301.", course: {code, title}, time}`. Errors:
  400 `code_invalid` ("This code is wrong or has expired. Check the newest code."),
  403 `not_enrolled`, 409 `already_checked_in`, 409 `device_used`, 410 `session_ended`.
- The old `/sessions/<id>/checkin/` and `/sessions/course-info/<id>/active/` (which leaked the QR token)
  are removed.

## 6. Teacher: courses, students, history, corrections

### GET /teacher/<user_or_profile_id>/courses/ (exists, extended)
`{success, current: [Course], previous: [Course]}` (previous = finished semesters; the UI makes them
view/export only). Course:
```
{ course_info_id, code, title, credits, semester: {id, label, is_active},
  student_count, classes_held, average_percent, below_min_count, face_registered_count,
  live_session_id | null }
```

### GET /teacher/course-info/<id>/ (exists, extended)
```
{ success, course: <Course>,
  students: [{profile_id, student_id, name, joined_at, left_at, attended, held, percent,
              below_min: bool, face_registered: bool}],
  dates: [{date, present, total}]   // newest first; one date = one class
}
```
`held` counts only class dates on or after the student's `joined_at` (and before `left_at`);
`percent` = attended / held (null when held = 0; never "Low" with no classes).

### GET /teacher/course-info/<id>/students/<profile_id>/
`{success, student: {profile_id, student_id, name, email, joined_at, left_at, attended, held,
percent}, days: [{date, status: "PRESENT"|"ABSENT"|null /* null = not enrolled yet */, method:
"QR"|"CODE"|"FACE"|"TEACHER"|null, changed_by: name | null, changed_at | null}]}`.

### GET /sessions/course-info/<id>/history/ (exists, extended)
Each day: `{date, sessions: [{session_id, delivery, mode}], logs: [{profile_id, student_id, name,
status, method, changed_by, changed_at}]}`.

### PUT /teacher/course-info/<id>/attendance/
Body `{date, profile_id, status: "PRESENT"|"ABSENT"}` → changes **only that student** on a date that
already has a class. Keeps the original method, records `changed_by` and `changed_at`, and adds an
AttendanceChange row (old → new, who, when). 404 `code: "no_class_on_date"` when the date has no
class (use roll call). Future dates → 400.

### POST /teacher/course-info/<id>/roll-call/  (replaces history-session POST)
Body `{date, present_profile_ids: [..]}`. Creates the class for that date if missing (source
TEACHER/MANUAL). For each student enrolled on that date: creates a log if missing; changes an
existing log only if its status differs (recorded like a correction). Never re-labels other methods.
Future dates → 400 `code: "future_date"`. 200 `{success, date, present, absent, changed}`.

### DELETE /teacher/course-info/<id>/history-session/<date>/ (exists)
Deletes that date's classes and logs (the UI asks for confirmation).

## 7. Student

### GET /student/<user_or_profile_id>/semesters/ (exists, fixed)
Only the student's **own** class group's courses; semesters sorted by level then term (First,
Second, Third, Fourth; I, II); percentages count from `joined_at`. Per course adds
`{course_info_id, held, attended, percent, below_min, classes_needed /* to reach the minimum; 0 if
already there */}` and per semester `{label, is_active, overall_percent}`.

### GET /student/course-info/<id>/
`{success, course: {course_info_id, code, title, credits, teacher_name, semester: {label,
is_active}}, attended, held, percent, classes_needed, days: [{date, status, method, changed: bool}]}`
for the signed-in student only (403 if not theirs).

## 8. Faces, reports

- Faces: as today. Course detail and course list carry face registration counts (section 6).
- GET /reports/course-info/<id>/export/?format=pdf|xlsx|csv|docx&date=YYYY-MM-DD (exists).
  Fixes: generated time in Asia/Dhaka; join dates respected (not enrolled = blank, not absent); the
  PDF splits the date grid across pages so ID, name and % are always visible; header has course,
  semester label, department and teacher.

## 9. Data changes (migrations, append-only)

- users: data migration sets `is_verified = true` for every existing user (they are already using
  the app). Admin-created and imported users are created verified.
- academic: `Semester.session` (CharField, blank); `StudentClassroom.joined_at` (DateField, null =
  "from the start", which is what existing rows get) and `left_at` (null).
- attendance: `AttendanceSession.delivery` (IN_CLASS default); `AttendanceLog.changed_by` (FK user,
  null, SET_NULL) and `changed_at` (null); new `AttendanceChange` (log FK, old_status, new_status,
  changed_by, changed_at).
- Management command `cleanup_broken_signups`: lists STUDENT/TEACHER users without a profile (made
  by the old sign-up bug); dry run by default, `--apply` removes them.

## 10. Settings

- Email (Gmail SMTP): `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (Gmail app password),
  `DEFAULT_FROM_EMAIL`; host smtp.gmail.com:587 TLS. Empty `EMAIL_HOST_USER` = email reset off.
- `WEB_URL` (https://attendanceportal.sakibkx.tech) for links in emails and QR codes.
- `MIN_APP_VERSION`, `LATEST_APP_VERSION`, `ATTENDANCE_MIN_PERCENT` (75).
