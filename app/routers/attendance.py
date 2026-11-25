from datetime import date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AttendanceDaily, User
from app.routers.auth import require_admin_roles
from app.services.attendance import (
    AttendanceConfigurationError,
    AttendanceRuleError,
    add_holiday,
    attendance_working_seconds,
    get_attendance_settings,
    remove_holiday,
    set_late_cutoff,
    set_weekly_off_days,
)
from app.services.biometrics import (
    FaceEmbeddingEncryptionError,
    get_face_match_threshold,
    set_face_match_threshold,
)

router = APIRouter(prefix="/attendance", tags=["attendance"])
ADMIN_SETTINGS_ROLES = ("standard", "super_admin")
HOLIDAY_ADMIN_ROLES = ("standard", "super_admin", "hr")


class AttendanceResponse(BaseModel):
    employee_id: int
    date: date
    status: str
    first_login_at: datetime | None
    final_logout_at: datetime | None
    working_seconds: int | None
    message: str


class LateCutoffRequest(BaseModel):
    cutoff_time: time | None = Field(...)


class WeeklyOffDaysRequest(BaseModel):
    weekdays: list[StrictInt] = Field(max_length=7)


class HolidayRequest(BaseModel):
    date: date
    name: str = Field(min_length=1, max_length=200)


class AttendanceSettingsResponse(BaseModel):
    late_cutoff_time: str | None
    weekly_off_days: list[int]


class FaceMatchThresholdRequest(BaseModel):
    threshold: float = Field(ge=0, le=1)


class FaceMatchThresholdResponse(BaseModel):
    threshold: float


class HolidayResponse(BaseModel):
    date: date
    name: str


def _response(record: AttendanceDaily, message: str) -> AttendanceResponse:
    return AttendanceResponse(
        employee_id=record.employee_id,
        date=record.date,
        status=record.status,
        first_login_at=record.first_login_at,
        final_logout_at=record.final_logout_at,
        working_seconds=attendance_working_seconds(record),
        message=message,
    )


def _raise_http_error(error: AttendanceRuleError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail=error.message,
    ) from error


def _raise_configuration_error(error: AttendanceConfigurationError) -> None:
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=str(error),
    ) from error


@router.get("/admin/settings", response_model=AttendanceSettingsResponse)
def read_attendance_settings(
    _admin: Annotated[User, Depends(require_admin_roles(*ADMIN_SETTINGS_ROLES))],
    db: Annotated[Session, Depends(get_db)],
) -> AttendanceSettingsResponse:
    try:
        return AttendanceSettingsResponse(**get_attendance_settings(db))
    except AttendanceConfigurationError as error:
        _raise_configuration_error(error)


@router.get(
    "/admin/settings/face-match-threshold",
    response_model=FaceMatchThresholdResponse,
)
def read_face_match_threshold(
    _admin: Annotated[User, Depends(require_admin_roles(*ADMIN_SETTINGS_ROLES))],
    db: Annotated[Session, Depends(get_db)],
) -> FaceMatchThresholdResponse:
    try:
        return FaceMatchThresholdResponse(threshold=get_face_match_threshold(db))
    except FaceEmbeddingEncryptionError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error


@router.put(
    "/admin/settings/face-match-threshold",
    response_model=FaceMatchThresholdResponse,
)
def update_face_match_threshold(
    update: FaceMatchThresholdRequest,
    _admin: Annotated[User, Depends(require_admin_roles(*ADMIN_SETTINGS_ROLES))],
    db: Annotated[Session, Depends(get_db)],
) -> FaceMatchThresholdResponse:
    set_face_match_threshold(db, update.threshold)
    return FaceMatchThresholdResponse(threshold=update.threshold)


@router.put("/admin/settings/late-cutoff", response_model=AttendanceSettingsResponse)
def update_late_cutoff(
    update: LateCutoffRequest,
    _admin: Annotated[User, Depends(require_admin_roles(*ADMIN_SETTINGS_ROLES))],
    db: Annotated[Session, Depends(get_db)],
) -> AttendanceSettingsResponse:
    if update.cutoff_time is not None and update.cutoff_time.tzinfo is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cutoff time must not include a timezone.",
        )
    set_late_cutoff(db, update.cutoff_time)
    try:
        return AttendanceSettingsResponse(**get_attendance_settings(db))
    except AttendanceConfigurationError as error:
        _raise_configuration_error(error)


@router.put("/admin/settings/weekly-offs", response_model=AttendanceSettingsResponse)
def update_weekly_off_days(
    update: WeeklyOffDaysRequest,
    _admin: Annotated[User, Depends(require_admin_roles(*ADMIN_SETTINGS_ROLES))],
    db: Annotated[Session, Depends(get_db)],
) -> AttendanceSettingsResponse:
    try:
        set_weekly_off_days(db, update.weekdays)
    except AttendanceRuleError as error:
        _raise_http_error(error)
    try:
        return AttendanceSettingsResponse(**get_attendance_settings(db))
    except AttendanceConfigurationError as error:
        _raise_configuration_error(error)


@router.post(
    "/admin/holidays",
    response_model=HolidayResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_holiday(
    holiday: HolidayRequest,
    _admin: Annotated[User, Depends(require_admin_roles(*HOLIDAY_ADMIN_ROLES))],
    db: Annotated[Session, Depends(get_db)],
) -> HolidayResponse:
    try:
        record = add_holiday(db, holiday_date=holiday.date, name=holiday.name)
    except AttendanceRuleError as error:
        _raise_http_error(error)
    return HolidayResponse(date=record.date, name=record.name)


@router.delete("/admin/holidays/{holiday_date}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holiday(
    holiday_date: date,
    _admin: Annotated[User, Depends(require_admin_roles(*HOLIDAY_ADMIN_ROLES))],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    try:
        remove_holiday(db, holiday_date=holiday_date)
    except AttendanceRuleError as error:
        _raise_http_error(error)
