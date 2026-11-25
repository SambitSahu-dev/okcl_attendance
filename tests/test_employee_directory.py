from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AttendanceAudit,
    AttendanceDaily,
    Consent,
    Employee,
    FaceEmbedding,
    LogoutLog,
    ScanAttempt,
    User,
)
from app.routers.management import get_backup_directory, get_report_directory
from app.services.auth import hash_password
from app.services.login_images import FileSystemLoginImageStore
from app.services.vision_runtime import get_login_image_store


def test_delete_deactivates_employee_erases_face_data_and_preserves_history(
    api_client, db_session: Session, employee, tmp_path
) -> None:
    admin = User(
        username="standard-admin",
        password_hash=hash_password("test-password"),
        role="admin",
        admin_role="standard",
    )
    employee_user = User(
        username="employee-login",
        password_hash=hash_password("employee-password"),
        role="employee",
        employee_id=employee.id,
    )
    db_session.add_all([admin, employee_user])
    db_session.flush()

    record = AttendanceDaily(
        employee_id=employee.id,
        date=date(2026, 11, 25),
        first_login_at=datetime(2026, 11, 25, 8, 0, tzinfo=UTC),
        status="Present",
        image_path=f"storage/images/{employee.id}/2026-11-25.jpg",
        image_sha256="a" * 64,
    )
    db_session.add(record)
    db_session.flush()
    db_session.add_all(
        [
            Consent(
                employee_id=employee.id,
                consent_text_version="1.0",
                granted=True,
            ),
            FaceEmbedding(
                employee_id=employee.id,
                embedding_data="encrypted-template",
            ),
            LogoutLog(
                employee_id=employee.id,
                attendance_id=record.id,
                date=record.date,
                logout_at=datetime(2026, 11, 25, 17, 0, tzinfo=UTC),
                accepted=True,
            ),
            AttendanceAudit(
                attendance_id=record.id,
                actor_user_id=admin.id,
                old_values='{"status":"Incomplete"}',
                new_values='{"status":"Present"}',
                reason="Test correction",
            ),
        ]
    )
    db_session.commit()

    image_store = FileSystemLoginImageStore(tmp_path)
    image_file = tmp_path / "storage" / "images" / str(employee.id) / "2026-11-25.jpg"
    image_file.parent.mkdir(parents=True)
    image_file.write_bytes(b"login-image")
    api_client.app.dependency_overrides[get_login_image_store] = lambda: image_store
    assert api_client.post(
        "/auth/login",
        json={"username": admin.username, "password": "test-password"},
    ).status_code == 200

    response = api_client.delete(f"/api/admin/employees/{employee.id}")

    assert response.status_code == 204
    db_session.refresh(employee)
    db_session.refresh(employee_user)
    db_session.refresh(record)
    assert employee.is_active is False
    assert employee_user.is_active is False
    assert not image_file.exists()
    assert record.image_path is None
    assert record.image_sha256 == "a" * 64
    assert db_session.scalars(select(FaceEmbedding)).all() == []
    assert len(db_session.scalars(select(Consent)).all()) == 1
    assert len(db_session.scalars(select(LogoutLog)).all()) == 1
    assert len(db_session.scalars(select(AttendanceAudit)).all()) == 1


def test_employee_list_marks_deleted_profiles(api_client, db_session, employee) -> None:
    admin = User(
        username="directory-admin",
        password_hash=hash_password("test-password"),
        role="admin",
        admin_role="standard",
    )
    db_session.add(admin)
    db_session.commit()
    assert api_client.post(
        "/auth/login",
        json={"username": admin.username, "password": "test-password"},
    ).status_code == 200

    active_response = api_client.get("/api/admin/employees")
    assert active_response.status_code == 200
    assert active_response.json()[0]["is_deleted"] is False

    assert api_client.delete(f"/api/admin/employees/{employee.id}").status_code == 204

    response = api_client.get("/api/admin/employees")

    assert response.status_code == 200
    assert response.json()[0]["is_deleted"] is True
    assert response.json()[0]["is_active"] is False
    update_response = api_client.patch(
        f"/api/admin/employees/{employee.id}",
        json={
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
            "email": employee.email,
            "is_active": True,
        },
    )
    assert update_response.status_code == 409


def test_permanent_delete_purges_deleted_employee_and_linked_data(
    api_client, db_session: Session, employee, tmp_path
) -> None:
    employee_id = employee.id
    admin = User(
        username="purge-admin",
        password_hash=hash_password("test-password"),
        role="admin",
        admin_role="standard",
    )
    employee_user = User(
        username="purge-employee",
        password_hash=hash_password("employee-password"),
        role="employee",
        employee_id=employee.id,
    )
    db_session.add_all([admin, employee_user])
    db_session.flush()
    employee_user_id = employee_user.id
    record = AttendanceDaily(
        employee_id=employee.id,
        date=date(2026, 11, 25),
        first_login_at=datetime(2026, 11, 25, 8, 0, tzinfo=UTC),
        status="Present",
        image_path=f"storage/images/{employee.id}/2026-11-25.jpg",
        image_sha256="b" * 64,
    )
    db_session.add(record)
    db_session.flush()
    db_session.add_all(
        [
            Consent(
                employee_id=employee.id,
                consent_text_version="1.0",
                granted=True,
            ),
            FaceEmbedding(
                employee_id=employee.id,
                embedding_data="encrypted-template",
            ),
            LogoutLog(
                employee_id=employee.id,
                attendance_id=record.id,
                date=record.date,
                logout_at=datetime(2026, 11, 25, 17, 0, tzinfo=UTC),
                accepted=True,
            ),
            AttendanceAudit(
                attendance_id=record.id,
                actor_user_id=admin.id,
                old_values='{"status":"Incomplete"}',
                new_values='{"status":"Present"}',
                reason="Test correction",
            ),
            ScanAttempt(
                employee_id=employee.id,
                scan_type="login",
                reason="Rejected test scan",
            ),
        ]
    )
    db_session.commit()

    image_store = FileSystemLoginImageStore(tmp_path)
    image_file = tmp_path / "storage" / "images" / str(employee.id) / "2026-11-25.jpg"
    image_file.parent.mkdir(parents=True)
    image_file.write_bytes(b"login-image")
    api_client.app.dependency_overrides[get_login_image_store] = lambda: image_store
    api_client.app.dependency_overrides[get_backup_directory] = (
        lambda: tmp_path / "backups"
    )
    api_client.app.dependency_overrides[get_report_directory] = (
        lambda: tmp_path / "reports"
    )
    assert api_client.post(
        "/auth/login",
        json={"username": admin.username, "password": "test-password"},
    ).status_code == 200

    not_deleted = api_client.delete(
        f"/api/admin/employees/{employee_id}/permanent"
    )
    assert not_deleted.status_code == 409
    assert api_client.delete(f"/api/admin/employees/{employee_id}").status_code == 204
    response = api_client.delete(
        f"/api/admin/employees/{employee_id}/permanent"
    )

    assert response.status_code == 204
    db_session.expire_all()
    assert (
        db_session.scalar(select(Employee.id).where(Employee.id == employee_id))
        is None
    )
    assert (
        db_session.scalar(select(User.id).where(User.id == employee_user_id)) is None
    )
    assert db_session.scalars(select(AttendanceDaily)).all() == []
    assert db_session.scalars(select(AttendanceAudit)).all() == []
    assert db_session.scalars(select(Consent)).all() == []
    assert db_session.scalars(select(FaceEmbedding)).all() == []
    assert db_session.scalars(select(LogoutLog)).all() == []
    assert db_session.scalars(select(ScanAttempt)).all() == []
    assert not image_file.exists()
    assert not image_file.parent.exists()
