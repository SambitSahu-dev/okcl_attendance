import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import AttendanceDaily, Employee
from app.services.attendance import day_off_status

logger = logging.getLogger(__name__)


def mark_absent_for_day(db: Session, attendance_date: date) -> int:
    off_status = day_off_status(db, attendance_date)
    employees = db.scalars(
        select(Employee).where(
            Employee.is_active.is_(True),
            Employee.created_at
            < datetime.combine(attendance_date, datetime.max.time(), tzinfo=UTC),
        )
    ).all()
    attendance_records = db.scalars(
        select(AttendanceDaily).where(AttendanceDaily.date == attendance_date)
    ).all()
    by_employee = {record.employee_id: record for record in attendance_records}
    changed = 0

    for employee in employees:
        record = by_employee.get(employee.id)
        if record is not None and record.manually_edited:
            continue
        if off_status is not None:
            if record is not None and record.first_login_at is None:
                record.status = off_status
                changed += 1
            continue
        if record is None:
            record = AttendanceDaily(
                employee_id=employee.id,
                date=attendance_date,
                status="Absent",
            )
            db.add(record)
            by_employee[employee.id] = record
            changed += 1
        elif record.first_login_at is None and record.status != "Absent":
            record.status = "Absent"
            changed += 1

    db.commit()
    return changed


def flag_incomplete_for_day(db: Session, attendance_date: date) -> int:
    if day_off_status(db, attendance_date) is not None:
        return 0

    records = db.scalars(
        select(AttendanceDaily).where(
            AttendanceDaily.date == attendance_date,
            AttendanceDaily.first_login_at.is_not(None),
            AttendanceDaily.final_logout_at.is_(None),
            AttendanceDaily.manually_edited.is_(False),
        )
    ).all()
    changed = 0
    for record in records:
        if record.status != "Incomplete":
            record.status = "Incomplete"
            changed += 1
    db.commit()
    return changed


def run_absent_marking() -> None:
    attendance_date = datetime.now(UTC).date()
    with SessionLocal() as db:
        changed = mark_absent_for_day(db, attendance_date)
    logger.info(
        "End-of-day absent marking completed: date=%s records_updated=%d",
        attendance_date,
        changed,
    )


def run_incomplete_flagging() -> None:
    attendance_date = datetime.now(UTC).date()
    with SessionLocal() as db:
        changed = flag_incomplete_for_day(db, attendance_date)
    logger.info(
        "End-of-day incomplete flagging completed: date=%s records_updated=%d",
        attendance_date,
        changed,
    )
