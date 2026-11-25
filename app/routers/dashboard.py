from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.models import AttendanceDaily, Employee, User
from app.routers.auth import get_current_user, require_admin_roles, require_employee
from app.services.dashboard import dashboard_data

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))

ADMIN_DASHBOARD_ROLES = (
    "standard",
    "super_admin",
    "hr",
    "accounts",
    "team_lead",
    "audit",
)


def _public_dashboard_summary(db: Session) -> dict[str, object]:
    data = dashboard_data(db, today=datetime.now(UTC).date())
    return {
        "date": data["date"],
        "inside_count": data["inside_count"],
        "outside_count": data["outside_count"],
        "absent_count": data["absent_count"],
    }


@router.get("/", include_in_schema=False)
def home_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    response = templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "data": _public_dashboard_summary(db),
            "is_authenticated": bool(request.session.get("user_id")),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/public/dashboard")
def public_dashboard_summary(
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    data = _public_dashboard_summary(db)
    return JSONResponse(
        content={
            "date": data["date"].isoformat(),
            "inside_count": data["inside_count"],
            "outside_count": data["outside_count"],
            "absent_count": data["absent_count"],
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    if user.role == "employee":
        employee = db.get(Employee, user.employee_id)
        records = db.scalars(
            select(AttendanceDaily)
            .where(AttendanceDaily.employee_id == user.employee_id)
            .order_by(AttendanceDaily.date.desc())
            .limit(90)
        ).all()
        return templates.TemplateResponse(
            request=request,
            name="employee_dashboard.html",
            context={"employee": employee, "records": records, "user": user},
        )

    data = dashboard_data(
        db,
        today=datetime.now(UTC).date(),
        admin_contact_email=(
            get_settings().admin_contact_email
            or get_settings().super_admin_username
        ),
    )
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={"data": data, "user": user},
    )


@router.get("/admin/attendance", response_class=HTMLResponse)
def admin_attendance_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin_roles(*ADMIN_DASHBOARD_ROLES))],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="admin_attendance.html",
        context={
            "today": datetime.now(UTC).date().isoformat(),
            "admin_role": admin.admin_role or "standard",
            "user": admin,
            "active_page": "attendance",
        },
    )


@router.get("/employee/profile", response_class=HTMLResponse)
def employee_profile_page(
    request: Request,
    employee_user: Annotated[User, Depends(require_employee)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    employee = db.get(Employee, employee_user.employee_id)
    records = db.scalars(
        select(AttendanceDaily)
        .where(AttendanceDaily.employee_id == employee_user.employee_id)
        .order_by(AttendanceDaily.date.desc())
        .limit(90)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="employee_dashboard.html",
        context={"employee": employee, "records": records, "user": employee_user},
    )


@router.get("/api/admin/dashboard")
def admin_dashboard_data(
    _admin: Annotated[User, Depends(require_admin_roles(*ADMIN_DASHBOARD_ROLES))],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    result = dashboard_data(db)
    rows = [
        {
            **row,
            "first_login_at": (
                row["first_login_at"].isoformat()
                if row["first_login_at"] is not None
                else None
            ),
            "final_logout_at": (
                row["final_logout_at"].isoformat()
                if row["final_logout_at"] is not None
                else None
            ),
        }
        for row in result["table_rows"]
    ]
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "inside_count": result["inside_count"],
        "outside_count": result["outside_count"],
        "absent_count": result["absent_count"],
        "table_rows": rows,
        "admin_contact_email": result["admin_contact_email"],
    }
