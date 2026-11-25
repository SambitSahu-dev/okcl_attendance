from sqlalchemy import Engine, inspect, text


def ensure_compatibility_schema(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    employee_columns = (
        {column["name"] for column in inspector.get_columns("employees")}
        if inspector.has_table("employees")
        else set()
    )
    attendance_columns = (
        {column["name"] for column in inspector.get_columns("attendance_daily")}
        if inspector.has_table("attendance_daily")
        else set()
    )
    user_columns = (
        {column["name"] for column in inspector.get_columns("users")}
        if inspector.has_table("users")
        else set()
    )
    with engine.begin() as connection:
        if employee_columns and "deleted_at" not in employee_columns:
            connection.execute(
                text("ALTER TABLE employees ADD COLUMN deleted_at DATETIME")
            )

        if attendance_columns:
            if "last_login_at" not in attendance_columns:
                connection.execute(
                    text(
                        "ALTER TABLE attendance_daily ADD COLUMN last_login_at DATETIME"
                    )
                )
            connection.execute(
                text(
                    "UPDATE attendance_daily SET last_login_at = first_login_at "
                    "WHERE last_login_at IS NULL AND first_login_at IS NOT NULL"
                )
            )

        if user_columns:
            if "admin_role" not in user_columns:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN admin_role VARCHAR(32)")
                )
            if "display_name" not in user_columns:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN display_name VARCHAR(200)")
                )
            if "email" not in user_columns:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN email VARCHAR(320)")
                )
            connection.execute(
                text(
                    "UPDATE users SET admin_role = 'standard' "
                    "WHERE role = 'admin' AND admin_role IS NULL"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"
                )
            )
