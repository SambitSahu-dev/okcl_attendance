import json
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AttendanceAudit, AttendanceDaily, Employee, MonthLock, User

ATTENDANCE_STATUSES = {
    "Present",
    "Absent",
    "Late",
    "Incomplete",
    "Holiday",
    "Weekly Off",
    "Present on Holiday",
}


class AttendanceEditError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def month_lock_for(db: Session, *, year: int, month: int) -> MonthLock | None:
    return db.scalar(
        select(MonthLock).where(MonthLock.year == year, MonthLock.month == month)
    )


def is_month_locked(db: Session, *, year: int, month: int) -> bool:
    lock = month_lock_for(db, year=year, month=month)
    return lock is not None and lock.locked


def update_month_lock(
    db: Session,
    *,
    year: int,
    month: int,
    locked: bool,
    actor: User,
) -> MonthLock:
    if not 1 <= month <= 12:
        raise AttendanceEditError("Month must be between 1 and 12.", 422)

    record = month_lock_for(db, year=year, month=month)
    now = datetime.now(UTC)
    if record is None:
        record = MonthLock(year=year, month=month)
        db.add(record)
    record.locked = locked
    if locked:
        record.locked_at = now
        record.locked_by_user_id = actor.id
    record.updated_by_user_id = actor.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        record = month_lock_for(db, year=year, month=month)
        if record is None:
            raise
        record.locked = locked
        if locked:
            record.locked_at = now
            record.locked_by_user_id = actor.id
        record.updated_by_user_id = actor.id
        db.commit()
    db.refresh(record)
    return record


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _values(record: AttendanceDaily) -> dict[str, object]:
    return {
        "employee_id": record.employee_id,
        "date": record.date.isoformat(),
        "first_login_at": _iso(record.first_login_at),
        "last_login_at": _iso(record.last_login_at),
        "final_logout_at": _iso(record.final_logout_at),
        "status": record.status,
    }


def _normalize_timestamp(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise AttendanceEditError(f"{field} must include a timezone.", 422)
    return value.astimezone(UTC)


def edit_attendance(
    db: Session,
    *,
    attendance_id: int,
    employee_id: int,
    attendance_date: date,
    first_login_at: datetime | None,
    final_logout_at: datetime | None,
    status: str,
    reason: str,
    actor: User,
) -> tuple[AttendanceDaily, AttendanceAudit]:
    record = db.get(AttendanceDaily, attendance_id)
    if record is None:
        raise AttendanceEditError("Attendance record not found.", 404)

    employee = db.get(Employee, employee_id)
    if employee is None:
        raise AttendanceEditError("Employee not found.", 404)
    reason = reason.strip()
    if not reason:
        raise AttendanceEditError("An edit reason is required.", 422)
    if status not in ATTENDANCE_STATUSES:
        raise AttendanceEditError("Invalid attendance status.", 422)

    old_date = record.date
    touched_months = {(old_date.year, old_date.month), (attendance_date.year, attendance_date.month)}
    locked = any(
        is_month_locked(db, year=year, month=month) for year, month in touched_months
    )
    if locked and (actor.admin_role or "standard") not in {"super_admin", "hr"}:
        raise AttendanceEditError(
            "This month is locked. Only Super Admin or HR can edit it.", 423
        )

    first_login_at = _normalize_timestamp(first_login_at, "First login")
    final_logout_at = _normalize_timestamp(final_logout_at, "Final logout")
    if final_logout_at is not None and first_login_at is None:
        raise AttendanceEditError("Final logout requires a first login.", 422)
    if first_login_at is not None and first_login_at.date() != attendance_date:
        raise AttendanceEditError(
            "First login must fall on the attendance date (UTC).", 422
        )
    if final_logout_at is not None and final_logout_at.date() != attendance_date:
        raise AttendanceEditError(
            "Final logout must fall on the attendance date (UTC).", 422
        )
    if (
        first_login_at is not None
        and final_logout_at is not None
        and final_logout_at < first_login_at
    ):
        raise AttendanceEditError(
            "Final logout cannot be earlier than first login.", 422
        )

    old_values = _values(record)
    record.employee_id = employee_id
    record.date = attendance_date
    record.first_login_at = first_login_at
    record.last_login_at = first_login_at
    record.final_logout_at = final_logout_at
    record.status = status
    record.manually_edited = True
    new_values = _values(record)
    audit = AttendanceAudit(
        attendance_id=record.id,
        actor_user_id=actor.id,
        edited_at=datetime.now(UTC),
        old_values=json.dumps(old_values, sort_keys=True),
        new_values=json.dumps(new_values, sort_keys=True),
        reason=reason,
    )
    db.add(audit)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise AttendanceEditError(
            "An attendance record already exists for that employee and date.", 409
        ) from error
    db.refresh(record)
    db.refresh(audit)
    return record, audit
