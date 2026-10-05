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
  Common codes: `not_authenticated`, `token_not_valid` (401), `permission_denied` (403), `not_found`,
  `throttled`. Unknown addresses and server errors answer JSON too (`not_found`, `server_error`).
- Existing keys keep their names (some are camelCase, e.g. `userName`, `presentStudents`). **New keys
  are snake_case.**
- Dates `YYYY-MM-DD` (Asia/Dhaka local date). Date-times ISO 8601 with offset. The app formats them
  for people ("05 Oct 2026").
- "Student id" means the university roll number (`student_id`, an integer like 2302001). Database
  ids are UUIDs and are named `id`, `user_id`, `profile_id`, `course_info_id`, `session_id`.
- Lists the admin pages show are returned whole (one department is small: hundreds of rows). Search
  and filters run on the server with query parameters where listed.
- Throttles (DRF, per IP unless noted): login 10/min, register 5/hour, password forgot 5/hour,
  check-in 30/min per user. Throttled requests get 429 with a `message` (`code: "throttled"`,
  `Retry-After` header). The rates are settings (section 10). The counts live in the cache (Redis);
  while it cannot be reached the limits are skipped (logged), so signing in never needs Redis.

## 1. Accounts and sign-in

### POST /auth/login/ (public)
Body `{email, password}`. **Email is case-insensitive.** 200: as today
`{success, access, refresh, user}`. Errors:
- 401 `code: "invalid_credentials"`, "Email or password is incorrect."
- 403 `code: "pending_approval"`, "Your account is waiting for admin approval." (correct password, not
  approved yet; no tokens)
- 403 `code: "account_disabled"`, "This account has been disabled. Contact the department office."

Admins (role ADMIN or superuser, e.g. made by `createsuperuser`) never wait for approval.

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
employee_id}`. `last_name` may be empty. Faculty and department are set by the server (CSE). Email
stored lower case.
Atomic: a duplicate email / student id / employee id gives 400 with a field error (never 500, never a
half-made account). Emails hold at most 150 characters (the email is also the username), here and
in the admin's create/PATCH and import. Password rules: Django validators (8+ chars, not common,
not all digits).
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
to reset the password."}` when email is configured (400 only for a malformed email). If email is not
configured on the server:
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
current_level?, current_semester?, employee_id?}]}`, oldest first (students and teachers only).

### POST /admin/users/<user_id>/verify/  → approve (exists; keep; PATCH also accepted for the old app)
### POST /admin/users/<user_id>/reject/
Soft-deletes the account (`deleted=true`, `is_active=false`) and frees nothing else (email and ids stay
taken; a student's face data is removed, as with DELETE). Students and teachers only (404 otherwise). 200.

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
errors). DELETE = soft delete (also removes face data, as today, and signs the user out). Unbinding
devices is gone (see 5). Other `?status=` values → 400 `code: "invalid_status"`. GET of one row also
works for deleted accounts; PATCH/DELETE of a deleted one → 404 (restore first). PUT is still accepted
as PATCH for the old app. POST → 201 `{success, student: <row>}`; GET/PATCH → `{success, student}`.
### POST /admin/students/<profile_id>/restore/
→ `{success, message, student: <row>}`.

### Teachers: GET/POST /admin/teachers/, GET/PATCH/DELETE /admin/teachers/<profile_id>/
Row `{id, user_id, email, first_name, last_name, userName, employee_id, is_verified, is_active,
deleted, last_login, course_count}`. POST `{email, first_name, last_name, employee_id, password}`.
PATCH partial: `email, first_name, last_name, employee_id`. Same search/status filters (search: name,
email or employee id); responses as for students with `teacher`. `course_count` counts the teacher's
course-infos that are not deleted (nor their semester or course).
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
Level also accepts 1–4 / "3rd", term 1 / 2. An email over 150 characters is an error ("Email is too
long."). A row repeating an earlier row's student id or email is an
error ("Same student ID as row 2."); an email of a teacher/admin account is an error. Applying writes
only the "create" rows, in one transaction (409 `code: "conflict"` if someone added the same students
meanwhile); without a class group yet, the semester gets its "Main" one. File problems (not .csv/.xlsx,
missing columns, empty, unreadable, over 2 MB or 1000 rows) → 400 `code: "invalid_file"`; an unknown
`semester_id` → 400 field error.

## 3. Admin: semesters, courses, assignments

A semester has exactly one hidden class group ("Main" classroom), created with it (migration
academic 0003 gives older semesters without one an empty one). The UI never shows classrooms.

**Membership dates.** A student's membership in a class group has `joined_at` (null = from the
start) and `left_at` (null = current member). It covers the dates `joined_at <= date < left_at`:
held classes, roll calls, saved sessions and face attendance only count a student on those dates.
Former members keep their row and their attendance (history stays visible); they are not current
anywhere (no check-in, live sessions, face matching, "current semester" of the students list).
**Same-day changes** (one date = one class): on the **join day** a class counts for the student
only if they have a log for it that day, so a class held before they were added does not count
(held 0, status null, blank in exports), while a session, roll call or face save after they joined
logs them and counts. **Leaving** (remove, promote) sets `left_at` = today, or **tomorrow** when the
class group already held a class today or has a session today (a live one included): that day's
attendance stays counted and a running session still saves their check-in. Either way they stop
being current at once (no check-in, live sessions, face matching, not on an active semester's class
list); until the day ends a later class that day still logs them (absent unless a teacher marks
them).
**Class list** = who a semester's or course's lists and numbers count (student counts, averages,
below-minimum counts, the teacher's course students, exports): its current members while the
semester is active; once it is finished, everyone who was in it (promotion marks them as left).
Soft-deleted student accounts are left out of rosters, class lists and counts.

Semester row:
```
{ id, level, semester, session /* "2025-26" */, label /* "Level 3 · Term I · 2025-26" */,
  start_date, end_date, is_active /* false = finished */, deleted,
  classroom_id, student_count /* class list */, course_count /* not deleted, course not deleted */ }
```
- GET /admin/semesters/ `?status=all|active|finished|deleted` (`all`, the default = every semester
  that is not deleted; other values → 400 `code: "invalid_status"`), ordered: active first, then by
  session (newest; none last), level (First..Fourth), term. GET /admin/semesters/<id>/ →
  `{success, semester}` (also for a deleted one).
- POST /admin/semesters/ `{level, semester, session, start_date?, end_date?}` → creates semester +
  its Main classroom; 201 `{success, semester}`. 400 if an **active** semester with the same level
  already exists (`code: "level_has_active_semester"`, "Level 3 already has an active semester.
  Finish it first."). `level` First|Second|Third|Fourth, `semester` I|II, `session` required
  ("2025-26"; "2025-2026" and "2025/26" are stored as "2025-26"), `end_date` not before `start_date`.
- PATCH /admin/semesters/<id>/ `{session?, start_date?, end_date?}` → `{success, semester}` (level and
  term never change; PUT is accepted as PATCH for the old app, whose `students`/`courses` lists are
  ignored). DELETE = soft delete (also finishes it) → `{success, message}`. PATCH/DELETE of a deleted
  semester → 404.
- POST /admin/semesters/<id>/finish/, POST /admin/semesters/<id>/reopen/ (400
  `level_has_active_semester` if its level has another active semester), POST
  /admin/semesters/<id>/restore/ (comes back finished; reopen it if needed). Each →
  `{success, message, semester}`; finish/reopen of a deleted semester → 404.
- Roster: GET /admin/semesters/<id>/students/ →
  `{success, students: [{profile_id, student_id, name, email, joined_at, left_at}]}` (current members
  first, each group by student id; `?include_left=true` adds former ones).
  POST /admin/semesters/<id>/students/ `{profile_ids: [..]}` → adds: joined_at = today once the
  semester has held a class (a late joiner; a class held earlier that day does not count for them,
  see "Same-day changes"); before its first class joined_at stays null (= from the start, so
  classes entered later for earlier dates still count); re-adding a former member clears
  left_at and keeps the original joined_at. → `{success, message, added, rejoined, already_in}`
  (counts). An unknown id or a deleted account → 400 (nothing is added). The student's profile
  level/term is not changed (promote does that).
  POST /admin/semesters/<id>/students/remove/ `{profile_ids: [..]}` → sets left_at = today, or
  tomorrow if the class group already held or is holding a class today (history stays visible; see
  "Same-day changes") → `{success, message, removed}`; ids that are not current members are ignored.
  The import (section 2, `semester_id`) adds students the same way.
- Courses taught in a semester: GET /admin/semesters/<id>/courses/ →
  `{success, courses: [{course_info_id, course: {id, code, title, credits}, teacher: {id, name} |
  null}]}` (by course code). POST `{course_id, teacher_id}` → creates the CourseInfo on the
  semester's classroom; 201 `{success, message, course: <row>}`. `teacher_id` may be null or left
  out (no teacher yet). An unknown/deleted course or teacher → 400 field error; a course already
  taught in the semester → 400 `code: "course_already_added"`; a course removed from this semester
  earlier comes back (same `course_info_id`, its attendance kept).
  PATCH /admin/course-info/<id>/ `{teacher_id}` (reassign; null = no teacher; the new teacher sees
  all history) → `{success, message, course_info: <row as in the semester's courses>}`.
  DELETE /admin/course-info/<id>/ = soft delete → `{success, message}`. Both → 404 when deleted.
- Promote: POST /admin/semesters/<id>/promote/
  `{target: {level, semester, session, start_date?, end_date?} | null, target_semester_id?,
  profile_ids?: [..] /* default: all current members */}` (exactly one of `target` /
  `target_semester_id`) → uses or creates the target semester (`target` reuses a semester that is
  not deleted with the same level, term and session; a new one gets 400 `level_has_active_semester`
  if another active semester of that level exists, the source not counted), marks the moved
  students as left on the source like a remove (left_at = today, or tomorrow after a class today)
  and adds them to the target like a roster add,
  updates their profile level/term, **finishes the source semester** (never deletes). 200
  `{success, message, target_semester_id, moved}`. `profile_ids` must be current members (400
  otherwise); deleted accounts are not moved; target = source → 400. The old
  `/admin/classrooms/<id>/promote/` stays but is unused.

Courses: GET/POST /admin/courses/, GET/PATCH/DELETE /admin/courses/<id>/ (as today, PATCH partial;
PUT accepted as PATCH), `?status=active|deleted` (others → 400 `invalid_status`), POST
/admin/courses/<id>/restore/ → `{success, message, course}`. The course row also has `deleted`. Codes
are unique ignoring capitals ("A course with the code CSE301 already exists." + "It is deleted:
restore it instead." when it is). DELETE of a deleted course → 404. A soft-deleted course is hidden
from teachers and students too (and from the semester's courses and the course-info list).

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
- `counts.students` / `teachers`: approved accounts that are not deleted; `courses`: not deleted.
- `semesters`: the active ones, in the semesters list's order. A student's percent in a course =
  attended / held over their class dates (section 6). `average_percent` = mean of every (student,
  course) percent with held > 0 (one decimal; null when nothing is held); `below_min_count` =
  students below `ATTENDANCE_MIN_PERCENT` in at least one course.
- `recent_sessions`: finished sessions of courses that are not deleted, newest saved first;
  `present` / `total` = its PRESENT logs / all its logs.

The admin opens any course's attendance through the teacher endpoints in section 6 (admins pass
`can_manage_course`); the admin UI is read-only there. List of all taught courses:
GET /admin/course-info/ (exists) with `?semester_id=` (not a valid id → 400); key `course_infos`; row
`{id, course: {id, code, title}, teacher: {id, name} | null, semester: {id, label, is_active},
student_count, classes_held, average_percent}` (class list; classes_held = class dates; average as
above, null without classes). Leaves out deleted course-infos, courses and semesters; ordered like
the semesters list, then by course code.

## 5. Live attendance sessions (rotating code)

A session belongs to one course. The teacher picks the length and where the class is:
`delivery: "IN_CLASS" | "ONLINE"` (stored on the session; logs keep source `QR_ONLINE`).
Every 30 seconds there is a new 6-digit **code**: `HMAC_SHA256(session.qr_token, floor(unix/30))`
(the window number as 8 bytes, big-endian) → 6 digits like a one-time password (RFC 4226 dynamic
truncation, zero-padded). The server accepts the current and the previous window (so a code is
valid for 30–60 s). `qr_token` is never sent out (teachers get codes). The QR shows the URL
`{WEB_URL}/check-in?s=<session_id>&c=<code>` (the phone camera opens the web check-in page; the
in-app scanner reads `s` and `c` from the same URL).

**One phone, one student per session:** every check-in sends `device_id` (a random id the app keeps
per install/browser). If that device already checked in a *different* student in this session:
409 `code: "device_used"`. No permanent binding; DeviceBinding is no longer checked. The old
GET /student/verify-device/<student_id>/ (used by the offline laptop server with the teacher's
token) is for teachers and admins only (403 `permission_denied` for students).

Teacher (owner of the course, or admin):
- POST /sessions/start/ `{course_info_id, delivery, duration_minutes: 2|5|10|15}` → 201
  `{success, session: {id, course_info_id, delivery, date, started_at, ends_at, time_left,
  code_period: 30}}`. 409 `code: "session_running"` with `session_id` if one is already live for
  this course (a session whose timer ran out is saved first and does not block). `delivery`
  defaults to IN_CLASS and `duration_minutes` to 5; other values → 400. Optional `mode` (default
  `QR_ONLINE`; `FINGERPRINT` / `QR_OFFLINE` stay for the hidden fingerprint devices and the offline
  laptop server; `FACE` → 400). 404 for a deleted course-info, course or semester.
- GET /sessions/<id>/code/ → `{success, code: "482913", check_in_url, expires_in /* s until it
  changes */, period: 30}`. Poll every few seconds. 410 `session_ended` once the session is over
  (an expired one is saved); 400 `no_code` for a fingerprint session.
- GET /sessions/<id>/status/ → live: `{success, active: true, session: {id, delivery, ends_at,
  time_left, total, checked_in: [{profile_id, student_id, name, time, method: "QR"|"CODE"|"TEACHER"}],
  not_checked_in: [{profile_id, student_id, name}]}}`; ended: `{success, active: false, session_id,
  saved: bool, total_present}` (status saves an expired session, as today). `total` = students
  enrolled on the session's date; `checked_in` newest first (`time` = check-in date-time),
  `not_checked_in` by student id. (`method` is `"FINGERPRINT"` only for the hidden devices.)
- POST /sessions/<id>/extend/ `{minutes: 2}` (1–28, default 2) → `{success, ends_at, time_left}`
  (max total 30 min: beyond it 400 `code: "too_long"`, "A session can last at most 30 minutes. You
  can add up to N more minutes."). 410 `session_ended` when it is over.
- POST /sessions/<id>/mark/ `{profile_id}` while live → adds the student to the live check-ins with
  method TEACHER (saved with the session) → `{success, message, student: {profile_id, student_id,
  name, time, method: "TEACHER"}}`. 400 `not_enrolled` (not a current member enrolled on the
  session's date), 409 `already_checked_in`, 410 `session_ended`. After the session ended, use
  section 6 corrections.
- POST /sessions/<id>/stop/ → saves now (exists) → `{success, message, session_id, total_present}`.
- POST /sessions/<id>/cancel/ → discards the session and its check-ins (the session is deleted:
  its status then answers 404); nothing is saved; 200. Already saved → 409 `code:
  "session_saved"` (delete its date in the history instead).
- Too many requests on one session at once → 503 `code: "busy"` (try again).
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
  With `session_id` (the QR link or the in-app scanner) the method is QR; without it, CODE. An
  unknown or cancelled `session_id`, or one of a deleted course or semester → 410 `session_ended`;
  a typed code that matches none of the student's own live sessions → 400 `code_invalid`. `code` is
  a string (spaces and dashes are ignored; digits of other scripts, e.g. Bengali ০–৯, count as 0–9);
  `device_id` is required (400 field error). The live data keeps each check-in's method, time and
  device id.
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
`held` counts only class dates on or after the student's `joined_at` (and before `left_at`; on the
join day only a class that logged them, see section 3 "Same-day changes");
`percent` = attended / held (null when held = 0; never "Low" with no classes).

### GET /teacher/course-info/<id>/students/<profile_id>/
`{success, student: {profile_id, student_id, name, email, joined_at, left_at, attended, held,
percent}, days: [{date, status: "PRESENT"|"ABSENT"|null /* null = not enrolled yet */, method:
"QR"|"CODE"|"FACE"|"TEACHER"|null, changed_by: name | null, changed_at | null}]}`.

### GET /sessions/course-info/<id>/history/ (exists, extended)
`{success, course_info_id, history: [day]}`, newest first. Each day: `{date, sessions:
[{session_id, delivery, mode}], logs: [{profile_id, student_id, name, status, method, changed_by,
changed_at}]}`. `sessions` = that day's saved sessions (a roll call has none); `logs` = one row per
student (one date = one class: PRESENT if any log that day says so; method/changed_* from that
log), by student id. `?date=YYYY-MM-DD` and `?student_id=<profile_id>` narrow it (bad values →
400). In `days` and `logs`, `method` is how the student was marked present: QR, CODE, FACE, TEACHER
(roll call or correction-created logs: either status), or null (absent in a live/face session).
It follows the day's status (corrections keep the stored method): an absent day shows only
TEACHER or null (a QR check-in corrected to absent shows null); a present day whose log had no
method (absent in a live/face session, then corrected) shows TEACHER.
`changed_at` is a date-time; `changed_by` a name.

### PUT /teacher/course-info/<id>/attendance/
Body `{date, profile_id, status: "PRESENT"|"ABSENT"}` → changes **only that student** on a date that
already has a class. Keeps the original method, records `changed_by` and `changed_at`, and adds an
AttendanceChange row (old → new, who, when). 404 `code: "no_class_on_date"` when the date has no
class (use roll call). Future dates → 400 `code: "future_date"`. A student who was not a member on
that date → 400 `code: "not_enrolled"`. Every log of that student that day with another status
changes (several sessions a day); a student without a log that day gets one (method TEACHER, old
status ""). 200 `{success, message, changed: bool /* false = it was already so */, day: {date,
status, method, changed_by, changed_at}}`.

### POST /teacher/course-info/<id>/roll-call/  (replaces history-session POST)
Body `{date, present_profile_ids: [..]}`. Creates the class for that date if missing (source
TEACHER/MANUAL). For each student enrolled on that date: creates a log if missing; changes an
existing log only if its status differs (recorded like a correction). Never re-labels other methods.
Future dates → 400 `code: "future_date"`. 200 `{success, message, date, present, absent, changed}`
(`present`/`absent` = students enrolled that date; `changed` = students whose existing status
changed). Ids of students not enrolled on that date are ignored. Created logs: source MANUAL,
method TEACHER.

### DELETE /teacher/course-info/<id>/history-session/<date>/ (exists)
Deletes that date's classes and logs (the UI asks for confirmation) → `{success, message, deleted
/* logs */}`. 409 `session_running` while a live session runs on that date; a bad date → 400.

All section 6 endpoints answer 404 for a deleted course, course-info or semester, and 403 (`code:
"permission_denied"`, `You do not teach this course.`) for another teacher's course (as do the
section 5 session endpoints and the export). The course list is ordered like the
semesters list, then by course code. The student endpoint answers 404 for a student who was never
in the course's class group.

## 7. Student

### GET /student/<user_or_profile_id>/semesters/ (exists, fixed)
For the student themself or an admin (403 `permission_denied` for another student and for
teachers: they see students through their own courses, section 6).
Only the student's **own** class group's courses; semesters sorted by level then term (First,
Second, Third, Fourth; I, II); percentages count from `joined_at`. Per course adds
`{course_info_id, held, attended, percent, below_min, classes_needed /* to reach the minimum; 0 if
already there */}` and per semester `{label, is_active, overall_percent, joined_at, left_at}`
(their membership: semesters they left are still listed, with `left_at` set = history only, not
current). `held` = the course's class dates inside the membership (a class date without a log for
them counts as absent; on the join day only a class that logged them, section 3); `overall_percent` = attended / held over all the semester's courses (null
when nothing is held); `classes_needed` = classes in a row to attend (null if the minimum can never
be reached, e.g. a 100% minimum). The older keys stay with the same numbers (`totalClasses` = held,
`presentCount` = attended, `percentage`, `history` over the held dates, oldest first).

### GET /student/course-info/<id>/
`{success, course: {course_info_id, code, title, credits, teacher_name, semester: {label,
is_active}}, attended, held, percent, classes_needed, days: [{date, status, method, changed: bool}]}`
for the signed-in student only (403 `code: "not_enrolled"` if they were never in its class group;
former members still see it). `days`: every class date of the course, newest first; status null =
outside their membership; `changed` = a teacher corrected it.

## 8. Faces, reports

- Faces: as today. Course detail and course list carry face registration counts (section 6).
- GET /reports/course-info/<id>/export/?format=pdf|xlsx|csv|docx&date=YYYY-MM-DD (exists).
  Fixes: generated time in Asia/Dhaka; join dates respected (not enrolled = blank, not absent); the
  PDF splits the date grid across pages so ID, name and % are always visible; header has course,
  semester label, department and teacher.
  Details: `format` defaults to xlsx (the old app's `export_format` still works); another value →
  400 `code: "invalid_format"`, a bad `date` → 400 `invalid_date`; 404 / 403 as in section 6 (admins
  may export any course). Rows = the course's class list by student id; one column per class date
  (oldest first): PRESENT / ABSENT, blank outside the student's membership (and on their join day
  for a class held before they were added, section 3); then held, present and
  percent counted as in section 6 (none held = blank / "-"). With `date`: one Status column (blank
  also when that date had no class). PDF and DOCX (landscape A4) show P / A with a legend and
  split the dates into pages of about 15, each repeating #, student ID, name, held, present and %
  (below the minimum in red); the header block (course, semester label, department, teacher,
  classes, generated time) is on the first page and a short running header on the others. XLSX has
  the same header block and keeps #, ID, name and email in view while scrolling. **CSV stays a
  plain table** (one header row, full words) so it opens cleanly in other programs.

## 9. Data changes (migrations, append-only)

- users: data migration sets `is_verified = true` for every existing user (they are already using
  the app). Admin-created and imported users are created verified.
- academic: `Semester.session` (CharField, blank); `StudentClassroom.joined_at` (DateField, null =
  "from the start", which is what existing rows get) and `left_at` (null) (0002); data migration
  0003 gives every semester without a class group an empty "Main" one.
- Management command `backfill_join_dates` (run once after migrating; dry run by default,
  `--apply` writes): existing rows got joined_at null, so students added mid-semester under the
  old app would count as absent for every class before they joined. The old app logged every
  member whenever a class was saved, so for each membership with joined_at null (class dates
  before its `left_at` only): joined_at = the student's first log date in the class group's
  courses when the group held classes before it; with no log at all but classes held, the last
  class date (it does not count for them without a log); otherwise it stays null. The dry run
  lists student, semester, the new date and held/percent before → after. Limits: an old class
  re-saved later also logged that time's late joiners, so a guess can be earlier than the real
  join (it never hides a class they have a log for).
- attendance: `AttendanceSession.delivery` (IN_CLASS default); `AttendanceLog.changed_by` (FK user,
  null, SET_NULL) and `changed_at` (null); new `AttendanceChange` (log FK, old_status, new_status,
  changed_by, changed_at) (0003). Also `AttendanceLog.method` (QR | CODE | FACE | TEACHER |
  FINGERPRINT | "" = not checked in) so saved days can show QR vs typed code; data migration 0004
  fills it for older logs (source MANUAL → TEACHER; present logs of QR / face / fingerprint
  sessions → QR / FACE / FINGERPRINT; other absent logs stay "").
- Management command `cleanup_broken_signups`: lists STUDENT/TEACHER users without a profile (made
  by the old sign-up bug); dry run by default, `--apply` removes them.

## 10. Settings

- Email (Gmail SMTP): `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (Gmail app password),
  `DEFAULT_FROM_EMAIL`; host smtp.gmail.com:587 TLS. Empty `EMAIL_HOST_USER` = email reset off.
- `WEB_URL` (https://attendanceportal.sakibkx.tech) for links in emails and QR codes.
- `MIN_APP_VERSION`, `LATEST_APP_VERSION`, `ATTENDANCE_MIN_PERCENT` (75).
- Throttle rates: `THROTTLE_LOGIN`, `THROTTLE_REGISTER`, `THROTTLE_PASSWORD_FORGOT`, `THROTTLE_CHECK_IN`
  (defaults as in Conventions; raise them if a class behind one campus IP gets blocked). `NUM_PROXIES`
  (production, default 1 = nginx) picks the visitor IP from `X-Forwarded-For`.
