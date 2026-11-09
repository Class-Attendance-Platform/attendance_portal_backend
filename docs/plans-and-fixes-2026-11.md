# Plans and fixes, November 2026

This is the record of the work on branch `claude/beautiful-mccarthy-xlseq5` in both repos, on 3 and 4 November 2026: every plan, the decision taken on it, and every fix. The same file is in the backend and frontend repos.

**Where it stands (9 November 2026):** every fix below is committed and pushed, and CI passed on both pull requests. Neither pull request is merged and nothing is deployed, so the live site has none of this yet. Two more fixes are planned but not started: see [Planned, not started](#planned-not-started).

## Commits and pull requests

| Commit | Repo | What |
|---|---|---|
| `62dcbce` | frontend | Stop tracking `.env` and `credentials.txt` |
| `ff24087` | backend | Promote refuses a finished target semester |
| `85f84c4` | frontend | Promote greys out finished semesters |
| `abe2017` | frontend | Admin pages refresh quietly when you come back to them |
| `37981cf` | backend | QA fixes: exports, sign-in limits, approvals, import, deploy safety |
| `51c3bce` | frontend | QA fixes: admin pages, sign-in pages, desktop app |
| `a278c7b` | frontend | Android: a version tag needs the release signing key |

Pull requests, which also carry the redesign commits from the earlier cloud session:

- [Backend #3](https://github.com/Class-Attendance-Platform/attendance_portal_backend/pull/3), into `main`
- [Frontend #2](https://github.com/Class-Attendance-Platform/attendance_portal_frontend/pull/2), into `master`

| After | Backend tests | Frontend tests | Frontend type check |
|---|---|---|---|
| Local setup | 422 pass, 1 skipped | 123 pass | clean (was 61 errors) |
| Promote fix | 423 pass, 1 skipped | 123 pass | clean |
| Admin refresh | 423 pass, 1 skipped | 133 pass | clean |
| QA fixes | 444 pass, 1 skipped | 133 pass | clean |

The skipped test is the real face engine test, which needs the face models downloaded.

## Plans and decisions, in order

### 1. Survey of the cloud branch (3 Nov)

**Asked:** pull both repos from the cloud session's branch, study everything and organise the remaining work.

**Done:** read the code and every `.md` file, ran the checks, and compared the frontend's API calls with the backend. No files changed.

**Found:** the redesign code looked finished, but it was not merged or deployed (backend 19 commits ahead of `main`, frontend 27 ahead of `master`). The laptop setup was out of date.

**Remaining work, as listed then:**

1. `credentials.txt` is tracked in the frontend repo.
2. Fix the local setup and get a real test run.
3. Bug: Promote (moving students to their next semester) accepts a finished semester.
4. Bug: admin pages show old numbers when you go back to them.
5. No QA pass yet on the admin area, sign-in, the account page and the desktop app.
6. Ship: pull requests (backend first), then deploy backend and web back to back while no class is running, with a database backup before the 5 migrations.
7. Android: a signed release and a test on a real phone.
8. Later: tune the face thresholds with real classroom photos.

### 2. Local setup (3 Nov)

**Plan:**

- Backend: rename the broken `venv` to `venv.old-machine` (nothing deleted), build a new one with Python 3.13 from `requirements.txt`, then run `check`, the migrations check and the full tests with `--settings=config.settings.test`.
- Optional: download the face models.
- Frontend: `npm ci`, generate fresh route types (the old file renamed aside), then run the type check and `npm test`.

**Decision:** Go, without the face models.

**Result:** backend 422 tests pass, 1 skipped. The frontend type check went from 61 errors to none, and 123 tests pass. No tracked files changed.

### 3. `credentials.txt` and `.env` (3 Nov)

**Found:** both files were tracked in the public frontend repo and should not have been.

**Plan:** stop tracking both (`git rm --cached` and `.gitignore`), keep the local copies, and update the note in the frontend `CLAUDE.md`. `.env.production` stays tracked, because the Android build uses it. Git history is not rewritten, because the project rules forbid a force-push.

**Decision:** Go, for both files.

**Result:** `62dcbce`. Changing the affected passwords is the owner's job; its status is deliberately not recorded in this public file.

### 4. Promote into a finished semester (3 Nov)

**Bug:** the API never checked whether the target semester was finished. The students ended up only in a finished semester, and the source semester was finished too.

**Plan:**

- Backend: refuse a finished target before anything changes, both when it is picked and when "A new semester" matches it. Nothing is reopened automatically.
- Tests for both ways in, and the new error in `docs/api-v2.md`.
- Screen: either hide finished semesters or grey them out.

**Decision:** grey them out. (Hiding was the suggested option.)

**Result:** `ff24087` and `85f84c4`.

### 5. Admin pages showing old numbers (3 and 4 Nov)

**Problem:** coming back to an admin page that was still open (Back, a breadcrumb or the section's nav item) never reloaded it.

**Plan**, after mapping all 11 admin screens and what a reload could break:

- Add an opt-in "reload on return" to the admin load hook: quiet (no spinner), retries an error screen, and never undoes a change just made on screen.
- Turn it on for Overview, Approvals, Students, Teachers, Courses, Semesters, the semester page and its Students, Courses and Promote tabs, the Attendance list and course pages, and the Import page's semester list. Lists inside dialogs already load each time they open.
- Guards so a reload cannot lose work: Promote keeps the ticks, the Edit semester form fills in only when it opens, a chosen semester that left the list is dropped, and a failed semester list no longer looks empty on Approvals.
- Put the rules in a small pure file with tests, check on screen, and get an independent review.

**Decision:** Go, with failures kept silent (the old numbers stay, no message).

**Left for later at that point:** reloading when you switch back to the browser tab or window (now [planned](#planned-not-started)), Promote always sending exactly the ticked students, and the backend import accepting a finished semester (both done in the QA round).

**Result:** `abe2017`.

### 6. QA of the admin area, sign-in, account and desktop (3 and 4 Nov)

A read-only QA pass confirmed 46 findings, which merged into 39. One more was ruled out.

**Decisions:**

| Question | Choice |
|---|---|
| Which findings to fix | Everything: all 39 |
| Should the student import refuse a finished semester, like Promote | Yes, refuse it |
| Adding students to a finished semester when they are in another active one | Keep refusing, as today |
| Django's own admin site at `/admin/`, unused by the app | Remove the route |
| Admin tables in windows about 768 to 1000 px wide | B: phone rows below 1100 px (the other option was a table that scrolls sideways) |

**Plan:** three batches, with tests after each, an independent review before committing, and no push until the owner says so.

- **Batch A, backend:** names and exports, the `/admin/` route, sign-in limit keys, Reject, password limits, the import rule, smaller fixes, server config files, docs, and a test for each change.
- **Batch B, frontend:** Import, Promote and Reopen, Courses and the status filters, one-time password dialogs, four known small items (reset-password links, late answers in dialogs, the roster following Finish and Reopen, face reset progress), the admin course page, the register page, accessibility, the desktop app and the table layout.
- **Batch C, build:** a release tag without the signing secrets fails.

Every item was done: see [Every fix](#every-fix).

**Deliberately not changed:**

- Adding students to a finished semester (the decision above).
- One-time sign-outs when a sign-in upgrades the stored password hash.
- Android temporary passwords made with `Math.random`, because the fix needs a new native package. This is now [planned](#planned-not-started).

**Result:** `37981cf`, `51c3bce` and `a278c7b`.

### 7. Push, pull requests and Auto-fix (4 Nov)

- All commits were pushed when the owner said so.
- Backend #3 and frontend #2 were opened, each linking to the other.
- CI passed on both: the backend tests, and the frontend web, Android and Windows and Linux desktop builds plus the Vercel preview.
- Auto-fix is on for both, so CI failures, merge conflicts and review comments wake the session. Nothing merges automatically.
- The only comment so far was Vercel's automatic status note, which needed no change.

## Every fix

### Backend: Promote (`ff24087`)

- Promote refuses a finished target semester with 400 `semester_finished`, whether it is picked or "A new semester" matches it. Nothing moves and the source stays active. (`apps/academic/views/admin.py`)

### Backend: QA (`37981cf`)

**Names and exports**

- Names refuse control characters on sign-up, profile edit, admin edit and import, with a readable message. (`apps/users/serializers.py`, `apps/users/importing.py`)
- Exports drop characters that XML cannot hold, so a name already stored cannot break an Excel or Word file. (`apps/reports/generators.py`)
- The export file name keeps only letters, digits, `-` and `_`.

**Sign-in and passwords**

- Django's admin site is gone; `/admin/` answers 404. (`config/urls.py`)
- Sign-in and sign-up limit keys hold a digest of the email, normalised the way the database matches emails. (`apps/users/throttles.py`)
- Password change is limited to 10 tries an hour per user (`THROTTLE_PASSWORD_CHANGE`).
- Password reset sends one email per account in 15 minutes (`PASSWORD_RESET_EMAIL_INTERVAL`), in the background after the answer. A failed send does not count, so the next request can try again. (`apps/users/services.py`)
- A refresh token blacklisted by a password change answers `password_changed`, so the device can say why it was signed out. (`apps/users/jwt.py`)

**Approvals**

- Reject leaves an account that was approved meanwhile alone: 409 `already_approved`.
- Approve and Reject write only if the account is still as they read it, so when both land together the second gets a clear refusal. (`apps/users/views/admin.py`)

**Student import**

- Refuses a finished semester with 400 `semester_finished`.
- Reads CSV files saved by Excel correctly (UTF-8 first, then Windows-1252).
- Marks rows that belong to a deleted account.
- The admin's "already exists" errors name a deleted account and say where to restore it. Public sign-up never does.

**Request handling**

- A JSON body that is not an object answers 400 `parse_error` on every endpoint, through one shared parser. (`config/parsers.py`)
- The hardware endpoint's `device_name` must be text. (`apps/hardware/views/hardware.py`)

**Server and docs**

- HSTS (browsers always use HTTPS) is on by default for the API and in the web app's nginx file. Subdomains and preload stay off, so other `sakibkx.tech` sites are not affected.
- A daily timer removes expired sign-in tokens at 03:30 Dhaka time. (`deploy/attendanceportal-flush-tokens.service` and `.timer`)
- `deploy/README.md` has the timer install, the three `check --deploy` warnings to expect (W005, W008, W021), and update notes steps 6 and 7.
- `.env.example` has the new keys, with comments on their own lines.
- `docs/api-v2.md` and `CLAUDE.md` describe all of the above.

### Frontend: tracked secrets (`62dcbce`)

- `.env` and `credentials.txt` are no longer tracked and are now ignored. The local copies stay.

### Frontend: Promote (`85f84c4`)

- Finished semesters are greyed out in the "An existing semester" list, read "Finished · reopen it first", and cannot be chosen.
- Options in the shared select field can now be disabled. (`components/ui/select.tsx`, `docs/design-system.md`)

### Frontend: admin refresh (`abe2017`)

- The admin `useLoad` hook has `refreshOnFocus`: coming back reloads quietly, a failure keeps the old data, and an answer older than a change made on screen is dropped. (`components/admin/hooks.ts`, `components/admin/rules.ts`)
- An error screen tries again on return, and a return during a running request refreshes once that request answers.
- It is on for every admin page list, not for lists inside dialogs.
- Promote keeps the admin's ticks, minus students who left. Select all means everyone, including students who join later.
- The Edit semester form fills in only when it opens.
- Import, Attendance and Promote drop a chosen semester that left the reloaded list.
- The attendance course page keeps the teacher name when that lookup fails.
- Approvals no longer turns a failed semester list into an empty one.

### Frontend: QA (`51c3bce`)

**Student import** (`app/(app)/admin/students/import.tsx`)

- The one-time passwords stay guarded while the import runs and after "Download as CSV" (the question becomes "Did the passwords file save?").
- A file changed after it was chosen must be chosen again, and the page says so.
- Controls lock while a check or an import runs, so a late answer cannot show under another file.
- Errors show next to the Add button.
- The result card names the semester that was actually sent.
- A chosen semester that was finished or deleted meanwhile gets a notice, instead of a silent switch to "Don't add to a semester". A `semester_finished` refusal keeps the choice.
- The confirm always says whether the students join a semester.
- An import confirmed after the page closed is not sent.

**Promote and Reopen** (`components/admin/semester-promote.tsx`, `components/admin/semester-dialogs.tsx`)

- Promote always sends exactly the ticked students.
- Errors on the new semester's fields show on those fields.
- Promote is off when there is nothing to choose, and so is Add course.
- Refusals list every blocking student in a notice that stays. Promote also unticks them.
- Promote reloads when its target was finished or deleted meanwhile.
- Reopen on the Semesters list scrolls up to its notice.

**Approvals**

- A sign-up approved, rejected or removed meanwhile drops off the list, with a message.

**Lists and dialogs**

- Courses, Students and Teachers: switching Status shows a spinner, not the other view's rows or error.
- Courses: Cancel on a delete keeps the unsaved edits.
- One-time password dialogs (new student, new teacher, set password) close only with Done.
- Face reset asks on top of the open dialog and shows its progress.
- The day and face dialogs drop late answers.
- The roster's former members follow Finish and Reopen until the admin chooses.
- A refresh that finds the item gone (403 or 404) shows that error instead of old data.
- The low-attendance tile says "No classes yet" when none have been held.

**Table layout** (`lib/theme.ts`, `hooks/useBreakpoint.ts`)

- Admin lists show as phone rows below 1100 px and as tables above, on Approvals, Semesters, Import (also its two-column layout), the semester's courses and roster, and every `ResponsiveList`.
- The rows show what the tables show: the semester on Students, the email on the roster.

**Accessibility**

- Screen readers hear the whole row: status, email, last sign-in, join and leave dates, and the numbers.
- Space and the arrow keys work on the sign-up role choice and Promote's choice cards.

**Sign-in pages**

- Reset-password links sign out another account first. (`app/(auth)/reset-password.tsx`)
- "Back to sign in" is a real button, so Enter works.
- A doubled `role` in the sign-up link no longer crashes the page. (`app/(auth)/register.tsx`)

**Desktop app** (`desktop/main.js`)

- Navigation uses Electron's `event.url`, with the old argument as a fallback.
- "Try again" on the offline page returns to the page that failed, if it is the app's own site.

### Frontend: build (`a278c7b`)

- A `v*` tag build without all four `ANDROID_*` signing secrets now fails with a clear error, so a release is never signed with the public debug key. Pull request and manual builds keep the debug key. (`.github/workflows/android.yml`)

## How it was checked

**Tests.** Backend tests always ran with `--settings=config.settings.test` (local SQLite), so the production database was never touched. These new tests were confirmed to fail without their fix: Promote, export names, the failed reset email, and look-alike letters in the limit keys.

**Independent reviews.** Each finding was checked by a second reader who tried to disprove it. All confirmed findings were fixed:

- Admin refresh: two timing problems (a semester tab that loaded while its page was hidden, and coming back during a running request), plus what Select all should mean.
- Backend QA: nine findings, two of them the same, so eight fixes: the failed reset email, look-alike letters, Approve and Reject at the same moment, bodies that are not JSON objects, the deploy guide's warnings, and three corrections to `docs/api-v2.md`.
- Frontend QA: eleven findings, all included in the list above.

**On screen,** in a local web build with the demo data. The Expo dev server's error panel blocks the in-app browser, so these used a production web build pointed at the local backend.

- Promote: a finished semester is greyed out and cannot be chosen, and "A new semester" that matches one is refused.
- Admin refresh: removing a student and pressing the breadcrumb showed 11 instead of a stale 12. Each list loaded once on opening and once on each return. Promote kept two unticked students after leaving and pressing Back.
- Layout at 900 and 1280 px.
- Cancelling a course delete keeps the edits.
- The reset-password links, by click and with Enter.
- The sign-up link with `role` twice, and the arrow keys on the role choice.
- The error on Promote's Session field.
- The Students rows and their screen-reader labels.

**Not checked:** the Android app on a phone, the Electron desktop app itself, the real face engine, Import's layout switch on screen, and the live site (nothing is deployed).

## Planned, not started

The owner chose these on 4 November. They are waiting for a "Go". Frontend only, pushed to the open pull requests when done.

**Secure temporary passwords on Android.** Decision: add `expo-crypto`.

- The app installs no secure random package, so on Android the new-student, new-teacher and set-password dialogs fall back to `Math.random`, unless the JavaScript engine provides a secure source (not tested on a device).
- Add `expo-crypto` 55.0.x, the version for Expo SDK 55. Android links it automatically, so no `expo prebuild`.
- Temporary passwords use it on every platform, the `Math.random` fallback goes, and every allowed character becomes equally likely.
- The device id that check-ins send uses it too.
- A new test covers the password rules: 10 characters, at least 2 letters and 2 digits, no look-alike characters.

**Refresh when you come back to the tab, window or app.** Decision: admin and teacher pages.

- Student pages already do this. That code moves to a shared file, and student pages keep working as before.
- Admin pages: coming back does the same as going Back to the page. It reloads quietly, an error screen tries again, and it never undoes a save on screen.
- Teacher pages (courses, course, student, roll call, history) reload quietly. Roll call keeps unsaved changes, as it already does on Back.
- A new test covers it: coming back reloads once, and clicking into a window you never left reloads nothing.

**Checks for both:** type check and tests; in a local web build, the Add student password and one quiet reload after a tab switch; then the Android build in CI. The Android app on a real phone stays part of the phone test below.

## Still to do

**Deploy, by the owner.** Old and new versions do not work together, so pick a time when no class is running and do the backend and web back to back.

1. Back up the database. The deploy guide's update notes do not say this yet.
2. Merge backend #3.
3. Run steps 1 to 4 of "Update notes: API v2" in `deploy/README.md`.
4. Merge frontend #2. Merging into `master` also makes a Vercel production deploy, so do this after step 3.
5. Run steps 5 to 7 of the same notes.
6. Say "check now" for a read-only check of the live site.

Two things to know:

- The deploy runs 5 migrations. One marks every existing account approved, and reversing it does not undo that.
- The next `git pull` of the server's frontend checkout removes `.env` there. Only the one-time Neon copy step in the deploy guide reads that file, so finish that step first if it is still needed.

**Also:**

- Android: a signed release, then a test on a real phone.
- Tune the face thresholds with real classroom photos.
- `npm audit` reported known issues in third-party packages on 3 November. They have not been reviewed.
