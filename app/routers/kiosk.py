import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.routers.attendance import AttendanceResponse, _response
from app.services.attendance import AttendanceRuleError
from app.services.face_enrollment import EnrollmentError, decode_capture
from app.services.kiosk_scans import KioskScanCoordinator, KioskScanError
from app.services.scan_rate_limit import limit_scan_frame, limit_scan_start
from app.services.vision_runtime import get_kiosk_scan_coordinator

router = APIRouter(tags=["kiosk"])
templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))
logger = logging.getLogger(__name__)


class ScanStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScanStartResponse(BaseModel):
    scan_id: str
    prompt: str


class ScanFrameRequest(BaseModel):
    scan_id: str = Field(min_length=20, max_length=128)
    capture: str = Field(max_length=2_800_000)


class ScanProgressResponse(BaseModel):
    state: str
    prompt: str
    employee_id: int | None = None
    employee_name: str | None = None
    similarity: float | None = None
    attendance: AttendanceResponse | None = None
    message: str | None = None
    operation: Literal["login", "logout"] | None = None
    event_at: datetime | None = None


class ScanCancelRequest(BaseModel):
    scan_id: str = Field(min_length=20, max_length=128)


@router.get("/attendance", response_class=HTMLResponse)
def attendance_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="attendance.html",
        context={},
    )


def _raise_scan_error(error: KioskScanError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.post("/attendance/scans/start", response_model=ScanStartResponse)
def start_scan(
    scan: ScanStartRequest,
    _rate_limit: Annotated[None, Depends(limit_scan_start)],
    db: Annotated[Session, Depends(get_db)],
    coordinator: Annotated[KioskScanCoordinator, Depends(get_kiosk_scan_coordinator)],
) -> ScanStartResponse:
    try:
        scan_id, prompt = coordinator.start_scan(
            db, kiosk_id=get_settings().kiosk_id
        )
    except KioskScanError as error:
        logger.warning("Kiosk scan start rejected: status=%d", error.status_code)
        _raise_scan_error(error)
    logger.info("Kiosk automatic scan started.")
    return ScanStartResponse(scan_id=scan_id, prompt=prompt)


@router.post("/attendance/scans/frame", response_model=ScanProgressResponse)
def process_scan_frame(
    body: ScanFrameRequest,
    _rate_limit: Annotated[None, Depends(limit_scan_frame)],
    db: Annotated[Session, Depends(get_db)],
    coordinator: Annotated[KioskScanCoordinator, Depends(get_kiosk_scan_coordinator)],
) -> ScanProgressResponse:
    try:
        frame = decode_capture(body.capture)
        progress = coordinator.process_frame(db, scan_id=body.scan_id, frame=frame)
    except EnrollmentError as error:
        coordinator.cancel_scan(body.scan_id)
        logger.warning("Kiosk capture rejected: status=%d", error.status_code)
        raise HTTPException(
            status_code=error.status_code, detail=error.message
        ) from error
    except (KioskScanError, AttendanceRuleError) as error:
        coordinator.cancel_scan(body.scan_id)
        if isinstance(error, KioskScanError):
            logger.warning("Kiosk scan rejected: status=%d", error.status_code)
            _raise_scan_error(error)
        logger.warning("Attendance scan rejected: status=%d", error.status_code)
        raise HTTPException(
            status_code=error.status_code, detail=error.message
        ) from error
    except Exception:
        coordinator.cancel_scan(body.scan_id)
        logger.exception("Unexpected kiosk scan processing failure.")
        raise

    if progress.state == "complete":
        logger.info("Kiosk scan completed.")
    attendance = (
        _response(progress.attendance, progress.message or "Attendance recorded")
        if progress.attendance is not None
        else None
    )
    return ScanProgressResponse(
        state=progress.state,
        prompt=progress.prompt,
        employee_id=progress.employee_id,
        employee_name=progress.employee_name,
        similarity=progress.similarity,
        attendance=attendance,
        message=progress.message,
        operation=progress.operation,
        event_at=progress.event_at,
    )


@router.post(
    "/attendance/scans/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
)
def cancel_scan(
    body: ScanCancelRequest,
    coordinator: Annotated[KioskScanCoordinator, Depends(get_kiosk_scan_coordinator)],
) -> Response:
    coordinator.cancel_scan(body.scan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
