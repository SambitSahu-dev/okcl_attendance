from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.db.base import Base
from app.db.session import get_db
from app.models import Employee, Kiosk
from app.routers.attendance import router as attendance_router
from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router
from app.routers.enrollment import router as enrollment_router
from app.routers.kiosk import router as kiosk_router
from app.routers.management import router as management_router


@pytest.fixture
def db_engine():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(test_engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def db_session(db_engine) -> Generator[Session, None, None]:
    with Session(db_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def employee(db_session: Session) -> Employee:
    record = Employee(employee_code="EMP-001", full_name="Test Employee")
    db_session.add(record)
    db_session.commit()
    return record


@pytest.fixture
def kiosk(db_session: Session) -> Kiosk:
    record = Kiosk(
        kiosk_id="test-kiosk",
        name="Test Kiosk",
        office="Test Office",
        latitude=0,
        longitude=0,
    )
    db_session.add(record)
    db_session.commit()
    return record


@pytest.fixture
def server_clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    clock = [datetime(2026, 11, 24, 8, 15, 30, tzinfo=UTC)]
    monkeypatch.setattr("app.services.attendance.utc_now", lambda: clock[0])
    return clock


@pytest.fixture
def api_client(db_engine) -> Generator[TestClient, None, None]:
    application = FastAPI()
    application.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    application.include_router(auth_router)
    application.include_router(attendance_router)
    application.include_router(enrollment_router)
    application.include_router(kiosk_router)
    application.include_router(dashboard_router)
    application.include_router(management_router)

    def override_get_db() -> Generator[Session, None, None]:
        with Session(db_engine, expire_on_commit=False) as session:
            yield session

    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as client:
        yield client
