from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Employee, FaceEmbedding, User
from app.routers.auth import require_admin_roles
from app.services.biometrics import FaceEmbeddingCipher, FaceEmbeddingEncryptionError
from app.services.face_enrollment import EnrollmentError, enroll_employee_faces
from app.services.vision_runtime import (
    get_face_detector,
    get_face_embedder,
    get_optional_face_embedding_cipher,
)
from vision.interfaces import FaceDetector, FaceEmbedder

router = APIRouter(tags=["employee enrollment"])
templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))
ENROLLMENT_ADMIN_ROLES = ("standard", "super_admin", "hr")


class EnrollmentRequest(BaseModel):
    employee_id: int = Field(gt=0)
    consent_granted: bool
    captures: list[Annotated[str, Field(max_length=2_800_000)]] = Field(max_length=10)


class EnrollmentResponse(BaseModel):
    employee_id: int
    captures_processed: int
    consented_at: str
    consent_text_version: str


@router.get("/admin/enrollment", response_class=HTMLResponse)
def enrollment_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin_roles(*ENROLLMENT_ADMIN_ROLES))],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    employees = db.scalars(
        select(Employee)
        .where(Employee.is_active.is_(True))
        .order_by(Employee.employee_code)
    ).all()
    enrolled_ids = set(db.scalars(select(FaceEmbedding.employee_id).distinct()).all())
    available_employees = [
        {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
        }
        for employee in employees
        if employee.id not in enrolled_ids
    ]
    return templates.TemplateResponse(
        request=request,
        name="enrollment.html",
        context={
            "employees": available_employees,
            "user": admin,
            "active_page": "enrollment",
        },
    )


@router.post(
    "/admin/enrollment",
    response_model=EnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def enroll(
    enrollment: EnrollmentRequest,
    _admin: Annotated[User, Depends(require_admin_roles(*ENROLLMENT_ADMIN_ROLES))],
    db: Annotated[Session, Depends(get_db)],
    detector: Annotated[FaceDetector, Depends(get_face_detector)],
    embedder: Annotated[FaceEmbedder, Depends(get_face_embedder)],
    cipher: Annotated[
        FaceEmbeddingCipher | None,
        Depends(get_optional_face_embedding_cipher),
    ],
) -> EnrollmentResponse:
    if enrollment.consent_granted and cipher is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Set FACE_EMBEDDING_KEY before enrolling employees.",
        )
    try:
        consent, count = enroll_employee_faces(
            db,
            employee_id=enrollment.employee_id,
            consent_granted=enrollment.consent_granted,
            captures=enrollment.captures,
            detector=detector,
            embedder=embedder,
            cipher=cipher,
        )
    except EnrollmentError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.message,
        ) from error
    except FaceEmbeddingEncryptionError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return EnrollmentResponse(
        employee_id=enrollment.employee_id,
        captures_processed=count,
        consented_at=consent.consented_at.isoformat(),
        consent_text_version=consent.consent_text_version,
    )
