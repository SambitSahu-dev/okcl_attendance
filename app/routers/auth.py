from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services.auth import authenticate_user

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1)


class AuthenticatedUser(BaseModel):
    id: int
    username: str
    role: str
    admin_role: str | None = None
    employee_id: int | None

    model_config = ConfigDict(from_attributes=True)


def get_current_user(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> User:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
    return user


def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def require_admin_roles(*allowed_roles: str):
    def dependency(
        user: Annotated[User, Depends(require_admin)],
    ) -> User:
        admin_role = user.admin_role or "standard"
        if admin_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient admin permissions",
            )
        return user

    return dependency


def require_employee(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if user.role != "employee":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Employee access required"
        )
    return user


@router.post("/login", response_model=AuthenticatedUser)
def login(
    credentials: LoginRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    user = authenticate_user(db, credentials.username, credentials.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    request.session.clear()
    request.session["user_id"] = user.id
    return user


@router.post("/logout")
def logout(request: Request) -> dict[str, str]:
    request.session.clear()
    return {"message": "Logged out"}


@router.get("/me", response_model=AuthenticatedUser)
def get_me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user
