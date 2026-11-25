import json
from datetime import UTC, date, datetime, time
from typing import Literal, NoReturn

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import (
    AttendanceDaily,
    Employee,
    Holiday,
    Kiosk,
    LogoutLog,
    ScanAttempt,
    Setting,
)
from app.services.login_images import LoginImageStore
from vision.types import Frame

ScanType = Literal["login", "logout"]
DEFAULT_WEEKLY_OFF_DAYS = (5, 6)


class AttendanceRuleError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AttendanceConfigurationError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _server_time() -> datetime:
    return _as_utc(utc_now())


def _read_setting(db: Session, key: str) -> str | None:
    setting = db.get(Setting, key)
    return setting.value if setting is not None else None


def get_weekly_off_days(db: Session) -> tuple[int, ...]:
    configured = _read_setting(db, "weekly_off_days")
    if configured is None:
        return DEFAULT_WEEKLY_OFF_DAYS

    try:
        value = json.loads(configured)
    except json.JSONDecodeError as error:
        raise AttendanceConfigurationError(
            "The weekly_off_days setting must be a JSON array of weekdays."
        ) from error

    if (
        not isinstance(value, list)
        or any(type(day) is not int or day < 0 or day > 6 for day in value)
        or len(set(value)) != len(value)
    ):
        raise AttendanceConfigurationError(
            "The weekly_off_days setting must contain unique weekday numbers from 0 to 6."
        )
    return tuple(value)


def get_late_cutoff(db: Session) -> time | None:
    configured = _read_setting(db, "late_cutoff_time")
    if configured is None:
        return None

    try:
        cutoff = time.fromisoformat(configured)
    except ValueError as error:
        raise AttendanceConfigurationError(
            "The late_cutoff_time setting must use HH:MM format."
        ) from error

    if cutoff.tzinfo is not None:
        raise AttendanceConfigurationError(
            "The late_cutoff_time setting must not include a timezone."
        )
    return cutoff


def day_off_status(db: Session, attendance_date: date) -> str | None:
    is_holiday = db.scalar(
        select(Holiday.id).where(Holiday.date == attendance_date).limit(1)
    )
    if is_holiday is not None:
        return "Holiday"
    if attendance_date.weekday() in get_weekly_off_days(db):
        return "Weekly Off"
    return None


def _employee_for_scan(db: Session, employee_id: int) -> Employee | None:
    return db.get(Employee, employee_id)


def _kiosk_for_scan(db: Session, kiosk_id: str) -> Kiosk | None:
    kiosk = db.get(Kiosk, kiosk_id)
    return kiosk if kiosk is not None and kiosk.is_active else None


def _log_rejected_scan(
    db: Session,
    *,
    employee_id: int,
    kiosk_id: str,
    scan_type: ScanType,
    attempted_at: datetime,
    reason: str,
    employee: Employee | None,
) -> None:
    active_kiosk = _kiosk_for_scan(db, kiosk_id)
    db.add(
        ScanAttempt(
            employee_id=employee.id if employee is not None else None,
            kiosk_id=active_kiosk.kiosk_id if active_kiosk is not None else None,
            scan_type=scan_type,
            attempted_at=attempted_at,
            reason=reason,
        )
    )
    if scan_type == "logout" and employee is not None:
        attendance = db.scalar(
            select(AttendanceDaily).where(
                AttendanceDaily.employee_id == employee.id,
                AttendanceDaily.date == attempted_at.date(),
            )
        )
        db.add(
            LogoutLog(
                employee_id=employee.id,
                attendance_id=attendance.id if attendance is not None else None,
                date=attempted_at.date(),
                logout_at=attempted_at,
                accepted=False,
                exception_reason=reason,
            )
        )
    db.commit()


def _reject(
    db: Session,
    *,
    employee_id: int,
    kiosk_id: str,
    scan_type: ScanType,
    attempted_at: datetime,
    message: str,
    employee: Employee | None,
    status_code: int = 400,
) -> NoReturn:
    _log_rejected_scan(
        db,
        employee_id=employee_id,
        kiosk_id=kiosk_id,
        scan_type=scan_type,
        attempted_at=attempted_at,
        reason=message,
        employee=employee,
    )
    raise AttendanceRuleError(message, status_code)


def _validate_scan(
    db: Session,
    *,
    employee_id: int,
    kiosk_id: str,
    gesture: str,
    expected_gesture: str,
    liveness_passed: bool,
    scan_type: ScanType,
    attempted_at: datetime,
) -> Employee:
    employee = _employee_for_scan(db, employee_id)
    if employee is None or not employee.is_active:
        _reject(
            db,
            employee_id=employee_id,
            kiosk_id=kiosk_id,
            scan_type=scan_type,
            attempted_at=attempted_at,
            message="Employee not found or inactive.",
            employee=employee,
            status_code=404,
        )

    if _kiosk_for_scan(db, kiosk_id) is None:
        _reject(
            db,
            employee_id=employee_id,
            kiosk_id=kiosk_id,
            scan_type=scan_type,
            attempted_at=attempted_at,
            message="Kiosk not found or inactive.",
            employee=employee,
            status_code=404,
        )

    if gesture != expected_gesture:
        _reject(
            db,
            employee_id=employee_id,
            kiosk_id=kiosk_id,
            scan_type=scan_type,
            attempted_at=attempted_at,
            message=f"Invalid gesture. Use {expected_gesture}.",
            employee=employee,
        )

    if not liveness_passed:
        _reject(
            db,
            employee_id=employee_id,
            kiosk_id=kiosk_id,
            scan_type=scan_type,
            attempted_at=attempted_at,
            message="Liveness check failed.",
            employee=employee,
        )

    return employee


def record_login(
    db: Session,
    *,
    employee_id: int,
    kiosk_id: str,
    gesture: str,
    liveness_passed: bool,
    image_frame: Frame,
    image_store: LoginImageStore,
) -> AttendanceDaily:
    attempted_at = _server_time()
    employee = _validate_scan(
        db,
        employee_id=employee_id,
        kiosk_id=kiosk_id,
        gesture=gesture,
        expected_gesture="thumb",
        liveness_passed=liveness_passed,
        scan_type="login",
        attempted_at=attempted_at,
    )

    attendance_date = attempted_at.date()
    existing = db.scalar(
        select(AttendanceDaily).where(
            AttendanceDaily.employee_id == employee.id,
            AttendanceDaily.date == attendance_date,
        )
    )
    is_first_login = existing is None or existing.first_login_at is None
    if not is_first_login:
        if existing.final_logout_at is None:
            latest_login = _as_utc(existing.last_login_at or existing.first_login_at)
            _reject(
                db,
                employee_id=employee_id,
                kiosk_id=kiosk_id,
                scan_type="login",
                attempted_at=attempted_at,
                message=f"Already logged in at {latest_login:%H:%M}",
                employee=employee,
                status_code=409,
            )
        previous_logout = _as_utc(existing.final_logout_at)
        if attempted_at <= previous_logout:
            _reject(
                db,
                employee_id=employee_id,
                kiosk_id=kiosk_id,
                scan_type="login",
                attempted_at=attempted_at,
                message="Check-in must be after the previous logout.",
                employee=employee,
                status_code=409,
            )

    status = (
        "Present on Holiday"
        if day_off_status(db, attendance_date) is not None
        else "Incomplete"
    )
    kiosk = db.get(Kiosk, kiosk_id)
    if kiosk is None or not kiosk.is_active:
        raise AttendanceRuleError("Kiosk not found or inactive.", 404)
    stored_image = (
        image_store.save_login_image(
            employee_id=employee.id,
            kiosk=kiosk,
            captured_at=attempted_at,
            frame=image_frame,
        )
        if is_first_login
        else None
    )
    if existing is None:
        attendance = AttendanceDaily(
            employee_id=employee.id,
            date=attendance_date,
            first_login_at=attempted_at,
            last_login_at=attempted_at,
            status=status,
            image_path=stored_image.path if stored_image is not None else None,
            image_sha256=stored_image.sha256 if stored_image is not None else None,
        )
        db.add(attendance)
    elif existing.first_login_at is None:
        attendance = existing
        attendance.first_login_at = attempted_at
        attendance.last_login_at = attempted_at
        attendance.final_logout_at = None
        attendance.status = status
        if stored_image is not None:
            attendance.image_path = stored_image.path
            attendance.image_sha256 = stored_image.sha256
    else:
        attendance = existing
        attendance.last_login_at = attempted_at
        attendance.final_logout_at = None
        attendance.status = status
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if stored_image is not None:
            image_store.delete_login_image(stored_image.path)
        existing = db.scalar(
            select(AttendanceDaily).where(
                AttendanceDaily.employee_id == employee.id,
                AttendanceDaily.date == attendance_date,
            )
        )
        if existing is None or existing.first_login_at is None:
            raise
        latest_login = _as_utc(existing.last_login_at or existing.first_login_at)
        _reject(
            db,
            employee_id=employee_id,
            kiosk_id=kiosk_id,
            scan_type="login",
            attempted_at=attempted_at,
            message=f"Already logged in at {latest_login:%H:%M}",
            employee=employee,
            status_code=409,
        )
    except SQLAlchemyError:
        db.rollback()
        if stored_image is not None:
            image_store.delete_login_image(stored_image.path)
        raise
    db.refresh(attendance)
    return attendance


def record_logout(
    db: Session,
    *,
    employee_id: int,
    kiosk_id: str,
    gesture: str,
    liveness_passed: bool,
) -> AttendanceDaily:
    attempted_at = _server_time()
    employee = _validate_scan(
        db,
        employee_id=employee_id,
        kiosk_id=kiosk_id,
        gesture=gesture,
        expected_gesture="open_palm",
        liveness_passed=liveness_passed,
        scan_type="logout",
        attempted_at=attempted_at,
    )

    attendance = db.scalar(
        select(AttendanceDaily).where(
            AttendanceDaily.employee_id == employee.id,
            AttendanceDaily.date == attempted_at.date(),
        )
    )
    if attendance is None or attendance.first_login_at is None:
        _reject(
            db,
            employee_id=employee_id,
            kiosk_id=kiosk_id,
            scan_type="logout",
            attempted_at=attempted_at,
            message="Cannot log out without a login for today.",
            employee=employee,
        )

    latest_login_at = _as_utc(attendance.last_login_at or attendance.first_login_at)
    if attendance.final_logout_at is not None:
        previous_logout = _as_utc(attendance.final_logout_at)
        _reject(
            db,
            employee_id=employee_id,
            kiosk_id=kiosk_id,
            scan_type="logout",
            attempted_at=attempted_at,
            message=f"Already checked out at {previous_logout:%H:%M}",
            employee=employee,
            status_code=409,
        )

    if attempted_at < latest_login_at:
        _reject(
            db,
            employee_id=employee_id,
            kiosk_id=kiosk_id,
            scan_type="logout",
            attempted_at=attempted_at,
            message="Logout time cannot be earlier than login time.",
            employee=employee,
        )

    logout = LogoutLog(
        employee_id=employee.id,
        attendance_id=attendance.id,
        date=attempted_at.date(),
        logout_at=attempted_at,
        accepted=True,
    )
    db.add(logout)

    attendance.final_logout_at = attempted_at
    if attendance.status != "Present on Holiday":
        cutoff = get_late_cutoff(db)
        first_login_at = _as_utc(attendance.first_login_at)
        attendance.status = (
            "Late"
            if cutoff is not None and first_login_at.time() > cutoff
            else "Present"
        )
    db.commit()
    db.refresh(attendance)
    return attendance


def attendance_working_seconds(record: AttendanceDaily) -> int | None:
    if record.first_login_at is None or record.final_logout_at is None:
        return None
    duration = _as_utc(record.final_logout_at) - _as_utc(record.first_login_at)
    return max(0, int(duration.total_seconds()))


def set_late_cutoff(db: Session, cutoff: time | None) -> None:
    setting = db.get(Setting, "late_cutoff_time")
    if cutoff is None:
        if setting is not None:
            db.delete(setting)
    else:
        if cutoff.tzinfo is not None:
            raise AttendanceRuleError("Late cutoff must not include a timezone.")
        value = cutoff.isoformat(timespec="minutes")
        if setting is None:
            db.add(Setting(key="late_cutoff_time", value=value))
        else:
            setting.value = value
    db.commit()


def set_weekly_off_days(db: Session, weekdays: list[int]) -> None:
    if any(type(day) is not int or day < 0 or day > 6 for day in weekdays):
        raise AttendanceRuleError("Weekday values must be integers from 0 to 6.")
    if len(set(weekdays)) != len(weekdays):
        raise AttendanceRuleError("Weekly off weekdays must not contain duplicates.")

    value = json.dumps(sorted(weekdays))
    setting = db.get(Setting, "weekly_off_days")
    if setting is None:
        db.add(Setting(key="weekly_off_days", value=value))
    else:
        setting.value = value
    db.commit()


def add_holiday(db: Session, *, holiday_date: date, name: str) -> Holiday:
    existing = db.scalar(select(Holiday).where(Holiday.date == holiday_date))
    if existing is not None:
        raise AttendanceRuleError("A holiday is already configured for this date.", 409)
    holiday = Holiday(date=holiday_date, name=name.strip())
    if not holiday.name:
        raise AttendanceRuleError("Holiday name is required.")
    db.add(holiday)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise AttendanceRuleError(
            "A holiday is already configured for this date.", 409
        ) from error
    db.refresh(holiday)
    return holiday


def remove_holiday(db: Session, *, holiday_date: date) -> None:
    holiday = db.scalar(select(Holiday).where(Holiday.date == holiday_date))
    if holiday is None:
        raise AttendanceRuleError("Holiday not found.", 404)
    db.delete(holiday)
    db.commit()


def get_attendance_settings(db: Session) -> dict[str, object]:
    cutoff = get_late_cutoff(db)
    return {
        "late_cutoff_time": cutoff.isoformat(timespec="minutes") if cutoff else None,
        "weekly_off_days": list(get_weekly_off_days(db)),
    }
