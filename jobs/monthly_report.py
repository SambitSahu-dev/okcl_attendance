import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.monthly_reports import build_monthly_report
from jobs.backup import storage_maintenance_lock

logger = logging.getLogger(__name__)


class ReportPurgeError(RuntimeError):
    pass


def save_monthly_report(
    db: Session,
    *,
    year: int,
    month: int,
    output_directory: Path | None = None,
) -> Path:
    with storage_maintenance_lock:
        directory = (
            output_directory
            or Path(__file__).resolve().parents[1] / "storage" / "reports"
        )
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"attendance-{year:04d}-{month:02d}.xlsx"
        temporary = destination.with_suffix(".xlsx.tmp")
        temporary.write_bytes(build_monthly_report(db, year=year, month=month).getvalue())
        temporary.replace(destination)
        logger.info("Monthly Excel report generated: file=%s", destination.name)
        return destination


def purge_employee_from_reports(
    report_directory: Path, employee_id: int
) -> int:
    with storage_maintenance_lock:
        if not report_directory.is_dir():
            return 0

        updated = 0
        for report_path in sorted(report_directory.glob("attendance-*.xlsx")):
            try:
                workbook = load_workbook(report_path)
            except (OSError, ValueError, BadZipFile, InvalidFileException) as error:
                raise ReportPurgeError(
                    f"Could not read monthly report {report_path.name}."
                ) from error
            changed = False
            for sheet in workbook.worksheets:
                headings = {
                    cell.value: cell.column
                    for cell in sheet[1]
                    if cell.value is not None
                }
                employee_id_column = headings.get("Employee ID")
                if employee_id_column is None:
                    continue
                for row_number in range(sheet.max_row, 1, -1):
                    value = sheet.cell(row=row_number, column=employee_id_column).value
                    if value == employee_id or value == str(employee_id):
                        sheet.delete_rows(row_number)
                        changed = True
            if not changed:
                continue

            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=".employee-purge-",
                    suffix=".xlsx",
                    dir=report_directory,
                    delete=False,
                ) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                workbook.save(temporary_path)
                temporary_path.replace(report_path)
            except OSError as error:
                raise ReportPurgeError(
                    f"Could not purge employee {employee_id} from {report_path.name}."
                ) from error
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            updated += 1
        return updated


def generate_current_month_report() -> Path:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        return save_monthly_report(db, year=now.year, month=now.month)
