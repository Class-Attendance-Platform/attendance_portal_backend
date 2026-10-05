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

## 1. Get the code

```bash
cd /var/www && git clone https://github.com/Class-Attendance-Platform/attendance_portal_backend.git attendanceportal-backend
```

```bash
cd /var/www && git clone https://github.com/Class-Attendance-Platform/attendance_portal_frontend.git attendanceportal-frontend
```

## 2. Database

Create the database user (you will be asked to type a new password; keep it for `.env`):

```bash
sudo -u postgres createuser --pwprompt attendanceportal
```

```bash
sudo -u postgres createdb --owner attendanceportal attendanceportal
```

### Copy the existing data from Neon

Paste your Neon connection string when asked (it is not shown on screen). Find it in the Neon
dashboard: Connection Details → connection string, e.g. `postgresql://user:password@host/db?sslmode=require`.

```bash
read -rs NEON_URL
```

```bash
pg_dump "$NEON_URL" --format=custom --no-owner --no-privileges --file=/root/neon-attendance.dump
```

Restore it into the new database (asks for the `attendanceportal` password):

```bash
pg_restore --no-owner --no-privileges --host=127.0.0.1 --username=attendanceportal --dbname=attendanceportal /root/neon-attendance.dump
```

Messages about objects that cannot be created (for example Neon-only extensions) can be ignored;
send the output if unsure. Afterwards, reset the Neon password or delete that Neon database: its
old password is public in the frontend repo.

## 3. Backend

```bash
cd /var/www/attendanceportal-backend && python3 -m venv venv
```

(If that says `ensurepip is not available`, run `sudo apt install python3-venv` and repeat it.)

```bash
cd /var/www/attendanceportal-backend && venv/bin/pip install -r requirements.txt
```

Create `.env` from the example and fill in your values (secret key, database password):

```bash
cd /var/www/attendanceportal-backend && cp .env.example .env && nano .env
```

```bash
cd /var/www/attendanceportal-backend && chown root:www-data .env && chmod 640 .env
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

If there is no admin account yet (the copied data may already have one):

```bash
cd /var/www/attendanceportal-backend && venv/bin/python manage.py createsuperuser --settings=config.settings.production
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

## Troubleshooting

```bash
journalctl -u attendanceportal-api -n 100 --no-pager
```

```bash
sudo tail -n 50 /var/log/nginx/error.log
```
