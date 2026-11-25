import json
from datetime import UTC, date, datetime, time

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AttendanceDaily,
    Holiday,
    LogoutLog,
    ScanAttempt,
    Setting,
)
from app.services.attendance import (
    AttendanceConfigurationError,
    AttendanceRuleError,
    attendance_working_seconds,
    get_weekly_off_days,
    record_logout,
    set_late_cutoff,
    set_weekly_off_days,
)
from app.services.attendance import (
    record_login as record_login_service,
)
from app.services.login_images import StoredLoginImage


class FakeImageStore:
    def __init__(self) -> None:
        self.saved = 0

    def save_login_image(self, **_kwargs) -> StoredLoginImage:
        self.saved += 1
        return StoredLoginImage("storage/images/test/2026-11-24.jpg", "a" * 64)

    def delete_login_image(self, _path: str) -> None:
        pass


def record_login(db: Session, **kwargs) -> AttendanceDaily:
    return record_login_service(
        db,
        **kwargs,
        image_frame=np.zeros((4, 4, 3), dtype=np.uint8),
        image_store=FakeImageStore(),
    )


def scan_login(db: Session, employee_id: int) -> AttendanceDaily:
    return record_login(
        db,
        employee_id=employee_id,
        kiosk_id="test-kiosk",
        gesture="thumb",
        liveness_passed=True,
    )


def scan_logout(db: Session, employee_id: int) -> AttendanceDaily:
    return record_logout(
        db,
        employee_id=employee_id,
        kiosk_id="test-kiosk",
        gesture="open_palm",
        liveness_passed=True,
    )


def test_duplicate_login_while_checked_in_is_rejected(
    db_session: Session, employee, kiosk, server_clock
) -> None:
    first_login_at = server_clock[0]
    record = scan_login(db_session, employee.id)
    assert record.first_login_at == first_login_at
    assert record.last_login_at == first_login_at
    assert record.status == "Incomplete"

    server_clock[0] = datetime(2026, 11, 24, 10, 4, tzinfo=UTC)
    with pytest.raises(AttendanceRuleError) as error:
        scan_login(db_session, employee.id)

    assert error.value.status_code == 409
    assert error.value.message == "Already logged in at 08:15"
    records = db_session.scalars(select(AttendanceDaily)).all()
    assert len(records) == 1
    assert records[0].first_login_at == first_login_at
    rejected = db_session.scalars(select(ScanAttempt)).all()
    assert len(rejected) == 1
    assert "Already logged in" in rejected[0].reason


def test_login_reuses_existing_absence_record_without_violating_unique_constraint(
    db_session: Session, employee, kiosk, server_clock
) -> None:
    record = AttendanceDaily(
        employee_id=employee.id,
        date=server_clock[0].date(),
        status="Absent",
    )
    db_session.add(record)
    db_session.commit()

    updated = scan_login(db_session, employee.id)

    assert updated.id == record.id
    assert updated.first_login_at == server_clock[0]
    assert updated.status == "Incomplete"
    assert len(db_session.scalars(select(AttendanceDaily)).all()) == 1


@pytest.mark.parametrize(
    ("gesture", "liveness_passed", "expected_message"),
    [
        ("open_palm", True, "Invalid gesture. Use thumb."),
        ("thumb", False, "Liveness check failed."),
    ],
)
def test_login_requires_thumb_and_liveness_and_logs_rejection(
    db_session: Session,
    employee,
    kiosk,
    server_clock,
    gesture: str,
    liveness_passed: bool,
    expected_message: str,
) -> None:
    with pytest.raises(AttendanceRuleError, match=expected_message):
        record_login(
            db_session,
            employee_id=employee.id,
            kiosk_id="test-kiosk",
            gesture=gesture,
            liveness_passed=liveness_passed,
        )

    assert db_session.scalars(select(AttendanceDaily)).all() == []
    assert db_session.scalar(select(ScanAttempt)).reason == expected_message


def test_login_rejects_missing_employee_or_inactive_kiosk(
    db_session: Session, employee, kiosk, server_clock
) -> None:
    with pytest.raises(AttendanceRuleError, match="Employee not found or inactive"):
        record_login(
            db_session,
            employee_id=999,
            kiosk_id="test-kiosk",
            gesture="thumb",
            liveness_passed=True,
        )
    assert db_session.scalar(select(ScanAttempt)).employee_id is None

    kiosk.is_active = False
    db_session.commit()
    with pytest.raises(AttendanceRuleError, match="Kiosk not found or inactive"):
        scan_login(db_session, employee.id)
    assert len(db_session.scalars(select(ScanAttempt)).all()) == 2


@pytest.mark.parametrize(
    ("attendance_date", "expected_status"),
    [
        (date(2026, 11, 28), "Present on Holiday"),
        (date(2026, 11, 29), "Present on Holiday"),
        (date(2026, 11, 24), "Incomplete"),
    ],
)
def test_weekends_are_weekly_off_by_default(
    db_session: Session,
    employee,
    kiosk,
    server_clock,
    attendance_date: date,
    expected_status: str,
) -> None:
    server_clock[0] = datetime(
        attendance_date.year,
        attendance_date.month,
        attendance_date.day,
        8,
        0,
        tzinfo=UTC,
    )
    record = scan_login(db_session, employee.id)
    assert record.status == expected_status


def test_holiday_login_is_present_on_holiday(db_session, employee, kiosk, server_clock):
    db_session.add(Holiday(date=date(2026, 11, 24), name="Office Holiday"))
    db_session.commit()

    record = scan_login(db_session, employee.id)
    assert record.status == "Present on Holiday"
    server_clock[0] = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    record = scan_logout(db_session, employee.id)
    assert record.status == "Present on Holiday"


def test_weekly_off_days_can_be_configured(db_session, employee, kiosk, server_clock):
    set_weekly_off_days(db_session, [0])
    server_clock[0] = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
    monday_record = scan_login(db_session, employee.id)
    assert monday_record.status == "Present on Holiday"


def test_weekly_off_setting_rejects_invalid_values(db_session: Session) -> None:
    with pytest.raises(AttendanceRuleError, match="from 0 to 6"):
        set_weekly_off_days(db_session, [7])
    with pytest.raises(AttendanceRuleError, match="duplicates"):
        set_weekly_off_days(db_session, [5, 5])
    db_session.add(Setting(key="weekly_off_days", value=json.dumps(["Saturday"])))
    db_session.commit()
    with pytest.raises(AttendanceConfigurationError, match="weekday numbers"):
        get_weekly_off_days(db_session)


def test_logout_without_login_is_blocked_and_recorded_as_exception(
    db_session: Session, employee, kiosk, server_clock
) -> None:
    with pytest.raises(AttendanceRuleError, match="without a login"):
        scan_logout(db_session, employee.id)

    logout = db_session.scalar(select(LogoutLog))
    assert logout is not None
    assert logout.accepted is False
    assert logout.attendance_id is None
    assert logout.exception_reason == "Cannot log out without a login for today."
    assert db_session.scalar(select(ScanAttempt)).reason == logout.exception_reason


def test_logout_earlier_than_login_is_rejected_and_logged(
    db_session: Session, employee, kiosk, server_clock
) -> None:
    scan_login(db_session, employee.id)
    server_clock[0] = datetime(2026, 11, 24, 8, 14, tzinfo=UTC)

    with pytest.raises(AttendanceRuleError, match="earlier than login"):
        scan_logout(db_session, employee.id)

    logout = db_session.scalar(select(LogoutLog))
    assert logout is not None and not logout.accepted
    assert logout.exception_reason == "Logout time cannot be earlier than login time."
    assert db_session.scalar(select(AttendanceDaily)).final_logout_at is None


@pytest.mark.parametrize(
    ("gesture", "liveness_passed", "expected_message"),
    [
        ("thumb", True, "Invalid gesture. Use open_palm."),
        ("open_palm", False, "Liveness check failed."),
    ],
)
def test_logout_requires_open_palm_and_liveness_and_logs_every_attempt(
    db_session: Session,
    employee,
    kiosk,
    server_clock,
    gesture: str,
    liveness_passed: bool,
    expected_message: str,
) -> None:
    scan_login(db_session, employee.id)
    with pytest.raises(AttendanceRuleError, match=expected_message):
        record_logout(
            db_session,
            employee_id=employee.id,
            kiosk_id="test-kiosk",
            gesture=gesture,
            liveness_passed=liveness_passed,
        )

    logout = db_session.scalar(select(LogoutLog))
    assert logout is not None and not logout.accepted
    assert logout.exception_reason == expected_message


def test_attendance_alternates_and_preserves_first_login_and_final_logout(
    db_session: Session, employee, kiosk, server_clock
) -> None:
    record = scan_login(db_session, employee.id)
    login_at = server_clock[0]
    server_clock[0] = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    record = scan_logout(db_session, employee.id)
    first_logout = server_clock[0]
    server_clock[0] = datetime(2026, 11, 24, 12, 30, tzinfo=UTC)
    record = scan_login(db_session, employee.id)

    assert record.first_login_at == login_at
    assert record.last_login_at == server_clock[0]
    assert record.final_logout_at is None
    assert record.status == "Incomplete"

    server_clock[0] = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    record = scan_logout(db_session, employee.id)
    final_logout = server_clock[0]

    assert record.first_login_at == login_at
    assert record.last_login_at == datetime(2026, 11, 24, 12, 30, tzinfo=UTC)
    assert record.final_logout_at == final_logout
    assert record.status == "Present"
    assert attendance_working_seconds(record) == int(
        (final_logout - login_at).total_seconds()
    )

    logs = db_session.scalars(select(LogoutLog).order_by(LogoutLog.logout_at)).all()
    assert len(logs) == 2
    assert all(log.accepted for log in logs)
    assert [log.logout_at for log in logs] == [first_logout, final_logout]


def test_duplicate_logout_is_rejected_until_next_checkin(
    db_session: Session, employee, kiosk, server_clock
) -> None:
    scan_login(db_session, employee.id)
    server_clock[0] = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    scan_logout(db_session, employee.id)
    server_clock[0] = datetime(2026, 11, 24, 17, 1, tzinfo=UTC)

    with pytest.raises(AttendanceRuleError, match="Already checked out at 17:00"):
        scan_logout(db_session, employee.id)

    logout = db_session.scalar(select(LogoutLog).order_by(LogoutLog.id.desc()))
    assert logout is not None and not logout.accepted
    assert logout.exception_reason == "Already checked out at 17:00"


def test_checkin_must_be_after_previous_checkout(
    db_session: Session, employee, kiosk, server_clock
) -> None:
    first_login_at = server_clock[0]
    scan_login(db_session, employee.id)
    server_clock[0] = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    scan_logout(db_session, employee.id)
    server_clock[0] = datetime(2026, 11, 24, 16, 59, tzinfo=UTC)

    with pytest.raises(AttendanceRuleError, match="Check-in must be after"):
        scan_login(db_session, employee.id)

    record = db_session.scalar(select(AttendanceDaily))
    assert record is not None
    assert record.first_login_at == first_login_at
    assert record.final_logout_at == datetime(2026, 11, 24, 17, 0, tzinfo=UTC)


def test_recheckin_does_not_save_a_second_image(
    db_session: Session, employee, kiosk, server_clock
) -> None:
    image_store = FakeImageStore()
    image_frame = np.zeros((4, 4, 3), dtype=np.uint8)
    login_kwargs = {
        "employee_id": employee.id,
        "kiosk_id": "test-kiosk",
        "gesture": "thumb",
        "liveness_passed": True,
        "image_frame": image_frame,
        "image_store": image_store,
    }

    first = record_login_service(db_session, **login_kwargs)
    server_clock[0] = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    record_logout(
        db_session,
        employee_id=employee.id,
        kiosk_id="test-kiosk",
        gesture="open_palm",
        liveness_passed=True,
    )
    server_clock[0] = datetime(2026, 11, 24, 12, 30, tzinfo=UTC)
    second = record_login_service(db_session, **login_kwargs)

    assert image_store.saved == 1
    assert second.image_path == first.image_path
    assert second.image_sha256 == first.image_sha256


def test_logout_equal_to_login_is_allowed(db_session, employee, kiosk, server_clock):
    record = scan_login(db_session, employee.id)
    record = scan_logout(db_session, employee.id)
    assert record.final_logout_at == record.first_login_at
    assert record.status == "Present"
    assert attendance_working_seconds(record) == 0


def test_late_cutoff_is_configurable_and_unset_means_no_late_status(
    db_session, employee, kiosk, server_clock
) -> None:
    server_clock[0] = datetime(2026, 11, 24, 9, 15, tzinfo=UTC)
    scan_login(db_session, employee.id)
    server_clock[0] = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    record = scan_logout(db_session, employee.id)
    assert record.status == "Present"

    db_session.query(LogoutLog).delete()
    db_session.query(AttendanceDaily).delete()
    set_late_cutoff(db_session, time(9, 0))
    server_clock[0] = datetime(2026, 11, 24, 9, 15, tzinfo=UTC)
    scan_login(db_session, employee.id)
    server_clock[0] = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    record = scan_logout(db_session, employee.id)
    assert record.status == "Late"


def test_login_at_late_cutoff_is_not_late(db_session, employee, kiosk, server_clock):
    set_late_cutoff(db_session, time(9, 0))
    server_clock[0] = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
    scan_login(db_session, employee.id)
    server_clock[0] = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    assert scan_logout(db_session, employee.id).status == "Present"


def test_working_hours_is_final_logout_minus_first_login(
    db_session, employee, kiosk, server_clock
) -> None:
    scan_login(db_session, employee.id)
    server_clock[0] = datetime(2026, 11, 24, 16, 15, tzinfo=UTC)
    record = scan_logout(db_session, employee.id)
    assert attendance_working_seconds(record) == 7 * 60 * 60 + 59 * 60 + 30


def test_timestamps_are_taken_from_server_clock(
    db_session, employee, kiosk, server_clock
) -> None:
    server_clock[0] = datetime(2026, 11, 24, 7, 42, 18, tzinfo=UTC)
    record = scan_login(db_session, employee.id)
    assert record.first_login_at == server_clock[0]

    server_clock[0] = datetime(2026, 11, 24, 16, 20, tzinfo=UTC)
    record = scan_logout(db_session, employee.id)
    assert record.final_logout_at == server_clock[0]
