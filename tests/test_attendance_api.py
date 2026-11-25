from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.models import AttendanceDaily, Employee, FaceEmbedding, User
from app.services.auth import hash_password
from app.services.scan_rate_limit import ScanRateLimiter
from app.services.vision_runtime import get_kiosk_scan_coordinator


def _create_user(
    db_session: Session,
    *,
    username: str,
    role: str,
    employee_id=None,
    admin_role: str | None = None,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password("test-password"),
        role=role,
        employee_id=employee_id,
        admin_role=admin_role,
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_public_home_shows_only_live_attendance_totals_without_login(
    api_client, db_session: Session, employee, monkeypatch
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 24, 12, 0, tzinfo=tz)

    monkeypatch.setattr("app.routers.dashboard.datetime", FixedDateTime)
    today = date(2026, 11, 24)
    outside = Employee(employee_code="EMP-002", full_name="Outside Employee")
    absent = Employee(employee_code="EMP-003", full_name="Absent Employee")
    db_session.add_all([outside, absent])
    db_session.flush()
    db_session.add_all(
        [
            AttendanceDaily(
                employee_id=employee.id,
                date=today,
                first_login_at=datetime(2026, 11, 24, 8, 0, tzinfo=UTC),
                status="Incomplete",
            ),
            AttendanceDaily(
                employee_id=outside.id,
                date=today,
                first_login_at=datetime(2026, 11, 24, 8, 5, tzinfo=UTC),
                final_logout_at=datetime(2026, 11, 24, 16, 0, tzinfo=UTC),
                status="Present",
            ),
        ]
    )
    db_session.commit()

    page = api_client.get("/")
    assert page.status_code == 200
    assert "Live overview" in page.text
    assert "Attendance today" in page.text
    assert 'id="inside-count">1<' in page.text
    assert 'id="outside-count">1<' in page.text
    assert 'id="absent-count">1<' in page.text
    assert "Test Employee" not in page.text
    assert "Outside Employee" not in page.text
    assert "Absent Employee" not in page.text

    summary = api_client.get("/api/public/dashboard")
    assert summary.status_code == 200
    assert summary.headers["cache-control"] == "no-store"
    assert summary.json() == {
        "date": "2026-11-24",
        "inside_count": 1,
        "outside_count": 1,
        "absent_count": 1,
    }
    assert api_client.get("/dashboard").status_code == 401

    admin = _create_user(
        db_session, username="admin", role="admin", admin_role="super_admin"
    )
    assert api_client.post(
        "/auth/login",
        json={"username": admin.username, "password": "test-password"},
    ).status_code == 200
    authenticated_home = api_client.get("/", follow_redirects=False)
    assert authenticated_home.status_code == 200
    assert "Open your dashboard" in authenticated_home.text
    assert "Test Employee" not in authenticated_home.text
    assert api_client.get("/dashboard").status_code == 200


def test_unverified_employee_id_attendance_endpoints_are_removed(api_client) -> None:
    assert api_client.post("/attendance/login", json={"employee_id": 1}).status_code == 404
    assert api_client.post("/attendance/logout", json={"employee_id": 1}).status_code == 404


def test_scan_start_rejects_client_supplied_identity(api_client) -> None:
    api_client.app.dependency_overrides[get_kiosk_scan_coordinator] = lambda: object()

    response = api_client.post(
        "/attendance/scans/start",
        json={"employee_id": 1},
    )

    assert response.status_code == 422
    manual_operation = api_client.post(
        "/attendance/scans/start",
        json={"operation": "login"},
    )
    assert manual_operation.status_code == 422


def test_gate_page_starts_closed_and_contains_live_activity_panel(api_client) -> None:
    page = api_client.get("/attendance")

    assert page.status_code == 200
    assert "<title>GATE · ATB</title>" in page.text
    assert 'id="open-gate"' in page.text
    assert 'id="gate-workspace"' in page.text
    assert 'id="gate-workspace" class="gate-workspace" aria-label="GATE live session" hidden' in page.text
    assert "CLOSE GATE" in page.text
    assert 'id="login"' not in page.text
    assert 'id="logout"' not in page.text
    assert "recognizes your face, blink, and hand gesture together" in page.text
    assert 'id="activity-list"' in page.text
    assert "Recent attendance" in page.text
    camera_position = page.text.index('<video id="camera"')
    assert page.text.index('id="employee-greeting"') < camera_position
    assert page.text.index('id="prompt"') < camera_position
    assert page.text.index('id="result"') < camera_position


def test_scan_start_rate_limit_returns_retry_after(api_client) -> None:
    class FakeCoordinator:
        calls = 0

        def start_scan(self, _db, *, kiosk_id):
            self.calls += 1
            assert kiosk_id
            return "s" * 24, "Blink once"

    coordinator = FakeCoordinator()
    api_client.app.state.scan_rate_limiter = ScanRateLimiter(
        start_limit=1,
        frame_limit=400,
    )
    api_client.app.dependency_overrides[get_kiosk_scan_coordinator] = lambda: coordinator

    first = api_client.post(
        "/attendance/scans/start",
        json={},
    )
    limited = api_client.post(
        "/attendance/scans/start",
        json={},
    )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["retry-after"].isdigit()
    assert coordinator.calls == 1


def test_admin_can_configure_late_cutoff_and_weekly_offs(
    api_client, db_session: Session, employee, kiosk
) -> None:
    admin = _create_user(db_session, username="admin", role="admin")
    assert api_client.get("/attendance/admin/settings").status_code == 401
    assert api_client.post(
        "/auth/login",
        json={"username": admin.username, "password": "test-password"},
    ).status_code == 200
    assert api_client.get("/admin/settings").status_code == 200

    configured = api_client.put(
        "/attendance/admin/settings/late-cutoff",
        json={"cutoff_time": "08:45"},
    )
    assert configured.status_code == 200
    assert configured.json()["late_cutoff_time"] == "08:45"
    assert "Late cutoff for completed attendance days (UTC)" in api_client.get(
        "/admin/settings"
    ).text
    weekly_offs = api_client.put(
        "/attendance/admin/settings/weekly-offs",
        json={"weekdays": [0]},
    )
    assert weekly_offs.status_code == 200
    assert weekly_offs.json()["weekly_off_days"] == [0]


def test_admin_can_configure_face_match_threshold(api_client, db_session: Session) -> None:
    admin = _create_user(db_session, username="admin", role="admin")
    api_client.post(
        "/auth/login",
        json={"username": admin.username, "password": "test-password"},
    )

    updated = api_client.put(
        "/attendance/admin/settings/face-match-threshold",
        json={"threshold": 0.72},
    )
    assert updated.status_code == 200
    assert updated.json()["threshold"] == 0.72
    assert api_client.get(
        "/attendance/admin/settings/face-match-threshold"
    ).json()["threshold"] == 0.72
    assert api_client.put(
        "/attendance/admin/settings/face-match-threshold",
        json={"threshold": 1.1},
    ).status_code == 422


def test_employee_cannot_change_attendance_settings(
    api_client, db_session: Session, employee
) -> None:
    user = _create_user(
        db_session,
        username="employee",
        role="employee",
        employee_id=employee.id,
    )
    api_client.post(
        "/auth/login",
        json={"username": user.username, "password": "test-password"},
    )

    response = api_client.put(
        "/attendance/admin/settings/late-cutoff",
        json={"cutoff_time": "08:45"},
    )
    assert response.status_code == 403


def test_admin_enrollment_page_only_lists_employees_without_embeddings(
    api_client, db_session: Session, employee
) -> None:
    admin = _create_user(
        db_session, username="admin", role="admin", admin_role="super_admin"
    )
    pending = Employee(employee_code="EMP-002", full_name="Pending Employee")
    db_session.add(pending)
    db_session.add(
        FaceEmbedding(
            employee_id=employee.id,
            embedding_data="encrypted",
            model_version="test",
        )
    )
    db_session.commit()

    assert api_client.get("/admin/enrollment").status_code == 401
    api_client.post(
        "/auth/login",
        json={"username": admin.username, "password": "test-password"},
    )
    page = api_client.get("/admin/enrollment")

    assert page.status_code == 200
    assert "EMP-002" in page.text
    assert "EMP-001" not in page.text
    employees = api_client.get("/api/admin/employees")
    assert employees.status_code == 200
    assert {
        row["employee_code"]: row["face_enrolled"] for row in employees.json()
    } == {"EMP-001": True, "EMP-002": False}


def test_admin_can_add_and_remove_holiday(
    api_client, db_session: Session
) -> None:
    admin = _create_user(db_session, username="admin", role="admin")
    api_client.post(
        "/auth/login",
        json={"username": admin.username, "password": "test-password"},
    )

    created = api_client.post(
        "/attendance/admin/holidays",
        json={"date": "2026-11-24", "name": "Office Day Off"},
    )
    assert created.status_code == 201
    assert created.json() == {"date": "2026-11-24", "name": "Office Day Off"}

    duplicate = api_client.post(
        "/attendance/admin/holidays",
        json={"date": "2026-11-24", "name": "Duplicate"},
    )
    assert duplicate.status_code == 409

    deleted = api_client.delete("/attendance/admin/holidays/2026-11-24")
    assert deleted.status_code == 204
    missing = api_client.delete("/attendance/admin/holidays/2026-11-24")
    assert missing.status_code == 404


def test_employee_and_subadmin_management_permissions(
    api_client, db_session: Session
) -> None:
    standard = _create_user(
        db_session, username="standard", role="admin", admin_role="standard"
    )
    api_client.post(
        "/auth/login",
        json={"username": standard.username, "password": "test-password"},
    )
    create_payload = {
        "employee_code": "EMP-200",
        "full_name": "Created Employee",
        "email": "created@example.org",
        "username": "created.employee",
        "password": "pass1234",
    }
    standard_created = api_client.post("/api/admin/employees", json=create_payload)
    assert standard_created.status_code == 201
    short_password_payload = {
        **create_payload,
        "employee_code": "EMP-202",
        "email": "short-password@example.org",
        "username": "short.password",
        "password": "pass123",
    }
    assert (
        api_client.post("/api/admin/employees", json=short_password_payload).status_code
        == 422
    )
    assert api_client.get("/admin/users").status_code == 403
    assert api_client.get("/admin/employees").status_code == 200
    assert api_client.get("/admin/enrollment").status_code == 200
    dashboard = api_client.get("/dashboard")
    assert 'href="/admin/employees"' in dashboard.text
    assert "Add Employee" in dashboard.text

    api_client.post("/auth/logout")
    hr = _create_user(db_session, username="hr", role="admin", admin_role="hr")
    api_client.post(
        "/auth/login",
        json={"username": hr.username, "password": "test-password"},
    )
    hr_payload = {
        **create_payload,
        "employee_code": "EMP-201",
        "email": "created.hr@example.org",
        "username": "created.employee.hr",
    }
    created = api_client.post("/api/admin/employees", json=hr_payload)
    assert created.status_code == 201
    assert created.json()["employee_code"] == "EMP-201"
    assert created.json()["username"] == "created.employee.hr"
    assert created.json()["face_enrolled"] is False
    employee_page = api_client.get("/admin/employees")
    assert employee_page.status_code == 200
    assert "Add Employee" in employee_page.text
    assert "Capture employee pictures" in employee_page.text
    assert "Capture 10 pictures" in employee_page.text
    assert "one picture per second" in employee_page.text
    assert "Train the system" in employee_page.text
    assert api_client.get("/dashboard").status_code == 200


def test_employee_dashboard_only_shows_own_profile_and_attendance(
    api_client, db_session: Session, employee
) -> None:
    user = _create_user(
        db_session,
        username="employee",
        role="employee",
        employee_id=employee.id,
    )
    api_client.post(
        "/auth/login",
        json={"username": user.username, "password": "test-password"},
    )

    dashboard = api_client.get("/dashboard")

    assert dashboard.status_code == 200
    assert "EMP-001" in dashboard.text
    assert api_client.get("/api/admin/dashboard").status_code == 403


def test_attendance_edit_api_persists_audit_and_locked_month_permissions(
    api_client, db_session: Session, employee
) -> None:
    hr = _create_user(db_session, username="hr", role="admin", admin_role="hr")
    record = AttendanceDaily(
        employee_id=employee.id,
        date=date(2026, 11, 24),
        first_login_at=datetime(2026, 11, 24, 8, 0, tzinfo=UTC),
        status="Incomplete",
    )
    db_session.add(record)
    db_session.commit()
    api_client.post(
        "/auth/login",
        json={"username": hr.username, "password": "test-password"},
    )

    edited = api_client.put(
        f"/api/admin/attendance/{record.id}",
        json={
            "employee_id": employee.id,
            "date": "2026-11-24",
            "first_login_at": "2026-11-24T08:00:00Z",
            "final_logout_at": "2026-11-24T16:30:00Z",
            "status": "Present",
            "reason": "Approved manager correction",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["manually_edited"] is True
    audit = api_client.get(f"/api/admin/attendance/{record.id}/audit")
    assert audit.status_code == 200
    assert audit.json()[0]["reason"] == "Approved manager correction"

    lock = api_client.put(
        "/api/admin/month-locks",
        json={"year": 2026, "month": 11, "locked": True},
    )
    assert lock.status_code == 403


def test_super_admin_can_lock_month_and_download_excel_report(
    api_client, db_session: Session
) -> None:
    super_admin = _create_user(
        db_session, username="root", role="admin", admin_role="super_admin"
    )
    api_client.post(
        "/auth/login",
        json={"username": super_admin.username, "password": "test-password"},
    )

    for path in (
        "/dashboard",
        "/admin/attendance",
        "/admin/employees",
        "/admin/holidays",
        "/admin/month-locks",
        "/admin/reports",
        "/admin/users",
    ):
        assert api_client.get(path).status_code == 200

    locked = api_client.put(
        "/api/admin/month-locks",
        json={"year": 2026, "month": 11, "locked": True},
    )
    assert locked.status_code == 200 and locked.json()["locked"] is True
    report = api_client.get("/api/admin/reports/monthly.xlsx?year=2026&month=11")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert report.content.startswith(b"PK")
