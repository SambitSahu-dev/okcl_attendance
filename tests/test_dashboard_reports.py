from datetime import UTC, date, datetime
from io import BytesIO

from openpyxl import load_workbook

from app.models import AttendanceDaily, Employee
from app.services.dashboard import dashboard_data
from app.services.monthly_reports import build_monthly_report
from jobs.monthly_report import save_monthly_report


def test_dashboard_tiles_distinguish_inside_outside_absent_and_off_days(
    db_session, employee
) -> None:
    outside = Employee(employee_code="EMP-002", full_name="Outside Employee")
    absent = Employee(employee_code="EMP-003", full_name="Absent Employee")
    db_session.add_all([outside, absent])
    db_session.flush()
    today = date(2026, 11, 24)
    db_session.add_all(
        [
            AttendanceDaily(
                employee_id=employee.id,
                date=today,
                first_login_at=datetime(2026, 11, 24, 8, 0, tzinfo=UTC),
                last_login_at=datetime(2026, 11, 24, 12, 0, tzinfo=UTC),
                status="Incomplete",
            ),
            AttendanceDaily(
                employee_id=outside.id,
                date=today,
                first_login_at=datetime(2026, 11, 24, 8, 5, tzinfo=UTC),
                final_logout_at=datetime(2026, 11, 24, 16, 0, tzinfo=UTC),
                status="Present",
            ),
        ]
    )
    db_session.commit()

    result = dashboard_data(
        db_session, today=today, admin_contact_email="help@example.org"
    )
    assert result["inside_count"] == 1
    assert result["outside_count"] == 1
    assert result["absent_count"] == 1
    assert result["admin_contact_email"] == "help@example.org"

    weekend = dashboard_data(db_session, today=date(2026, 11, 28))
    assert weekend["absent_count"] == 0


def test_excel_report_includes_derived_absences_and_manual_edit_flag(
    db_session, employee
) -> None:
    now = datetime.now(UTC)
    today = now.date()
    db_session.add(
        AttendanceDaily(
            employee_id=employee.id,
            date=today,
            first_login_at=datetime(
                today.year, today.month, today.day, 8, 0, tzinfo=UTC
            ),
            last_login_at=datetime(
                today.year, today.month, today.day, 12, 0, tzinfo=UTC
            ),
            final_logout_at=datetime(
                today.year, today.month, today.day, 16, 0, tzinfo=UTC
            ),
            status="Late",
            manually_edited=True,
        )
    )
    db_session.commit()

    report = build_monthly_report(db_session, year=today.year, month=today.month)
    workbook = load_workbook(BytesIO(report.getvalue()), data_only=True)
    sheet = workbook["Monthly Attendance"]
    row = next(
        sheet.iter_rows(min_row=2, values_only=True)
    )

    assert row[0] == employee.employee_code
    assert row[3].date() == today
    assert row[4] == "Late"
    assert row[5].hour == 8
    assert row[6].hour == 16
    assert row[7] == 8
    assert row[8] == "Yes"
    assert sheet["I1"].value == "Edited Manually"


def test_monthly_report_job_writes_xlsx_to_configured_directory(
    db_session, employee, tmp_path
) -> None:
    today = datetime.now(UTC).date()

    report_path = save_monthly_report(
        db_session,
        year=today.year,
        month=today.month,
        output_directory=tmp_path,
    )

    assert report_path.is_file()
    assert report_path.name == f"attendance-{today.year:04d}-{today.month:02d}.xlsx"
    assert load_workbook(report_path).sheetnames == ["Monthly Attendance"]
