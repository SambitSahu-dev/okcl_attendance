from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import AttendanceDaily, Employee


def test_employee_can_have_only_one_attendance_record_per_date() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        employee = Employee(employee_code="EMP-001", full_name="Test Employee")
        db.add(employee)
        db.flush()
        attendance_date = date(2026, 11, 24)
        db.add(
            AttendanceDaily(
                employee_id=employee.id,
                date=attendance_date,
                status="Incomplete",
            )
        )
        db.commit()

        db.add(
            AttendanceDaily(
                employee_id=employee.id,
                date=attendance_date,
                status="Incomplete",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
