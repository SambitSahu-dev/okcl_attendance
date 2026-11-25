# AI Employee Attendance System

## Initial setup

Requires Python 3.11 or newer (tested with Python 3.11.9).

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `SECRET_KEY` in `.env` to a generated value before starting the app:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env` to choose the default admin
ID and password before running the seed command. If the username is omitted it
defaults to `admin`; if the password is omitted the seed command generates a
one-time password and prints it.
Set both `SUPER_ADMIN_USERNAME` and `SUPER_ADMIN_PASSWORD` in `.env` to
bootstrap the super-admin account. The seed command never prints the password;
existing users promoted by username keep their existing password. Configure
`ADMIN_CONTACT_EMAIL` to show the support contact on the dashboard.
Generate a Fernet key for encrypted face embeddings and keep it stable across
restarts and backups:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the result as `FACE_EMBEDDING_KEY` in `.env`. The application refuses to
enroll or scan faces without a valid key.

## Run

```powershell
python -m scripts.seed
python -m uvicorn app.main:app --reload
```

The SQLite database is created on first app startup with WAL mode enabled.
The public home page `/` shows only the aggregate Inside Office, Out of Office,
and Absent counts for today; it does not show employee names or records. It
refreshes the counts every 30 seconds, including for signed-in visitors. The
role-specific dashboard at `/dashboard` requires sign-in; sign-in is available
at `/login`, and the GATE page is `/attendance`. GATE checks face match,
single-blink liveness, and hand gesture together from the live camera stream. A
thumb automatically checks an employee in; an open palm checks them out. There
are no separate check-in/check-out buttons. Scans alternate: an employee must
check out before checking in again; repeated same-action scans are rejected.
The daily record keeps the first and latest check-in separately. A new check-in
clears the previous check-out; the next check-out becomes final. Monthly reports
calculate the interval from the first check-in to the final check-out. The scan
API does not accept an employee ID, gesture assertion, liveness assertion, or
client timestamp. Only a successful first check-in stores the day's watermarked image
and SHA-256 hash.

After sign-in, `/dashboard` shows the admin attendance tiles and same-day IN/OUT
table, or the employee's own profile and attendance history. Inside Office
means logged in without a logout; Out of Office means logged in with a final
logout; Absent means no login on a working day. Instant Support displays the
configured admin contact email and has no action.

Standard Admin, Super Admin, and HR can create and edit employee records and
enroll faces. Super Admin manages Accounts, Team Lead, Audit, HR, and standard
admin accounts. Accounts can export reports, Team Lead has read-only attendance
views, and Audit has read-only attendance and audit-history views. Standard
admins can edit unlocked attendance and manage holidays. Attendance corrections
always require a reason and create an audit row with old/new values and actor.
Super Admin manages month locks; HR and Super Admin can still edit locked months,
with the same required audit trail.

The admin screens are `/admin/attendance`, `/admin/employees`,
`/admin/enrollment`, `/admin/holidays`, `/admin/month-locks`, `/admin/users`,
`/admin/reports`, `/admin/settings`, and `/admin/profile`. Admin pages share
role-aware navigation; GATE remains available as a dashboard action, not an admin
navigation item. Admins can update their username, display name, and email, and
optionally change their password from `/admin/profile`; the current password is
required to save. Super Admin uses `/admin/users` to manage other admin logins.
Employee login accounts are created together with employee records, and employees
can view only their own profile and history. Employee passwords must have at
least 8 characters.
Enrollment requires explicit consent and 3-10 camera captures; images are only
processed in memory, and embeddings are encrypted before storage. The standalone
HTTP authentication API is `/auth/login`, `/auth/logout`, and `/auth/me`.
The **Add Employee** flow creates the profile and login first, then opens the
camera capture step. After consent, one click captures 10 guided face angles at
one-second intervals; the employee follows the on-screen prompts. **Train the
system** creates that employee's encrypted face embeddings. Incomplete training
can be resumed later from `/admin/enrollment`.
The employee directory can be filtered by active, inactive, open enrolment, and
deleted status. Delete first deactivates an employee and removes biometric data
while retaining history. **Permanently Delete** is available in the Deleted
Employee filter and irreversibly removes the employee, linked attendance and
audit records, login, images, generated reports, and stored backups.
Admins can manage the late cutoff and weekly offs under
`/attendance/admin/settings`, holidays under `/attendance/admin/holidays`, and
the face match threshold under `/attendance/admin/settings/face-match-threshold`.
Standard Admins and Super Admins can set the late-arrival cutoff at
`/admin/settings`; the cutoff is interpreted in UTC. It is unset by default, so
no completed attendance day is marked late until an admin configures it.

Monthly Excel reports can be downloaded from `/admin/reports`. The workbook
includes derived Absent, Holiday, and Weekly Off rows and an `Edited Manually`
column. APScheduler runs on the server in UTC: absent marking at 23:50,
incomplete-day flagging at 23:55, the month-end report at 23:58 on the last day,
the SQLite-and-images backup at 00:30, and image/backup retention at 01:00.
Backups are ZIP files in `storage/backups/`; SQLite is snapshotted with its
online-backup API so WAL data is included. Images older than
`IMAGE_RETENTION_DAYS` (default 365) are removed from live storage while their
SHA-256 hashes and attendance summaries remain in SQLite. Backup ZIPs older than
`BACKUP_RETENTION_DAYS` (default 30) are removed. Set `LOG_LEVEL` to configure
console logging. Scan-start and frame-request limits are configurable with
`SCAN_START_LIMIT_PER_MINUTE` (default 10) and
`SCAN_FRAME_LIMIT_PER_MINUTE` (default 400).

Run one Uvicorn worker: APScheduler, the rate limiter, and active vision scans
are process-local. The scan limits are per source IP and process, so multiple
kiosks behind the same address share the configured limits. The application
does not trust `X-Forwarded-For`; behind a reverse proxy, all requests may share
the proxy address unless trusted-proxy handling is configured separately.

## Fixed kiosk setup (Windows)

1. Use a dedicated Windows account on the kiosk PC. Connect and test the webcam,
   keep the PC awake, and disable screen savers and sleep while the kiosk is in
   service.
2. Configure `.env`, seed the admin and kiosk, and start the app as described
   above. For camera permissions, use `http://localhost:8000/attendance` on the
   kiosk PC or serve the page over HTTPS when accessing it remotely.
   For unattended use, start `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
   as a Windows scheduled task or service before launching the browser; omit
   `--reload` and keep a single worker.
3. Open the GATE page in Chrome kiosk/full-screen mode. Select **GATE** to turn
   on the camera and begin automatic scanning. Employees keep their face visible,
   complete the blink prompt, and show a thumb for check-in or an open palm for
   check-out. The live activity list appears beside the camera; select
   **CLOSE GATE** to stop the camera. For a standard Chrome installation, run
   this from PowerShell:

   ```powershell
   & "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" --kiosk http://localhost:8000/attendance
   ```

   To start the kiosk automatically, place a shortcut to that command in the
   Windows Startup folder for the dedicated account.
4. Synchronize both the kiosk PC and application server clocks. In Windows,
   enable **Set time automatically**, select **Sync now** under Date & time, and
   verify the time zone. The server supplies attendance timestamps; clock sync
   also keeps TLS certificates, logs, and scheduled jobs reliable.
5. Confirm the camera permission prompt is allowed for the kiosk origin, then
   test one login and logout with an enrolled employee. Set the late-arrival
   cutoff from `/admin/settings` if needed; enter the office cutoff as UTC.

## Test

```powershell
pytest
```

## Vision webcam test

Install the requirements, then download the ONNX files from the official
[OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
and [SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface)
model directories. Place `face_detection_yunet_2023mar.onnx` and
`face_recognition_sface_2021dec.onnx` in `models\vision\`. Model files are
ignored by Git. They can be downloaded from PowerShell with:

```powershell
New-Item -ItemType Directory -Force models\vision
curl.exe -fL https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx -o models\vision\face_detection_yunet_2023mar.onnx
curl.exe -fL https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx -o models\vision\face_recognition_sface_2021dec.onnx
```

The kiosk and enrollment pages require a browser with camera access. For local
development, use `http://localhost` or HTTPS so the browser permits camera
access. `KIOSK_ID` selects the registered kiosk used by the Attendance page.

Run the standalone webcam demo:

```powershell
python -m scripts.test_vision_webcam
```

Press `C` to reset the one-blink challenge and `Q` to quit. The demo prints
face counts, embedding dimensions (not embedding values), blink progress, and
detected hand gestures. Set `VISION_YUNET_MODEL`, `VISION_SFACE_MODEL`, and
`VISION_CAMERA_INDEX` to override the defaults.
