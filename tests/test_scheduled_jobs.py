import os
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from app.models import AttendanceDaily, Employee, Holiday
from jobs.attendance_jobs import flag_incomplete_for_day, mark_absent_for_day
from jobs.backup import create_backup, purge_employee_from_backups
from jobs.monthly_report import purge_employee_from_reports
from jobs.retention import purge_expired_backups, purge_expired_images
from jobs.scheduler import build_scheduler


def test_end_of_day_marks_absent_and_incomplete_without_overwriting_manual_edits(
    db_session, employee
) -> None:
    target_day = date(2026, 11, 24)
    employee.created_at = datetime(2026, 11, 1, tzinfo=UTC)
    absent_employee = Employee(
        employee_code="EMP-002",
        full_name="Absent Employee",
        created_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    manual_employee = Employee(
        employee_code="EMP-003",
        full_name="Manually Edited Employee",
        created_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    inactive_employee = Employee(
        employee_code="EMP-004",
        full_name="Inactive Employee",
        is_active=False,
        created_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    db_session.add_all([absent_employee, manual_employee, inactive_employee])
    db_session.flush()
    incomplete = AttendanceDaily(
        employee_id=employee.id,
        date=target_day,
        first_login_at=datetime(2026, 11, 24, 8, 0, tzinfo=UTC),
        status="Late",
    )
    manual = AttendanceDaily(
        employee_id=manual_employee.id,
        date=target_day,
        status="Present",
        manually_edited=True,
    )
    db_session.add_all(
        [absent_employee, manual_employee, inactive_employee, incomplete, manual]
    )
    db_session.commit()

    marked_absent = mark_absent_for_day(db_session, target_day)
    flagged_incomplete = flag_incomplete_for_day(db_session, target_day)

    absent = db_session.scalar(
        select(AttendanceDaily).where(
            AttendanceDaily.employee_id == absent_employee.id,
            AttendanceDaily.date == target_day,
        )
    )
    assert marked_absent == 1
    assert absent is not None and absent.status == "Absent"
    assert flagged_incomplete == 1
    assert incomplete.status == "Incomplete"
    assert manual.status == "Present"
    assert (
        db_session.scalar(
            select(AttendanceDaily).where(
                AttendanceDaily.employee_id == inactive_employee.id
            )
        )
        is None
    )


def test_end_of_day_skips_holidays_and_weekly_offs(db_session, employee):
    employee.created_at = datetime(2026, 11, 1, tzinfo=UTC)
    holiday_day = date(2026, 11, 24)
    weekly_off = date(2026, 11, 28)
    db_session.add_all(
        [
            Holiday(date=holiday_day, name="Office Holiday"),
            AttendanceDaily(
                employee_id=employee.id,
                date=holiday_day,
                status="Absent",
            ),
        ]
    )
    db_session.commit()

    assert mark_absent_for_day(db_session, holiday_day) == 1
    assert flag_incomplete_for_day(db_session, holiday_day) == 0
    assert mark_absent_for_day(db_session, weekly_off) == 0
    assert flag_incomplete_for_day(db_session, weekly_off) == 0
    assert (
        db_session.scalar(
            select(AttendanceDaily).where(AttendanceDaily.date == holiday_day)
        ).status
        == "Holiday"
    )
    assert (
        db_session.scalar(
            select(AttendanceDaily).where(AttendanceDaily.date == weekly_off)
        )
        is None
    )


def test_nightly_backup_contains_consistent_sqlite_snapshot_and_images(tmp_path):
    database_path = tmp_path / "attendance.db"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample (value) VALUES ('backed up')")
        connection.commit()
    image_directory = tmp_path / "images"
    image_directory.mkdir()
    image = image_directory / "1" / "2026-11-24.jpg"
    image.parent.mkdir()
    image.write_bytes(b"test-image")

    backup_path = create_backup(
        database_path=database_path,
        image_directory=image_directory,
        output_directory=tmp_path / "backups",
        created_at=datetime(2026, 11, 25, 1, 0, tzinfo=UTC),
    )

    with ZipFile(backup_path) as archive:
        assert archive.read("images/1/2026-11-24.jpg") == b"test-image"
        snapshot_path = tmp_path / "snapshot.db"
        snapshot_path.write_bytes(archive.read("attendance.db"))
        with closing(sqlite3.connect(snapshot_path)) as connection:
            assert connection.execute("SELECT value FROM sample").fetchone() == (
                "backed up",
            )


def test_permanent_employee_purge_rewrites_backups_and_removes_images(tmp_path):
    database_path = tmp_path / "source.db"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE employees (id INTEGER PRIMARY KEY);
            CREATE TABLE users (id INTEGER PRIMARY KEY, employee_id INTEGER);
            CREATE TABLE attendance_daily (id INTEGER PRIMARY KEY, employee_id INTEGER);
            CREATE TABLE attendance_audit (
                id INTEGER PRIMARY KEY, attendance_id INTEGER, actor_user_id INTEGER
            );
            CREATE TABLE logout_log (
                id INTEGER PRIMARY KEY, employee_id INTEGER, attendance_id INTEGER
            );
            CREATE TABLE scan_attempts (id INTEGER PRIMARY KEY, employee_id INTEGER);
            CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY, employee_id INTEGER);
            CREATE TABLE consents (id INTEGER PRIMARY KEY, employee_id INTEGER);
            CREATE TABLE month_locks (
                id INTEGER PRIMARY KEY,
                locked_by_user_id INTEGER,
                updated_by_user_id INTEGER
            );
            INSERT INTO employees VALUES (1), (2);
            INSERT INTO users VALUES (11, 1), (22, 2);
            INSERT INTO attendance_daily VALUES (101, 1), (202, 2);
            INSERT INTO attendance_audit VALUES (1, 101, 99), (2, 202, 99);
            INSERT INTO logout_log VALUES (1, 1, 101), (2, 2, 202);
            INSERT INTO scan_attempts VALUES (1, 1), (2, 2);
            INSERT INTO face_embeddings VALUES (1, 1), (2, 2);
            INSERT INTO consents VALUES (1, 1), (2, 2);
            INSERT INTO month_locks VALUES (1, 11, 11);
            """
        )
        connection.commit()
    image_directory = tmp_path / "images"
    for employee_id in (1, 2):
        image_path = image_directory / str(employee_id) / "2026-11-24.jpg"
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"test-image")
    backup_directory = tmp_path / "backups"
    backup = create_backup(
        database_path=database_path,
        image_directory=image_directory,
        output_directory=backup_directory,
        created_at=datetime(2026, 11, 25, 1, 0, tzinfo=UTC),
    )

    assert purge_employee_from_backups(backup_directory, 1) == 1

    with ZipFile(backup) as archive:
        assert "images/1/2026-11-24.jpg" not in archive.namelist()
        assert "images/2/2026-11-24.jpg" in archive.namelist()
        snapshot_path = tmp_path / "purged-snapshot.db"
        snapshot_path.write_bytes(archive.read("attendance.db"))
    with closing(sqlite3.connect(snapshot_path)) as connection:
        assert connection.execute("SELECT id FROM employees ORDER BY id").fetchall() == [
            (2,)
        ]
        assert connection.execute(
            "SELECT employee_id FROM attendance_daily"
        ).fetchall() == [(2,)]
        assert connection.execute("SELECT employee_id FROM users").fetchall() == [(2,)]
        assert connection.execute("SELECT employee_id FROM logout_log").fetchall() == [
            (2,)
        ]
        assert connection.execute(
            "SELECT employee_id FROM scan_attempts"
        ).fetchall() == [(2,)]
        assert connection.execute(
            "SELECT employee_id FROM face_embeddings"
        ).fetchall() == [(2,)]
        assert connection.execute("SELECT employee_id FROM consents").fetchall() == [
            (2,)
        ]
        assert connection.execute(
            "SELECT attendance_id FROM attendance_audit"
        ).fetchall() == [(202,)]
        assert connection.execute(
            "SELECT locked_by_user_id, updated_by_user_id FROM month_locks"
        ).fetchall() == [(None, None)]


def test_permanent_employee_purge_removes_monthly_report_rows(tmp_path):
    report_directory = tmp_path / "reports"
    report_directory.mkdir()
    report_path = report_directory / "attendance-2026-11.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Employee Code", "Employee ID", "Employee Name"])
    sheet.append(["EMP-001", 1, "Deleted Employee"])
    sheet.append(["EMP-002", 2, "Retained Employee"])
    workbook.save(report_path)

    assert purge_employee_from_reports(report_directory, 1) == 1

    updated_workbook = load_workbook(report_path)
    rows = list(updated_workbook.active.values)
    updated_workbook.close()
    assert rows == [
        ("Employee Code", "Employee ID", "Employee Name"),
        ("EMP-002", 2, "Retained Employee"),
    ]


def test_image_retention_removes_old_file_but_keeps_hash_and_summary(
    db_session, employee, tmp_path
) -> None:
    today = date(2026, 11, 25)
    old_date = today - timedelta(days=100)
    old_without_hash_date = today - timedelta(days=120)
    recent_date = today - timedelta(days=10)
    employee.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    old_record = AttendanceDaily(
        employee_id=employee.id,
        date=old_date,
        status="Present",
        image_path=f"storage/images/{employee.id}/{old_date:%Y-%m-%d}.jpg",
        image_sha256="a" * 64,
    )
    recent_record = AttendanceDaily(
        employee_id=employee.id,
        date=recent_date,
        status="Present",
        image_path=f"storage/images/{employee.id}/{recent_date:%Y-%m-%d}.jpg",
        image_sha256="b" * 64,
    )
    old_without_hash = AttendanceDaily(
        employee_id=employee.id,
        date=old_without_hash_date,
        status="Present",
        image_path=(
            f"storage/images/{employee.id}/{old_without_hash_date:%Y-%m-%d}.jpg"
        ),
    )
    db_session.add_all([old_record, recent_record, old_without_hash])
    db_session.commit()
    old_image = tmp_path / str(employee.id) / f"{old_date:%Y-%m-%d}.jpg"
    recent_image = tmp_path / str(employee.id) / f"{recent_date:%Y-%m-%d}.jpg"
    old_image.parent.mkdir()
    old_image.write_bytes(b"old")
    old_without_hash_image = (
        tmp_path / str(employee.id) / f"{old_without_hash_date:%Y-%m-%d}.jpg"
    )
    old_without_hash_image.write_bytes(b"old-without-hash")
    recent_image.write_bytes(b"recent")

    purged = purge_expired_images(
        db_session,
        retention_days=90,
        today=today,
        image_directory=tmp_path,
    )

    assert purged == 1
    assert not old_image.exists()
    assert recent_image.exists()
    assert old_record.image_path is None
    assert old_record.image_sha256 == "a" * 64
    assert old_record.status == "Present"
    assert old_without_hash.image_path is not None
    assert old_without_hash_image.exists()


def test_backup_retention_removes_only_expired_attendance_archives(tmp_path):
    now = datetime(2026, 11, 25, tzinfo=UTC)
    expired = tmp_path / "attendance-backup-old.zip"
    retained = tmp_path / "attendance-backup-new.zip"
    unrelated = tmp_path / "other.zip"
    expired.touch()
    retained.touch()
    unrelated.touch()
    expired_time = (now - timedelta(days=31)).timestamp()
    retained_time = (now - timedelta(days=5)).timestamp()
    os.utime(expired, (expired_time, expired_time))
    os.utime(retained, (retained_time, retained_time))

    deleted = purge_expired_backups(tmp_path, retention_days=30, now=now)

    assert deleted == 1
    assert not expired.exists()
    assert retained.exists()
    assert unrelated.exists()


def test_scheduler_registers_all_attendance_and_maintenance_jobs():
    scheduler = build_scheduler()

    assert {job.id for job in scheduler.get_jobs()} == {
        "mark-absent-attendance",
        "flag-incomplete-attendance",
        "monthly-attendance-report",
        "nightly-attendance-backup",
        "image-and-backup-retention",
    }
