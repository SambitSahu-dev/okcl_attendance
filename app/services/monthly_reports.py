import calendar
from datetime import UTC, date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AttendanceDaily, Employee, Holiday
from app.services.attendance import attendance_working_seconds, get_weekly_off_days


def build_monthly_report(db: Session, *, year: int, month: int) -> BytesIO:
    if not 1 <= month <= 12:
        raise ValueError("Month must be between 1 and 12.")

    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    today = datetime.now(UTC).date()
    report_end = min(month_end, today)
    last_report_day = 0 if month_start > today else min(
        month_end.day, report_end.day
    )
    employees = db.scalars(
        select(Employee).order_by(Employee.employee_code)
    ).all()
    attendance_records = db.scalars(
        select(AttendanceDaily)
        .where(
            AttendanceDaily.date >= month_start,
            AttendanceDaily.date <= month_end,
        )
        .order_by(AttendanceDaily.date, AttendanceDaily.employee_id)
    ).all()
    by_employee_date = {
        (record.employee_id, record.date): record for record in attendance_records
    }
    holidays = {
        holiday.date: holiday.name
        for holiday in db.scalars(
            select(Holiday).where(
                Holiday.date >= month_start,
                Holiday.date <= month_end,
            )
        ).all()
    }
    weekly_offs = set(get_weekly_off_days(db))

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Monthly Attendance"
    sheet.append(
        [
            "Employee Code",
            "Employee ID",
            "Employee Name",
            "Date",
            "Status",
            "First Login (UTC)",
            "Final Logout (UTC)",
            "Working Hours",
            "Edited Manually",
        ]
    )
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.alignment = Alignment(horizontal="center")

    for employee in employees:
        if employee.created_at.date() > report_end:
            continue
        for day_number in range(1, last_report_day + 1):
            attendance_date = date(year, month, day_number)
            if attendance_date < employee.created_at.date():
                continue
            record = by_employee_date.get((employee.id, attendance_date))
            if record is None:
                status = (
                    "Holiday"
                    if attendance_date in holidays
                    else "Weekly Off"
                    if attendance_date.weekday() in weekly_offs
                    else "Absent"
                )
                first_login = final_logout = None
                working_hours = None
                manually_edited = False
            else:
                status = record.status
                first_login = _excel_datetime(record.first_login_at)
                final_logout = _excel_datetime(record.final_logout_at)
                seconds = attendance_working_seconds(record)
                working_hours = seconds / 3600 if seconds is not None else None
                manually_edited = record.manually_edited
            sheet.append(
                [
                    employee.employee_code,
                    employee.id,
                    employee.full_name,
                    attendance_date,
                    status,
                    first_login,
                    final_logout,
                    working_hours,
                    "Yes" if manually_edited else "No",
                ]
            )

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in {
        "A": 18,
        "B": 13,
        "C": 28,
        "D": 14,
        "E": 22,
        "F": 23,
        "G": 23,
        "H": 17,
        "I": 19,
    }.items():
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        row[3].number_format = "yyyy-mm-dd"
        row[5].number_format = "yyyy-mm-dd hh:mm:ss"
        row[6].number_format = "yyyy-mm-dd hh:mm:ss"
        row[7].number_format = "0.00"

    workbook.properties.title = f"Attendance report {year:04d}-{month:02d}"
    workbook.properties.subject = "Monthly employee attendance"
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _excel_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
