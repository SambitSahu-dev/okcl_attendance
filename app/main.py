import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db.base import Base
from app.db.migrations import ensure_compatibility_schema
from app.db.session import engine
from app.logging_config import configure_logging
from app.models import (  # noqa: F401 - register all models before create_all
    AttendanceAudit,
    AttendanceDaily,
    Consent,
    Employee,
    FaceEmbedding,
    Holiday,
    Kiosk,
    LogoutLog,
    MonthLock,
    ScanAttempt,
    Setting,
    User,
)
from app.routers.attendance import router as attendance_router
from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router
from app.routers.enrollment import router as enrollment_router
from app.routers.kiosk import router as kiosk_router
from app.routers.management import router as management_router
from app.services.scan_rate_limit import ScanRateLimiter
from jobs.scheduler import build_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine)
    ensure_compatibility_schema(engine)
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Attendance scheduler started with %d jobs.", len(scheduler.get_jobs()))
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Attendance scheduler stopped.")


settings = get_settings()
if not settings.secret_key:
    raise RuntimeError("SECRET_KEY must be configured before starting the application")
configure_logging(settings.log_level)

app = FastAPI(title="AI Employee Attendance System", lifespan=lifespan)
app.state.scan_rate_limiter = ScanRateLimiter(
    start_limit=settings.scan_start_limit_per_minute,
    frame_limit=settings.scan_frame_limit_per_minute,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=False,
)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
app.include_router(auth_router)
app.include_router(attendance_router)
app.include_router(enrollment_router)
app.include_router(kiosk_router)
app.include_router(dashboard_router)
app.include_router(management_router)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="login.html", context={})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
