# Deploying to the VPS

Server: Ubuntu 26.04, nginx, PostgreSQL 18, Redis, certbot, Python 3.14, Node 22.

| What | Where |
|---|---|
| API | https://api.attendanceportal.sakibkx.tech → `/var/www/attendanceportal-backend` (systemd `attendanceportal-api`) |
| Web app | https://attendanceportal.sakibkx.tech → `/var/www/attendanceportal-frontend/web-build` (static, nginx) |
| Database | local PostgreSQL, database and user `attendanceportal` |
| Redis | local Redis, database 5 |

Run every command yourself, one block at a time, and check its output before the next one.
Nothing here touches the other sites on the server. Start after both pull requests are merged
(the server runs `main` for the backend and `master` for the frontend).

Run the commands as root: start with `sudo -i`.

## 0. DNS

At your domain provider, add two `A` records pointing to this server's IP address:
`api.attendanceportal` and `attendanceportal` (under `sakibkx.tech`). certbot (step 5) only works
once both names point here. Check (read-only; both lines should show the server's IP):

```bash
getent ahostsv4 api.attendanceportal.sakibkx.tech | head -1; getent ahostsv4 attendanceportal.sakibkx.tech | head -1
```

## 1. Get the code

```bash
cd /var/www && git clone https://github.com/Class-Attendance-Platform/attendance_portal_backend.git attendanceportal-backend
```

```bash
cd /var/www && git clone https://github.com/Class-Attendance-Platform/attendance_portal_frontend.git attendanceportal-frontend
```

## 2. Database and Redis

Create the database user (you will be asked to type a new password; keep it for `.env`):

```bash
sudo -u postgres createuser --pwprompt attendanceportal
```

```bash
sudo -u postgres createdb --owner attendanceportal attendanceportal
```

### Copy the existing data from Neon

The Neon login is in the frontend repo's `.env` (after step 1 it is on this server). This reads it
from there, uses Neon's direct connection (the host without `-pooler`, which Neon asks for with
`pg_dump`) and never shows the password; the settings exist only inside the brackets:

```bash
( envf=/var/www/attendanceportal-frontend/.env; get() { grep -E "^$1=" "$envf" | head -1 | cut -d= -f2- | tr -d "\r"; }; export PGHOST="$(get DB_HOST | sed "s/-pooler//")" PGPORT="$(get DB_PORT)" PGDATABASE="$(get DB_NAME)" PGUSER="$(get DB_USER)" PGPASSWORD="$(get DB_PASSWORD)" PGSSLMODE=require; echo "Copying from $PGHOST, database $PGDATABASE"; pg_dump --format=custom --no-owner --no-privileges --file=/root/neon-attendance.dump && ls -lh /root/neon-attendance.dump )
```

Restore it into the new database (asks for the `attendanceportal` password):

```bash
pg_restore --no-owner --no-privileges --host=127.0.0.1 --username=attendanceportal --dbname=attendanceportal /root/neon-attendance.dump
```

Messages about objects that cannot be created (for example Neon-only extensions) can be ignored;
send the output if unsure.

The old Neon password is public in the frontend repo, so check who can log in as admin
(read-only; send the output, and say if you don't recognise an account):

```bash
sudo -u postgres psql attendanceportal -c "SELECT email, role, is_superuser, is_active, date_joined, last_login FROM users_user WHERE is_superuser OR is_staff OR role = 'ADMIN' ORDER BY date_joined;"
```

The dump file holds every account and password hash: remove it once the restore worked.

```bash
shred -u /root/neon-attendance.dump
```

Then reset the Neon password or delete that Neon database.

### Redis check (read-only)

Database 5 must be unused by your other sites (expect `0`), and Redis must not delete keys when
memory is full (expect `noeviction`):

```bash
redis-cli -n 5 DBSIZE; redis-cli CONFIG GET maxmemory-policy
```

If it says `NOAUTH`, Redis has a password: put it in `.env` later as
`REDIS_URL=redis://:<password>@127.0.0.1:6379/5`. Send the output if either value is different.

## 3. Backend

A system user for the API, so other sites on this server can't read its settings (group
`www-data` lets nginx reach it):

```bash
useradd --system --gid www-data --no-create-home --shell /usr/sbin/nologin attendanceportal
```

```bash
cd /var/www/attendanceportal-backend && python3 -m venv venv
```

(If that says `ensurepip is not available`, run `sudo apt install python3-venv` and repeat it.)

```bash
cd /var/www/attendanceportal-backend && venv/bin/pip install -r requirements.txt
```

Create `.env` from the example, readable only by the API user, and fill in your values (secret
key, database password):

```bash
cd /var/www/attendanceportal-backend && install -m 600 -o attendanceportal -g www-data .env.example .env && nano .env
```

```bash
cd /var/www/attendanceportal-backend && venv/bin/python manage.py migrate --settings=config.settings.production
```

```bash
cd /var/www/attendanceportal-backend && venv/bin/python manage.py collectstatic --noinput --settings=config.settings.production
```

Face models (about 290 MB download once; 180 MB kept in `face_models/`):

```bash
cd /var/www/attendanceportal-backend && venv/bin/python manage.py download_face_models --settings=config.settings.production
```

```bash
cd /var/www/attendanceportal-backend && venv/bin/python manage.py check --deploy --settings=config.settings.production
```

(Two warnings about HSTS and SSL redirect are expected: nginx and certbot handle HTTPS.)

```bash
sudo cp /var/www/attendanceportal-backend/deploy/attendanceportal-api.service /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now attendanceportal-api
```

```bash
sudo systemctl status attendanceportal-api --no-pager
```

Your admin account (asks for its password twice; use your own email):

```bash
cd /var/www/attendanceportal-backend && venv/bin/python manage.py createsuperuser --settings=config.settings.production --email you@example.com --username you --role ADMIN
```

## 4. Web app

The API and web addresses are in the frontend's `.env.production` (built into the app).

```bash
cd /var/www/attendanceportal-frontend && npm ci
```

```bash
cd /var/www/attendanceportal-frontend && npm run build:web
```

## 5. nginx and HTTPS

```bash
sudo cp /var/www/attendanceportal-backend/deploy/nginx/*.conf /etc/nginx/sites-available/
```

```bash
sudo ln -s /etc/nginx/sites-available/api.attendanceportal.sakibkx.tech.conf /etc/nginx/sites-available/attendanceportal.sakibkx.tech.conf /etc/nginx/sites-enabled/
```

```bash
sudo nginx -t
```

Only if `nginx -t` says the test is successful:

```bash
sudo systemctl reload nginx
```

```bash
sudo certbot --nginx -d api.attendanceportal.sakibkx.tech -d attendanceportal.sakibkx.tech
```

certbot writes the HTTPS settings into the two files in `/etc/nginx/sites-available/`. Don't copy
the files from `deploy/nginx/` over them again (that removes HTTPS); change them in place instead.

## 6. Security check: PostgreSQL port

PostgreSQL listens on all addresses (`0.0.0.0:5432`). Check whether the firewall blocks it
(read-only):

```bash
sudo ufw status verbose; sudo -u postgres psql -tAc "SHOW listen_addresses;"
```

Send the output before changing anything: the fix depends on whether your other sites connect to
PostgreSQL from outside this server.

## 7. Phone and desktop apps

GitHub Actions in the frontend repo builds them: the Android APK, a Windows installer and a Linux
AppImage. The desktop app only opens https://attendanceportal.sakibkx.tech, so web updates reach it
without a new installer.

### Android signing key (once)

Android only installs an update if it is signed with the same key as the first version, so make
one key and keep it safe (a backup of the `.p12` file and its password). On the server:

```bash
cd /root && openssl req -x509 -newkey rsa:2048 -sha256 -days 10000 -nodes -keyout attendance-key.pem -out attendance-cert.pem -subj "/CN=Class Attendance Portal"
```

Pack it with a new password (asked twice; you use it for two of the secrets below):

```bash
cd /root && openssl pkcs12 -export -inkey attendance-key.pem -in attendance-cert.pem -name upload -out attendance-release.p12
```

The `.p12` now holds the key behind your password, so remove the unprotected copy:

```bash
cd /root && shred -u attendance-key.pem && chmod 600 attendance-release.p12
```

Print it as one line of text for GitHub:

```bash
base64 -w0 /root/attendance-release.p12; echo
```

In the frontend repo on GitHub: Settings → Secrets and variables → Actions → New repository secret.

| Name | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | the long line printed above |
| `ANDROID_KEYSTORE_PASSWORD` | the password you chose |
| `ANDROID_KEY_ALIAS` | `upload` |
| `ANDROID_KEY_PASSWORD` | the same password |

Without these secrets the APK is signed with the public debug key: fine for testing, not for students.

### Publishing a version

In the frontend repo on GitHub: Releases → Draft a new release → "Choose a tag" → type `v1.0.0`
→ "Create new tag on publish", target `master` → Publish release. The builds start by themselves
(about 20–40 minutes) and add three files to that release:

- `Class-Attendance-Portal-1.0.0.apk` (Android)
- `Class-Attendance-Portal-Setup-1.0.0.exe` (Windows)
- `Class-Attendance-Portal-1.0.0.AppImage` (Linux)

Share https://github.com/Class-Attendance-Platform/attendance_portal_frontend/releases/latest.
Use a higher number (`v1.0.1`, `v1.1.0`, ...) for each new version.

What users see the first time (the files are not store-signed):

- Android: allow "Install unknown apps" for the browser, and tap "Install anyway" if Play Protect asks.
- Windows: "Windows protected your PC" → More info → Run anyway.
- Linux: make the AppImage executable (`chmod +x`), then open it.

## Updating later

Backend (migrate and restart included):

```bash
cd /var/www/attendanceportal-backend && git pull && venv/bin/pip install -r requirements.txt && venv/bin/python manage.py migrate --settings=config.settings.production && venv/bin/python manage.py collectstatic --noinput --settings=config.settings.production && sudo systemctl restart attendanceportal-api
```

Web app (no restart needed; nginx serves the new files at once):

```bash
cd /var/www/attendanceportal-frontend && git pull && npm ci && npm run build:web
```

Only when `deploy/attendanceportal-api.service` changed (the hand-off says so):

```bash
sudo cp /var/www/attendanceportal-backend/deploy/attendanceportal-api.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart attendanceportal-api
```

## Removing an account

Shows the account and everything that goes with it, without changing anything:

```bash
cd /var/www/attendanceportal-backend && venv/bin/python manage.py remove_user someone@example.com --settings=config.settings.production
```

Then the same with `--apply` to remove it. Its login tokens stop working at once. It refuses to
remove the last active admin. If the dry run prints Django admin history you want to keep, save
that output first: it is deleted with the account. To only block a student or teacher, prefer the admin pages: removing
a student also removes their attendance records.

## Broken sign-ups

The old sign-up page could save an account without its student/teacher profile. Such accounts
cannot use the app and block their email from signing up again. This lists them, without changing
anything:

```bash
cd /var/www/attendanceportal-backend && venv/bin/python manage.py cleanup_broken_signups --settings=config.settings.production
```

Then the same with `--apply` to remove them.

## Email for password reset

"Forgot password" sends a reset link by email through a Gmail account. It is off until
`EMAIL_HOST_USER` is set; meanwhile the app tells people to ask an admin, and admins can always
set a temporary password on the admin pages.

1. Pick the Gmail account that sends the emails (a separate one for the portal is best) and sign
   in to it in a browser.
2. Turn on 2-Step Verification: Google Account → Security → 2-Step Verification. App passwords
   need it.
3. Open https://myaccount.google.com/apppasswords, type a name such as `Attendance Portal` and
   press Create. Google shows a 16-letter app password once: copy it. It only lets the portal send
   mail; it is not your Gmail login password. Remove it on the same page to switch email off.
4. Put it in the backend `.env` (on the server, as root):

```bash
cd /var/www/attendanceportal-backend && nano .env
```

Fill in these lines (they are already in the file if it came from `.env.example`). Type the 16
letters without spaces; keep comments on their own lines (text after a value becomes part of it):

```
EMAIL_HOST_USER=<gmail address>
EMAIL_HOST_PASSWORD=<16-letter app password>
WEB_URL=https://attendanceportal.sakibkx.tech
```

Restart the API so it reads `.env` again:

```bash
sudo systemctl restart attendanceportal-api
```

Send yourself a test email (use your own address; it should arrive within a minute):

```bash
cd /var/www/attendanceportal-backend && venv/bin/python manage.py sendtestemail you@example.com --settings=config.settings.production
```

If it fails, the error names the problem (usually a mistyped address or app password). The reset
links point to `WEB_URL` and work for one day, once.

## Update notes: API v2 (the redesign)

This release changes the database, so `migrate` is needed (the backend update command above runs
it). Existing accounts are marked approved; new sign-ups wait for an admin (Approvals page). Every
semester gets its hidden class group. The service file and the nginx files did not change.

1. Backend: pull, install, migrate, restart:

```bash
cd /var/www/attendanceportal-backend && git pull && venv/bin/pip install -r requirements.txt && venv/bin/python manage.py migrate --settings=config.settings.production && venv/bin/python manage.py collectstatic --noinput --settings=config.settings.production && sudo systemctl restart attendanceportal-api
```

2. New `.env` keys are optional (the defaults are fine): `WEB_URL`, `MIN_APP_VERSION`,
   `LATEST_APP_VERSION`, `ATTENDANCE_MIN_PERCENT`, the request limits and email (see
   "Email for password reset" and `.env.example`). Restart the API after changing `.env`.
3. Accounts left half-made by the old sign-up page: run the dry run in "Broken sign-ups" above,
   send the output, then the same with `--apply`.
4. Web app: rebuild it:

```bash
cd /var/www/attendanceportal-frontend && git pull && npm ci && npm run build:web
```

## Troubleshooting

```bash
journalctl -u attendanceportal-api -n 100 --no-pager
```

```bash
sudo tail -n 50 /var/log/nginx/error.log
```
