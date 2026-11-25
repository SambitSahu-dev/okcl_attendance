from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AttendanceDaily, Employee, Holiday
from app.services.attendance import get_weekly_off_days


def dashboard_data(
    db: Session,
    *,
    today: date | None = None,
    admin_contact_email: str | None = None,
) -> dict[str, object]:
    today = today or datetime.now(UTC).date()
    active_employees = db.scalars(
        select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.employee_code)
    ).all()
    active_employee_ids = {employee.id for employee in active_employees}
    today_rows = db.execute(
        select(AttendanceDaily, Employee)
        .join(Employee, Employee.id == AttendanceDaily.employee_id)
        .where(AttendanceDaily.date == today)
        .order_by(Employee.employee_code)
    ).all()

    inside = []
    outside = []
    present_employee_ids: set[int] = set()
    table_rows = []
    for attendance, employee in today_rows:
        if attendance.first_login_at is not None:
            if employee.id in active_employee_ids:
                present_employee_ids.add(employee.id)
            if attendance.final_logout_at is None:
                inside.append(attendance)
            else:
                outside.append(attendance)
        table_rows.append(
            {
                "attendance_id": attendance.id,
                "employee_id": employee.id,
                "employee_code": employee.employee_code,
                "full_name": employee.full_name,
                "first_login_at": attendance.first_login_at,
                "final_logout_at": attendance.final_logout_at,
                "status": attendance.status,
                "manually_edited": attendance.manually_edited,
            }
        )

    is_holiday = db.scalar(
        select(Holiday.id).where(Holiday.date == today).limit(1)
    ) is not None
    is_weekly_off = today.weekday() in get_weekly_off_days(db)
    absent = (
        0
        if is_holiday or is_weekly_off
        else len(active_employee_ids - present_employee_ids)
    )
    return {
        "date": today,
        "inside_count": len(inside),
        "outside_count": len(outside),
        "absent_count": absent,
        "inside": inside,
        "outside": outside,
        "table_rows": table_rows,
        "admin_contact_email": admin_contact_email,
    }
