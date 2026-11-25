import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import SessionLocal
from app.models import AttendanceDaily
from jobs.backup import PROJECT_ROOT

logger = logging.getLogger(__name__)


def purge_expired_images(
    db: Session,
    *,
    retention_days: int,
    today: date | None = None,
    image_directory: Path | None = None,
) -> int:
    if retention_days < 1:
        raise ValueError("Image retention must be at least one day.")
    cutoff = (today or datetime.now(UTC).date()) - timedelta(days=retention_days)
    root = (image_directory or PROJECT_ROOT / "storage" / "images").resolve()
    records = db.scalars(
        select(AttendanceDaily).where(
            AttendanceDaily.date < cutoff,
            AttendanceDaily.image_path.is_not(None),
        )
    ).all()
    purged = 0
    for record in records:
        if record.image_sha256 is None:
            logger.error(
                "Skipping expired image without a stored SHA-256 hash: attendance_id=%d",
                record.id,
            )
            continue
        expected_relative_path = (
            Path("storage")
            / "images"
            / str(record.employee_id)
            / f"{record.date:%Y-%m-%d}.jpg"
        ).as_posix()
        if record.image_path != expected_relative_path:
            logger.error(
                "Skipping invalid retained-image path: attendance_id=%d",
                record.id,
            )
            continue

        image_path = (
            root / str(record.employee_id) / f"{record.date:%Y-%m-%d}.jpg"
        ).resolve()
        if not image_path.is_relative_to(root):
            logger.error(
                "Skipping image outside retention directory: attendance_id=%d",
                record.id,
            )
            continue
        if image_path.is_file():
            image_path.unlink()
        record.image_path = None
        purged += 1

    if purged:
        db.commit()
    return purged


def purge_expired_backups(
    backup_directory: Path,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    if retention_days < 1:
        raise ValueError("Backup retention must be at least one day.")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    if not backup_directory.is_dir():
        return 0

    deleted = 0
    for backup_path in backup_directory.glob("attendance-backup-*.zip"):
        modified_at = datetime.fromtimestamp(backup_path.stat().st_mtime, UTC)
        if modified_at < cutoff:
            backup_path.unlink()
            deleted += 1
    return deleted


def run_retention() -> None:
    settings = get_settings()
    backup_directory = PROJECT_ROOT / "storage" / "backups"
    with SessionLocal() as db:
        image_count = purge_expired_images(
            db,
            retention_days=settings.image_retention_days,
        )
    backup_count = purge_expired_backups(
        backup_directory,
        retention_days=settings.backup_retention_days,
    )
    logger.info(
        "Retention completed: images_purged=%d backups_purged=%d",
        image_count,
        backup_count,
    )
