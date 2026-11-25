import logging
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from app.db.session import engine

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
storage_maintenance_lock = RLock()


class BackupPurgeError(RuntimeError):
    pass


def _create_backup(
    *,
    database_path: Path,
    image_directory: Path,
    output_directory: Path,
    created_at: datetime | None = None,
) -> Path:
    database_path = database_path.expanduser().resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {database_path}")
    output_directory.mkdir(parents=True, exist_ok=True)

    timestamp = (created_at or datetime.now(UTC)).astimezone(UTC)
    destination = output_directory / (
        f"attendance-backup-{timestamp:%Y%m%dT%H%M%SZ}.zip"
    )
    temporary_archive = destination.with_suffix(".zip.tmp")
    try:
        with tempfile.TemporaryDirectory(
            prefix=".attendance-backup-", dir=output_directory
        ) as temporary_directory:
            snapshot_path = Path(temporary_directory) / "attendance.db"
            with (
                closing(sqlite3.connect(database_path)) as source,
                closing(sqlite3.connect(snapshot_path)) as snapshot,
            ):
                source.backup(snapshot)

            with ZipFile(temporary_archive, "w", compression=ZIP_DEFLATED) as archive:
                archive.write(snapshot_path, "attendance.db")
                if image_directory.is_dir():
                    for image_path in sorted(image_directory.rglob("*")):
                        if image_path.is_file():
                            archive.write(
                                image_path,
                                Path("images", image_path.relative_to(image_directory)),
                            )
            temporary_archive.replace(destination)
    except Exception:
        temporary_archive.unlink(missing_ok=True)
        raise

    logger.info("Nightly attendance backup completed: file=%s", destination.name)
    return destination


def create_backup(
    *,
    database_path: Path,
    image_directory: Path,
    output_directory: Path,
    created_at: datetime | None = None,
) -> Path:
    with storage_maintenance_lock:
        return _create_backup(
            database_path=database_path,
            image_directory=image_directory,
            output_directory=output_directory,
            created_at=created_at,
        )


def _purge_employee_from_snapshot(database_path: Path, employee_id: int) -> bool:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "employees" not in tables:
            raise sqlite3.DatabaseError("Backup database has no employees table.")
        if connection.execute(
            "SELECT 1 FROM employees WHERE id = ?", (employee_id,)
        ).fetchone() is None:
            return False

        attendance_ids = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM attendance_daily WHERE employee_id = ?",
                (employee_id,),
            )
        ] if "attendance_daily" in tables else []
        user_ids = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM users WHERE employee_id = ?", (employee_id,)
            )
        ] if "users" in tables else []

        connection.execute("BEGIN")
        if "attendance_audit" in tables:
            conditions = []
            parameters: list[int] = []
            if attendance_ids:
                placeholders = ", ".join("?" for _ in attendance_ids)
                conditions.append(f"attendance_id IN ({placeholders})")
                parameters.extend(attendance_ids)
            if user_ids:
                placeholders = ", ".join("?" for _ in user_ids)
                conditions.append(f"actor_user_id IN ({placeholders})")
                parameters.extend(user_ids)
            if conditions:
                connection.execute(
                    "DELETE FROM attendance_audit WHERE " + " OR ".join(conditions),
                    parameters,
                )

        if "logout_log" in tables and attendance_ids:
            placeholders = ", ".join("?" for _ in attendance_ids)
            connection.execute(
                f"DELETE FROM logout_log WHERE attendance_id IN ({placeholders})",
                attendance_ids,
            )

        if "month_locks" in tables and user_ids:
            placeholders = ", ".join("?" for _ in user_ids)
            connection.execute(
                "UPDATE month_locks SET locked_by_user_id = NULL "
                f"WHERE locked_by_user_id IN ({placeholders})",
                user_ids,
            )
            connection.execute(
                "UPDATE month_locks SET updated_by_user_id = NULL "
                f"WHERE updated_by_user_id IN ({placeholders})",
                user_ids,
            )

        for table in (
            "logout_log",
            "scan_attempts",
            "face_embeddings",
            "consents",
            "attendance_daily",
            "users",
        ):
            if table in tables:
                connection.execute(
                    f"DELETE FROM {table} WHERE employee_id = ?", (employee_id,)
                )
        connection.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
        connection.commit()
        return True
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def _is_employee_image_entry(name: str, employee_id: int) -> bool:
    parts = Path(name).parts
    return len(parts) >= 2 and parts[0] == "images" and parts[1] == str(employee_id)


def _purge_employee_from_backup(backup_path: Path, employee_id: int) -> bool:
    with tempfile.TemporaryDirectory(
        prefix=".employee-purge-", dir=backup_path.parent
    ) as temporary_directory:
        temporary_path = Path(temporary_directory)
        snapshot_path = temporary_path / "attendance.db"
        rewritten_path = temporary_path / backup_path.name
        with ZipFile(backup_path) as source:
            if "attendance.db" not in source.namelist():
                raise BackupPurgeError("Backup archive has no attendance.db snapshot.")
            snapshot_path.write_bytes(source.read("attendance.db"))
            database_changed = _purge_employee_from_snapshot(
                snapshot_path, employee_id
            )
            images_changed = any(
                _is_employee_image_entry(info.filename, employee_id)
                for info in source.infolist()
            )
            if not database_changed and not images_changed:
                return False

            with ZipFile(
                rewritten_path, "w", compression=ZIP_DEFLATED
            ) as destination:
                for info in source.infolist():
                    if _is_employee_image_entry(info.filename, employee_id):
                        continue
                    if info.filename == "attendance.db":
                        destination.writestr(info, snapshot_path.read_bytes())
                    else:
                        destination.writestr(info, source.read(info))
        rewritten_path.replace(backup_path)
    return True


def purge_employee_from_backups(
    backup_directory: Path, employee_id: int
) -> int:
    with storage_maintenance_lock:
        if not backup_directory.is_dir():
            return 0

        updated = 0
        for backup_path in sorted(backup_directory.glob("attendance-backup-*.zip")):
            try:
                updated += _purge_employee_from_backup(backup_path, employee_id)
            except (OSError, sqlite3.Error, BadZipFile, KeyError) as error:
                raise BackupPurgeError(
                    f"Could not purge employee {employee_id} from {backup_path.name}."
                ) from error
        return updated


def run_nightly_backup() -> Path:
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Nightly backup currently supports SQLite databases only.")
    database_name = engine.url.database
    if database_name in (None, "", ":memory:"):
        raise RuntimeError("Nightly backup requires a file-backed SQLite database.")

    database_path = Path(database_name)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return create_backup(
        database_path=database_path,
        image_directory=PROJECT_ROOT / "storage" / "images",
        output_directory=PROJECT_ROOT / "storage" / "backups",
    )
