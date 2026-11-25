# Project: AI Employee Attendance System (Face + Gesture), Kiosk Prototype

## Goal
A lightweight, fully automated attendance system that runs on a fixed kiosk / office PC with a webcam.
Employees mark attendance using face recognition + hand gesture + eye-blink liveness.
Admins manage employees, holidays, corrections and reports.

## Tech stack (do not substitute without asking)
- Python 3.11+, FastAPI + Uvicorn, Jinja2 templates + vanilla JS (no heavy frontend framework)
- SQLite (WAL mode) via SQLAlchemy; Alembic optional
- OpenCV (YuNet face detector + SFace embeddings, ONNX models), MediaPipe (FaceMesh for blink, Hands for gestures)
- APScheduler for scheduled jobs
- openpyxl for Excel reports (PDF optional later)
- pytest for tests; ruff for linting
- Config via `.env` / `settings` table; no hard-coded secrets

## Core business rules (strict)
1. LOGIN = face recognised + THUMB gesture + liveness passed.
   - Only ONE login per employee per day. The FIRST login is final.
   - Any later login attempt is rejected with the message "Already logged in at HH:MM".
   - Enforce with a UNIQUE(employee_id, date) constraint, not just UI checks.
2. LOGOUT = face recognised + open PALM gesture + liveness passed.
   - Multiple logouts allowed. Every scan is stored in a logout log.
   - The LAST logout of the day (max timestamp) is the final logout in the daily record.
   - Logout with no login: block and record it as an exception.
   - Logout earlier than login: reject and log it.
3. Liveness: random blink challenge each time (e.g. "blink twice") using eye aspect ratio from MediaPipe FaceMesh.
4. All timestamps come from the SERVER clock, never from the client.
5. Saturdays and Sundays are weekly offs by default, but configurable in settings. Admin can add date-wise holidays.
   A login on a weekly off / holiday is recorded as "Present on holiday".
6. Daily status values: Present, Absent, Late, Incomplete (login but no logout), Holiday, Weekly Off, Present on Holiday.
7. Working hours = final logout - first login.

## Images and location
- Kiosk has no GPS. Each kiosk is registered in a `kiosks` table (kiosk_id, name, office/district, latitude, longitude).
- On a successful LOGIN, save ONE watermarked image (kiosk location lat/long, date, time, employee ID).
  Do not save images for logout scans.
- Store images at `storage/images/<employee_id>/<YYYY-MM-DD>.jpg`.
- In the database store only the file path and a SHA-256 hash of the image (tamper detection). Never store image blobs in SQLite.

## Face data
- Enrolment captures 3-5 images per employee, computes SFace embeddings, and stores the embeddings (encrypted at rest if feasible).
- Matching uses cosine similarity with a configurable threshold. Reject below the threshold.
- Enrolment requires an explicit consent checkbox, stored with a timestamp.

## Roles
- Employee: view own profile, own attendance history, own same-day record.
- Admin: everything below.
  - Manage employees and enrolment
  - View all attendance and same-day IN/OUT
  - Edit any attendance record (full edit rights)
  - Add or remove holidays
  - Manage kiosks and settings
  - Generate reports
- Passwords hashed with bcrypt/argon2. Session-based auth.

## Admin edit audit (mandatory)
Every manual edit writes to `attendance_audit`: who, when, old values, new values, reason (required).
Reports show an "edited manually" flag. Months can be locked, after which edits need a super-admin.

## Pages
- Home page: logo, title, nav (Dashboard, Login, Attendance)
- Attendance page (kiosk): live camera view, instruction prompts, after a scan shows name, employee ID, login time, last logout time
- Dashboard: live tiles for Inside Office, Out of Office, Absent, Instant Support, plus same-day IN/OUT table
- Employee login view and Admin login view (as per the roles above)
- Admin pages: employees, enrolment, holidays, attendance edit, reports, kiosks, settings

## Scheduled jobs
- End of day: mark Absent for employees with no login (skip holidays/weekly offs); flag Incomplete days
- Last day of month: generate the monthly Excel report
- Nightly: back up the SQLite DB and images
- Retention job: archive or delete images older than the configured retention period (keep hash + summary)

## Database tables (starting point)
employees, face_embeddings, consents, kiosks, attendance_daily, logout_log, holidays, attendance_audit, users, settings, scan_attempts (failed or rejected scans with reason)

## Project structure
app/ (main.py, config.py, db/, models/, services/, routers/, templates/, static/), vision/ (detector, embedder, liveness, gestures), jobs/, tests/, storage/, scripts/

## Working rules for you (the coding agent)
- Work in the phases I give you. Do not build ahead.
- After each phase: run tests, show how to run the app, and list what is left.
- Write unit tests for the business rules (first login final, last logout wins, duplicate login rejected, holiday handling).
- Keep vision code behind interfaces so the camera can be mocked in tests.
- Make small, readable commits with clear messages.
- If a requirement is ambiguous, ask me before assuming.
- Never commit images, embeddings, .env files or the database. Add them to .gitignore.