from sqlalchemy import create_engine, text

from app.db.migrations import ensure_compatibility_schema


def test_legacy_admin_users_receive_standard_admin_role() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, role VARCHAR(20) NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO users (id, role) VALUES (1, 'admin'), (2, 'employee')")
        )

    ensure_compatibility_schema(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, admin_role FROM users ORDER BY id")
        ).all()
    assert rows == [(1, "standard"), (2, None)]
    engine.dispose()


def test_legacy_user_table_receives_profile_columns_and_unique_email_index() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, role VARCHAR(20) NOT NULL)"
            )
        )

    ensure_compatibility_schema(engine)

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(users)"))
        }
        indexes = connection.execute(text("PRAGMA index_list(users)")).all()
    assert {"display_name", "email"}.issubset(columns)
    assert any(index[1] == "ix_users_email" and index[2] == 1 for index in indexes)
    engine.dispose()


def test_legacy_employee_table_receives_deleted_at_column() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE employees "
                "(id INTEGER PRIMARY KEY, employee_code VARCHAR(64) NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO employees (id, employee_code) VALUES (1, 'EMP-001')")
        )

    ensure_compatibility_schema(engine)
    ensure_compatibility_schema(engine)

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(employees)"))
        }
        deleted_at = connection.execute(
            text("SELECT deleted_at FROM employees WHERE id = 1")
        ).scalar_one()
    assert "deleted_at" in columns
    assert deleted_at is None
    engine.dispose()


def test_legacy_attendance_table_receives_last_login_timestamp() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE attendance_daily "
                "(id INTEGER PRIMARY KEY, first_login_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO attendance_daily (id, first_login_at) "
                "VALUES (1, '2026-11-25 08:15:00'), (2, NULL)"
            )
        )

    ensure_compatibility_schema(engine)
    ensure_compatibility_schema(engine)

    with engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(attendance_daily)"))
        }
        rows = connection.execute(
            text("SELECT id, last_login_at FROM attendance_daily ORDER BY id")
        ).all()
    assert "last_login_at" in columns
    assert rows == [(1, "2026-11-25 08:15:00"), (2, None)]
    engine.dispose()
