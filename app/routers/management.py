from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.models import (
    AttendanceAudit,
    AttendanceDaily,
    Consent,
    Employee,
    FaceEmbedding,
    Holiday,
    LogoutLog,
    MonthLock,
    ScanAttempt,
    Setting,
    User,
)
from app.routers.auth import require_admin, require_admin_roles
from app.services.admin_attendance import (
    ATTENDANCE_STATUSES,
    AttendanceEditError,
    edit_attendance,
    is_month_locked,
    update_month_lock,
)
from app.services.auth import hash_password, verify_password
from app.services.login_images import LoginImageStore
from app.services.monthly_reports import build_monthly_report
from app.services.vision_runtime import get_login_image_store
from jobs.backup import (
    PROJECT_ROOT,
    BackupPurgeError,
    purge_employee_from_backups,
    storage_maintenance_lock,
)
from jobs.monthly_report import ReportPurgeError, purge_employee_from_reports

router = APIRouter(tags=["admin management"])
templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))


def get_backup_directory() -> Path:
    return PROJECT_ROOT / "storage" / "backups"


def get_report_directory() -> Path:
    return PROJECT_ROOT / "storage" / "reports"


SUPER_ADMIN = ("super_admin",)
EMPLOYEE_MANAGERS = ("standard", "super_admin", "hr")
ATTENDANCE_VIEWERS = (
    "standard",
    "super_admin",
    "hr",
    "accounts",
    "team_lead",
    "audit",
)
ATTENDANCE_EDITORS = ("standard", "super_admin", "hr")
REPORT_VIEWERS = ("standard", "super_admin", "accounts")
AUDIT_VIEWERS = ("standard", "super_admin", "hr", "audit")
HOLIDAY_MANAGERS = ("standard", "super_admin", "hr")
SUB_ADMIN_ROLES = ("standard", "hr", "accounts", "team_lead", "audit")


class EmployeeCreateRequest(BaseModel):
    employee_code: str = Field(min_length=1, max_length=64)
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(
        default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class EmployeeUpdateRequest(BaseModel):
    employee_code: str = Field(min_length=1, max_length=64)
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(
        default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    is_active: bool


class EmployeeResponse(BaseModel):
    id: int
    employee_code: str
    full_name: str
    email: str | None
    is_active: bool
    is_deleted: bool
    username: str | None
    face_enrolled: bool


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=128)
    admin_role: Literal["standard", "hr", "accounts", "team_lead", "audit"]


class AdminUserUpdateRequest(BaseModel):
    admin_role: Literal["standard", "hr", "accounts", "team_lead", "audit"]
    is_active: bool


class AdminUserResponse(BaseModel):
    id: int
    username: str
    admin_role: str | None
    is_active: bool


class AdminProfileUpdateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(
        default=None,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str | None = Field(default=None, min_length=12, max_length=128)
    confirm_new_password: str | None = Field(
        default=None, min_length=12, max_length=128
    )

    @model_validator(mode="after")
    def validate_password_confirmation(self) -> "AdminProfileUpdateRequest":
        if (
            self.new_password != self.confirm_new_password
            and (self.new_password is not None or self.confirm_new_password is not None)
        ):
            raise ValueError("New password and confirmation must match.")
        return self


class AdminProfileResponse(BaseModel):
    id: int
    username: str
    display_name: str | None
    email: str | None
    admin_role: str | None


class AttendanceEditRequest(BaseModel):
    employee_id: int = Field(gt=0)
    date: date
    first_login_at: datetime | None
    final_logout_at: datetime | None
    status: str
    reason: str = Field(min_length=1, max_length=1000)


class AttendanceRecordResponse(BaseModel):
    id: int
    employee_id: int
    employee_code: str
    full_name: str
    date: date
    first_login_at: datetime | None
    final_logout_at: datetime | None
    status: str
    manually_edited: bool


class MonthLockRequest(BaseModel):
    year: int = Field(ge=1900, le=9999)
    month: int = Field(ge=1, le=12)
    locked: bool


class MonthLockResponse(BaseModel):
    year: int
    month: int
    locked: bool
    locked_at: datetime | None
    locked_by_user_id: int | None
    updated_by_user_id: int | None


def _employee_response(
    employee: Employee, *, face_enrolled: bool = False
) -> EmployeeResponse:
    return EmployeeResponse(
        id=employee.id,
        employee_code=employee.employee_code,
        full_name=employee.full_name,
        email=employee.email,
        is_active=employee.is_active,
        is_deleted=employee.deleted_at is not None,
        username=employee.user.username if employee.user is not None else None,
        face_enrolled=face_enrolled,
    )


def _attendance_response(
    record: AttendanceDaily, employee: Employee
) -> AttendanceRecordResponse:
    return AttendanceRecordResponse(
        id=record.id,
        employee_id=record.employee_id,
        employee_code=employee.employee_code,
        full_name=employee.full_name,
        date=record.date,
        first_login_at=record.first_login_at,
        final_logout_at=record.final_logout_at,
        status=record.status,
        manually_edited=record.manually_edited,
    )


def _month_lock_response(record: MonthLock) -> MonthLockResponse:
    return MonthLockResponse(
        year=record.year,
        month=record.month,
        locked=record.locked,
        locked_at=record.locked_at,
        locked_by_user_id=record.locked_by_user_id,
        updated_by_user_id=record.updated_by_user_id,
    )


def _raise_edit_error(error: AttendanceEditError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.get("/admin/profile", response_class=HTMLResponse)
def admin_profile_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="admin_profile.html",
        context={"user": admin, "active_page": "profile"},
    )


@router.put("/api/admin/profile", response_model=AdminProfileResponse)
def update_admin_profile(
    payload: AdminProfileUpdateRequest,
    admin: Annotated[User, Depends(require_admin)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AdminProfileResponse:
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    username = payload.username.strip()
    display_name = payload.display_name.strip() if payload.display_name else None
    email = payload.email.strip().lower() if payload.email else None
    if not username:
        raise HTTPException(status_code=422, detail="Username is required.")

    settings = get_settings()
    bootstrap_keys = {
        settings.admin_username: "bootstrap_admin_user_id",
        settings.super_admin_username: "bootstrap_super_admin_user_id",
    }
    bootstrap_key = bootstrap_keys.get(admin.username)
    if bootstrap_key is not None:
        marker = db.get(Setting, bootstrap_key)
        if marker is None:
            marker = Setting(key=bootstrap_key, value=str(admin.id))
            db.add(marker)
        else:
            marker.value = str(admin.id)

    admin.username = username
    admin.display_name = display_name
    admin.email = email
    if payload.new_password is not None:
        admin.password_hash = hash_password(payload.new_password)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Username or email is already in use.",
        ) from error
    db.refresh(admin)
    request.session["user_id"] = admin.id
    return AdminProfileResponse(
        id=admin.id,
        username=admin.username,
        display_name=admin.display_name,
        email=admin.email,
        admin_role=admin.admin_role,
    )


@router.get("/admin/employees", response_class=HTMLResponse)
def employees_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin_roles(*EMPLOYEE_MANAGERS))],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="employees.html",
        context={"user": admin, "active_page": "employees"},
    )


@router.get("/admin/settings", response_class=HTMLResponse)
def admin_settings_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin_roles("standard", "super_admin"))],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="admin_settings.html",
        context={"user": admin, "active_page": "settings"},
    )


@router.get("/api/admin/employees", response_model=list[EmployeeResponse])
def list_employees(
    _admin: Annotated[User, Depends(require_admin_roles(*EMPLOYEE_MANAGERS))],
    db: Annotated[Session, Depends(get_db)],
) -> list[EmployeeResponse]:
    employees = db.scalars(select(Employee).order_by(Employee.employee_code)).all()
    enrolled_ids = set(
        db.scalars(select(FaceEmbedding.employee_id).distinct()).all()
    )
    return [
        _employee_response(employee, face_enrolled=employee.id in enrolled_ids)
        for employee in employees
    ]


@router.post(
    "/api/admin/employees",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_employee(
    payload: EmployeeCreateRequest,
    _actor: Annotated[User, Depends(require_admin_roles(*EMPLOYEE_MANAGERS))],
    db: Annotated[Session, Depends(get_db)],
) -> EmployeeResponse:
    employee = Employee(
        employee_code=payload.employee_code.strip(),
        full_name=payload.full_name.strip(),
        email=str(payload.email) if payload.email is not None else None,
        is_active=True,
    )
    if not employee.employee_code or not employee.full_name:
        raise HTTPException(status_code=422, detail="Employee code and name are required.")
    db.add(employee)
    try:
        db.flush()
        db.add(
            User(
                username=payload.username.strip(),
                password_hash=hash_password(payload.password),
                role="employee",
                employee_id=employee.id,
                is_active=True,
            )
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Employee code, email, or username already exists.",
        ) from error
    db.refresh(employee)
    return _employee_response(employee)


@router.patch("/api/admin/employees/{employee_id}", response_model=EmployeeResponse)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdateRequest,
    _actor: Annotated[User, Depends(require_admin_roles(*EMPLOYEE_MANAGERS))],
    db: Annotated[Session, Depends(get_db)],
) -> EmployeeResponse:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")
    if employee.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Deleted employees cannot be edited.")
    employee.employee_code = payload.employee_code.strip()
    employee.full_name = payload.full_name.strip()
    employee.email = str(payload.email) if payload.email is not None else None
    employee.is_active = payload.is_active
    if employee.user is not None:
        employee.user.is_active = payload.is_active
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Employee code or email already exists.",
        ) from error
    db.refresh(employee)
    face_enrolled = db.scalar(
        select(FaceEmbedding.id)
        .where(FaceEmbedding.employee_id == employee.id)
        .limit(1)
    ) is not None
    return _employee_response(employee, face_enrolled=face_enrolled)


@router.delete(
    "/api/admin/employees/{employee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_employee(
    employee_id: int,
    _actor: Annotated[User, Depends(require_admin_roles(*EMPLOYEE_MANAGERS))],
    db: Annotated[Session, Depends(get_db)],
    image_store: Annotated[LoginImageStore, Depends(get_login_image_store)],
) -> Response:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")

    employee.is_active = False
    employee.deleted_at = employee.deleted_at or datetime.now(UTC)
    if employee.user is not None:
        employee.user.is_active = False
    db.commit()

    attendance_records = db.scalars(
        select(AttendanceDaily).where(AttendanceDaily.employee_id == employee_id)
    ).all()
    image_paths = sorted(
        {record.image_path for record in attendance_records if record.image_path}
    )
    try:
        for image_path in image_paths:
            image_store.delete_login_image(image_path)
    except (OSError, ValueError) as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=(
                "Employee was deactivated, but login image cleanup did not finish. "
                "Retry Delete to complete biometric cleanup."
            ),
        ) from error

    for record in attendance_records:
        record.image_path = None
    db.execute(
        delete(FaceEmbedding).where(FaceEmbedding.employee_id == employee_id)
    )
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=(
                "Employee was deactivated and images removed, but face-data cleanup "
                "did not finish. Retry Delete."
            ),
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/api/admin/employees/{employee_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
)
def permanently_delete_employee(
    employee_id: int,
    _actor: Annotated[User, Depends(require_admin_roles(*EMPLOYEE_MANAGERS))],
    db: Annotated[Session, Depends(get_db)],
    image_store: Annotated[LoginImageStore, Depends(get_login_image_store)],
    backup_directory: Annotated[Path, Depends(get_backup_directory)],
    report_directory: Annotated[Path, Depends(get_report_directory)],
) -> Response:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")
    if employee.deleted_at is None:
        raise HTTPException(
            status_code=409,
            detail="Only deleted employees can be permanently deleted.",
        )

    with storage_maintenance_lock:
        return _purge_deleted_employee_data(
            employee=employee,
            db=db,
            image_store=image_store,
            backup_directory=backup_directory,
            report_directory=report_directory,
        )


def _purge_deleted_employee_data(
    *,
    employee: Employee,
    db: Session,
    image_store: LoginImageStore,
    backup_directory: Path,
    report_directory: Path,
) -> Response:
    employee_id = employee.id
    user = employee.user
    attendance_ids = db.scalars(
        select(AttendanceDaily.id).where(AttendanceDaily.employee_id == employee_id)
    ).all()
    try:
        image_store.delete_employee_images(employee_id)
        purge_employee_from_backups(backup_directory, employee_id)
        purge_employee_from_reports(report_directory, employee_id)
    except (OSError, ValueError, BackupPurgeError, ReportPurgeError) as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "Permanent deletion could not finish cleaning stored images, "
                "backups, or reports. Database records were retained; retry the "
                "permanent delete."
            ),
        ) from error

    try:
        if attendance_ids or user is not None:
            audit_filters = []
            if attendance_ids:
                audit_filters.append(AttendanceAudit.attendance_id.in_(attendance_ids))
            if user is not None:
                audit_filters.append(AttendanceAudit.actor_user_id == user.id)
            db.execute(delete(AttendanceAudit).where(or_(*audit_filters)))
        db.execute(
            delete(LogoutLog).where(
                or_(
                    LogoutLog.employee_id == employee_id,
                    LogoutLog.attendance_id.in_(attendance_ids),
                )
            )
        )
        db.execute(delete(ScanAttempt).where(ScanAttempt.employee_id == employee_id))
        db.execute(
            delete(FaceEmbedding).where(FaceEmbedding.employee_id == employee_id)
        )
        db.execute(delete(Consent).where(Consent.employee_id == employee_id))
        db.execute(
            delete(AttendanceDaily).where(AttendanceDaily.employee_id == employee_id)
        )
        if user is not None:
            db.execute(
                sql_update(MonthLock)
                .where(MonthLock.locked_by_user_id == user.id)
                .values(locked_by_user_id=None)
            )
            db.execute(
                sql_update(MonthLock)
                .where(MonthLock.updated_by_user_id == user.id)
                .values(updated_by_user_id=None)
            )
            db.delete(user)
        db.delete(employee)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=(
                "Stored images, backups, and reports were purged, but database "
                "deletion failed. Retry the permanent delete."
            ),
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin_roles(*SUPER_ADMIN))],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={
            "sub_admin_roles": SUB_ADMIN_ROLES,
            "user": admin,
            "active_page": "users",
        },
    )


@router.get("/api/admin/users", response_model=list[AdminUserResponse])
def list_admin_users(
    _admin: Annotated[User, Depends(require_admin_roles(*SUPER_ADMIN))],
    db: Annotated[Session, Depends(get_db)],
) -> list[AdminUserResponse]:
    users = db.scalars(
        select(User)
        .where(
            User.role == "admin",
            or_(User.admin_role.is_(None), User.admin_role != "super_admin"),
        )
        .order_by(User.username)
    ).all()
    return [
        AdminUserResponse(
            id=user.id,
            username=user.username,
            admin_role=user.admin_role or "standard",
            is_active=user.is_active,
        )
        for user in users
    ]


@router.post(
    "/api/admin/users",
    response_model=AdminUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_admin_user(
    payload: AdminUserCreateRequest,
    _admin: Annotated[User, Depends(require_admin_roles(*SUPER_ADMIN))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserResponse:
    user = User(
        username=payload.username.strip(),
        password_hash=hash_password(payload.password),
        role="admin",
        admin_role=payload.admin_role,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already exists.") from error
    db.refresh(user)
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        admin_role=user.admin_role,
        is_active=user.is_active,
    )


@router.patch("/api/admin/users/{user_id}", response_model=AdminUserResponse)
def update_admin_user(
    user_id: int,
    payload: AdminUserUpdateRequest,
    actor: Annotated[User, Depends(require_admin_roles(*SUPER_ADMIN))],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserResponse:
    user = db.get(User, user_id)
    if user is None or user.role != "admin":
        raise HTTPException(status_code=404, detail="Sub-admin not found.")
    if user.admin_role == "super_admin" or user.id == actor.id:
        raise HTTPException(
            status_code=403,
            detail="The super-admin account cannot be changed through sub-admin management.",
        )
    user.admin_role = payload.admin_role
    user.is_active = payload.is_active
    db.commit()
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        admin_role=user.admin_role,
        is_active=user.is_active,
    )


@router.get("/api/admin/attendance", response_model=list[AttendanceRecordResponse])
def list_attendance(
    _admin: Annotated[User, Depends(require_admin_roles(*ATTENDANCE_VIEWERS))],
    db: Annotated[Session, Depends(get_db)],
    day: Annotated[date | None, Query()] = None,
) -> list[AttendanceRecordResponse]:
    attendance_date = day or datetime.now(UTC).date()
    rows = db.execute(
        select(AttendanceDaily, Employee)
        .join(Employee, Employee.id == AttendanceDaily.employee_id)
        .where(AttendanceDaily.date == attendance_date)
        .order_by(Employee.employee_code)
    ).all()
    return [_attendance_response(record, employee) for record, employee in rows]


@router.get("/admin/attendance/{attendance_id}/edit", response_class=HTMLResponse)
def attendance_edit_page(
    attendance_id: int,
    request: Request,
    actor: Annotated[User, Depends(require_admin_roles(*ATTENDANCE_EDITORS))],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    record = db.get(AttendanceDaily, attendance_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found.")
    employees = db.scalars(
        select(Employee).order_by(Employee.employee_code)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="attendance_edit.html",
        context={
            "record": record,
            "employees": employees,
            "user": actor,
            "active_page": "attendance",
            "statuses": sorted(ATTENDANCE_STATUSES),
            "locked": is_month_locked(
                db, year=record.date.year, month=record.date.month
            ),
            "can_edit_locked": (actor.admin_role or "standard") in {"super_admin", "hr"},
        },
    )


@router.put(
    "/api/admin/attendance/{attendance_id}",
    response_model=AttendanceRecordResponse,
)
def edit_attendance_record(
    attendance_id: int,
    payload: AttendanceEditRequest,
    actor: Annotated[User, Depends(require_admin_roles(*ATTENDANCE_EDITORS))],
    db: Annotated[Session, Depends(get_db)],
) -> AttendanceRecordResponse:
    try:
        record, _audit = edit_attendance(
            db,
            attendance_id=attendance_id,
            employee_id=payload.employee_id,
            attendance_date=payload.date,
            first_login_at=payload.first_login_at,
            final_logout_at=payload.final_logout_at,
            status=payload.status,
            reason=payload.reason,
            actor=actor,
        )
    except AttendanceEditError as error:
        _raise_edit_error(error)
    employee = db.get(Employee, record.employee_id)
    if employee is None:
        raise HTTPException(status_code=500, detail="Edited employee record is unavailable.")
    return _attendance_response(record, employee)


@router.get(
    "/api/admin/attendance/{attendance_id}/audit",
    response_model=list[dict[str, object]],
)
def attendance_audit_history(
    attendance_id: int,
    _admin: Annotated[User, Depends(require_admin_roles(*AUDIT_VIEWERS))],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, object]]:
    if db.get(AttendanceDaily, attendance_id) is None:
        raise HTTPException(status_code=404, detail="Attendance record not found.")
    audits = db.scalars(
        select(AttendanceAudit)
        .where(AttendanceAudit.attendance_id == attendance_id)
        .order_by(AttendanceAudit.edited_at.desc())
    ).all()
    return [
        {
            "actor_user_id": audit.actor_user_id,
            "edited_at": audit.edited_at,
            "old_values": audit.old_values,
            "new_values": audit.new_values,
            "reason": audit.reason,
        }
        for audit in audits
    ]


@router.get("/admin/holidays", response_class=HTMLResponse)
def holidays_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin_roles(*HOLIDAY_MANAGERS))],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="holidays.html",
        context={"user": admin, "active_page": "holidays"},
    )


@router.get("/api/admin/holidays")
def list_holidays(
    _admin: Annotated[User, Depends(require_admin_roles(*ATTENDANCE_VIEWERS))],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, object]]:
    records = db.scalars(select(Holiday).order_by(Holiday.date)).all()
    return [{"date": record.date, "name": record.name} for record in records]


@router.get("/admin/month-locks", response_class=HTMLResponse)
def month_locks_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin_roles(*SUPER_ADMIN))],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="month_locks.html",
        context={"user": admin, "active_page": "month_locks"},
    )


@router.get("/api/admin/month-locks", response_model=list[MonthLockResponse])
def list_month_locks(
    _admin: Annotated[User, Depends(require_admin_roles(*SUPER_ADMIN))],
    db: Annotated[Session, Depends(get_db)],
) -> list[MonthLockResponse]:
    locks = db.scalars(
        select(MonthLock).order_by(MonthLock.year.desc(), MonthLock.month.desc())
    ).all()
    return [_month_lock_response(record) for record in locks]


@router.put("/api/admin/month-locks", response_model=MonthLockResponse)
def set_month_lock(
    payload: MonthLockRequest,
    actor: Annotated[User, Depends(require_admin_roles(*SUPER_ADMIN))],
    db: Annotated[Session, Depends(get_db)],
) -> MonthLockResponse:
    try:
        record = update_month_lock(
            db,
            year=payload.year,
            month=payload.month,
            locked=payload.locked,
            actor=actor,
        )
    except AttendanceEditError as error:
        _raise_edit_error(error)
    return _month_lock_response(record)


@router.get("/admin/reports", response_class=HTMLResponse)
def reports_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin_roles(*REPORT_VIEWERS))],
) -> HTMLResponse:
    now = datetime.now(UTC)
    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "year": now.year,
            "month": now.month,
            "user": admin,
            "active_page": "reports",
        },
    )


@router.get("/api/admin/reports/monthly.xlsx")
def monthly_report(
    _admin: Annotated[User, Depends(require_admin_roles(*REPORT_VIEWERS))],
    db: Annotated[Session, Depends(get_db)],
    year: Annotated[int, Query(ge=1900, le=9999)],
    month: Annotated[int, Query(ge=1, le=12)],
) -> StreamingResponse:
    try:
        workbook = build_monthly_report(db, year=year, month=month)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    filename = f"attendance-{year:04d}-{month:02d}.xlsx"
    return StreamingResponse(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


