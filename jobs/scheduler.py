from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from jobs.attendance_jobs import run_absent_marking, run_incomplete_flagging
from jobs.backup import run_nightly_backup
from jobs.monthly_report import generate_current_month_report
from jobs.retention import run_retention


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    shared_options = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 3600,
        "replace_existing": True,
    }
    scheduler.add_job(
        run_absent_marking,
        CronTrigger(hour=23, minute=50, timezone="UTC"),
        id="mark-absent-attendance",
        **shared_options,
    )
    scheduler.add_job(
        run_incomplete_flagging,
        CronTrigger(hour=23, minute=55, timezone="UTC"),
        id="flag-incomplete-attendance",
        **shared_options,
    )
    scheduler.add_job(
        generate_current_month_report,
        CronTrigger(day="last", hour=23, minute=58, timezone="UTC"),
        id="monthly-attendance-report",
        **shared_options,
    )
    scheduler.add_job(
        run_nightly_backup,
        CronTrigger(hour=0, minute=30, timezone="UTC"),
        id="nightly-attendance-backup",
        **shared_options,
    )
    scheduler.add_job(
        run_retention,
        CronTrigger(hour=1, minute=0, timezone="UTC"),
        id="image-and-backup-retention",
        **shared_options,
    )
    return scheduler
