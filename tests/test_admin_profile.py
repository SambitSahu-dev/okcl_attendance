import re
from datetime import date
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models import AttendanceDaily, Setting, User
from app.services.auth import hash_password, verify_password
from scripts.seed import _bootstrap_user


def _create_admin(
    db: Session,
    *,
    username: str,
    admin_role: str = "super_admin",
    email: str | None = None,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password("current-password"),
        role="admin",
        admin_role=admin_role,
        email=email,
    )
    db.add(user)
    db.commit()
    return user


def _login(client, username: str = "root") -> None:
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "current-password"},
    )
    assert response.status_code == 200


def test_admin_profile_updates_identity_and_password(api_client, db_session: Session) -> None:
    admin = _create_admin(db_session, username="root")
    _login(api_client)

    response = api_client.put(
        "/api/admin/profile",
        json={
            "username": "root-renamed",
            "display_name": "Root Admin",
            "email": "root@example.com",
            "current_password": "current-password",
            "new_password": "new-password-123",
            "confirm_new_password": "new-password-123",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": admin.id,
        "username": "root-renamed",
        "display_name": "Root Admin",
        "email": "root@example.com",
        "admin_role": "super_admin",
    }
    db_session.refresh(admin)
    assert verify_password("new-password-123", admin.password_hash)
    assert api_client.get("/admin/profile").status_code == 200
    assert api_client.post(
        "/auth/login",
        json={"username": "root-renamed", "password": "new-password-123"},
    ).status_code == 200


def test_profile_rename_keeps_bootstrap_seed_account_linked(
    api_client, db_session: Session, monkeypatch
) -> None:
    admin = _create_admin(db_session, username="root")
    _login(api_client)
    monkeypatch.setattr(
        "app.routers.management.get_settings",
        lambda: SimpleNamespace(admin_username="root", super_admin_username=None),
    )

    response = api_client.put(
        "/api/admin/profile",
        json={
            "username": "root-renamed",
            "current_password": "current-password",
        },
    )

    assert response.status_code == 200
    marker = db_session.get(Setting, "bootstrap_admin_user_id")
    assert marker is not None
    assert marker.value == str(admin.id)
    assert _bootstrap_user(db_session, "bootstrap_admin_user_id", "root") is admin


def test_admin_profile_requires_current_password(api_client, db_session: Session) -> None:
    _create_admin(db_session, username="root")
    _login(api_client)

    response = api_client.put(
        "/api/admin/profile",
        json={
            "username": "root",
            "display_name": "Changed",
            "email": None,
            "current_password": "wrong-password",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Current password is incorrect."


def test_admin_profile_rejects_duplicate_username_or_email(
    api_client, db_session: Session
) -> None:
    _create_admin(db_session, username="root")
    _create_admin(db_session, username="existing", email="used@example.com")
    _login(api_client)

    for fields in (
        {"username": "existing", "email": None},
        {"username": "root", "email": "used@example.com"},
    ):
        response = api_client.put(
            "/api/admin/profile",
            json={
                **fields,
                "display_name": None,
                "current_password": "current-password",
            },
        )
        assert response.status_code == 409


def test_admin_profile_is_admin_only(api_client, employee, db_session: Session) -> None:
    user = User(
        username="employee",
        password_hash=hash_password("current-password"),
        role="employee",
        employee_id=employee.id,
    )
    db_session.add(user)
    db_session.commit()
    _login(api_client, username="employee")

    assert api_client.get("/admin/profile").status_code == 403
    assert api_client.put(
        "/api/admin/profile",
        json={
            "username": "employee",
            "current_password": "current-password",
        },
    ).status_code == 403


def test_shared_admin_navigation_has_management_links_without_gate_menu(
    api_client, db_session: Session, employee
) -> None:
    _create_admin(db_session, username="root")
    attendance = AttendanceDaily(
        employee_id=employee.id,
        date=date(2026, 11, 24),
        status="Absent",
    )
    db_session.add(attendance)
    db_session.commit()
    _login(api_client)

    paths = (
        "/dashboard",
        "/admin/attendance",
        "/admin/employees",
        "/admin/enrollment",
        "/admin/holidays",
        "/admin/settings",
        "/admin/reports",
        "/admin/month-locks",
        "/admin/users",
        "/admin/profile",
    )
    expected_links = {
        "/dashboard",
        "/admin/attendance",
        "/admin/employees",
        "/admin/enrollment",
        "/admin/holidays",
        "/admin/settings",
        "/admin/reports",
        "/admin/month-locks",
        "/admin/users",
        "/admin/profile",
        "/auth/logout",
    }
    for path in paths:
        response = api_client.get(path)
        assert response.status_code == 200, path
        nav = re.search(
            r'<nav aria-label="Admin navigation">(.*?)</nav>',
            response.text,
            re.DOTALL,
        )
        assert nav is not None, path
        links = set(re.findall(r'href="([^"]+)"', nav.group(1)))
        assert links == expected_links, path
        assert "/attendance" not in links, path


def test_role_restricted_navigation_stays_role_aware(api_client, db_session: Session) -> None:
    _create_admin(db_session, username="hr-user", admin_role="hr")
    _login(api_client, username="hr-user")

    response = api_client.get("/dashboard")
    nav = re.search(
        r'<nav aria-label="Admin navigation">(.*?)</nav>',
        response.text,
        re.DOTALL,
    )

    assert nav is not None
    assert 'href="/admin/employees"' in nav.group(1)
    assert 'href="/admin/users"' not in nav.group(1)
    assert 'href="/admin/month-locks"' not in nav.group(1)
