import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import AttendanceAudit, AttendanceDaily, MonthLock, User
from app.services.admin_attendance import (
    AttendanceEditError,
    edit_attendance,
    update_month_lock,
)


def _admin(db_session, *, username: str, admin_role: str) -> User:
    user = User(
        username=username,
        password_hash="test-hash",
        role="admin",
        admin_role=admin_role,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _record(db_session, employee) -> AttendanceDaily:
    record = AttendanceDaily(
        employee_id=employee.id,
        date=date(2026, 11, 24),
        first_login_at=datetime(2026, 11, 24, 8, 0, tzinfo=UTC),
        last_login_at=datetime(2026, 11, 24, 8, 0, tzinfo=UTC),
        final_logout_at=datetime(2026, 11, 24, 16, 0, tzinfo=UTC),
        status="Present",
    )
    db_session.add(record)
    db_session.commit()
    return record


def test_manual_attendance_edit_writes_actor_reason_old_and_new_values(
    db_session, employee
) -> None:
    actor = _admin(db_session, username="hr-user", admin_role="hr")
    record = _record(db_session, employee)

    updated, audit = edit_attendance(
        db_session,
        attendance_id=record.id,
        employee_id=employee.id,
        attendance_date=date(2026, 11, 24),
        first_login_at=datetime(2026, 11, 24, 8, 30, tzinfo=UTC),
        final_logout_at=datetime(2026, 11, 24, 16, 0, tzinfo=UTC),
        status="Late",
        reason="Corrected from approved time sheet",
        actor=actor,
    )

    assert updated.manually_edited is True
    assert audit.actor_user_id == actor.id
    assert audit.reason == "Corrected from approved time sheet"
    assert json.loads(audit.old_values)["status"] == "Present"
    assert json.loads(audit.new_values)["status"] == "Late"
    assert updated.last_login_at == datetime(2026, 11, 24, 8, 30, tzinfo=UTC)
    assert json.loads(audit.new_values)["last_login_at"] == "2026-11-24T08:30:00+00:00"
    assert audit.edited_at.tzinfo is not None
    assert db_session.scalars(select(AttendanceAudit)).all() == [audit]


def test_manual_edit_requires_reason_and_valid_login_logout_order(
    db_session, employee
) -> None:
    actor = _admin(db_session, username="standard-admin", admin_role="standard")
    record = _record(db_session, employee)

    with pytest.raises(AttendanceEditError, match="reason is required"):
        edit_attendance(
            db_session,
            attendance_id=record.id,
            employee_id=employee.id,
            attendance_date=record.date,
            first_login_at=record.first_login_at,
            final_logout_at=record.final_logout_at,
            status="Present",
            reason="  ",
            actor=actor,
        )
    with pytest.raises(AttendanceEditError, match="earlier than first login"):
        edit_attendance(
            db_session,
            attendance_id=record.id,
            employee_id=employee.id,
            attendance_date=record.date,
            first_login_at=datetime(2026, 11, 24, 9, 0, tzinfo=UTC),
            final_logout_at=datetime(2026, 11, 24, 8, 0, tzinfo=UTC),
            status="Present",
            reason="Bad correction",
            actor=actor,
        )
    assert db_session.scalars(select(AttendanceAudit)).all() == []


def test_locked_month_blocks_standard_admin_but_allows_hr_and_super_admin(
    db_session, employee
) -> None:
    standard = _admin(db_session, username="standard", admin_role="standard")
    hr = _admin(db_session, username="hr", admin_role="hr")
    record = _record(db_session, employee)
    update_month_lock(
        db_session,
        year=2026,
        month=11,
        locked=True,
        actor=_admin(db_session, username="super", admin_role="super_admin"),
    )

    with pytest.raises(AttendanceEditError, match="month is locked") as error:
        edit_attendance(
            db_session,
            attendance_id=record.id,
            employee_id=employee.id,
            attendance_date=record.date,
            first_login_at=record.first_login_at,
            final_logout_at=record.final_logout_at,
            status="Late",
            reason="Standard admin attempt",
            actor=standard,
        )
    assert error.value.status_code == 423

    updated, _audit = edit_attendance(
        db_session,
        attendance_id=record.id,
        employee_id=employee.id,
        attendance_date=record.date,
        first_login_at=record.first_login_at,
        final_logout_at=record.final_logout_at,
        status="Late",
        reason="HR correction",
        actor=hr,
    )
    assert updated.status == "Late"
    lock = db_session.scalar(select(MonthLock))
    assert lock is not None and lock.locked is True


def test_month_lock_checks_target_month_to_prevent_move_into_locked_month(
    db_session, employee
) -> None:
    standard = _admin(db_session, username="standard", admin_role="standard")
    record = _record(db_session, employee)
    update_month_lock(
        db_session,
        year=2026,
        month=12,
        locked=True,
        actor=_admin(db_session, username="super", admin_role="super_admin"),
    )

    with pytest.raises(AttendanceEditError, match="month is locked"):
        edit_attendance(
            db_session,
            attendance_id=record.id,
            employee_id=employee.id,
            attendance_date=date(2026, 12, 1),
            first_login_at=datetime(2026, 12, 1, 8, 0, tzinfo=UTC),
            final_logout_at=datetime(2026, 12, 1, 16, 0, tzinfo=UTC),
            status="Present",
            reason="Move into locked period",
            actor=standard,
        )
