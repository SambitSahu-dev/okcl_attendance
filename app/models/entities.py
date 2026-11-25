from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime


def utc_now() -> datetime:
    return datetime.now(UTC)


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    user: Mapped["User | None"] = relationship(back_populates="employee", uselist=False)
    attendance_records: Mapped[list["AttendanceDaily"]] = relationship(
        back_populates="employee"
    )


class FaceEmbedding(Base):
    __tablename__ = "face_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    embedding_data: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)

    employee: Mapped[Employee] = relationship()


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    consented_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    consent_text_version: Mapped[str] = mapped_column(String(50))
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)

    employee: Mapped[Employee] = relationship()


class Kiosk(Base):
    __tablename__ = "kiosks"

    kiosk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    office: Mapped[str] = mapped_column(String(200))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AttendanceDaily(Base):
    __tablename__ = "attendance_daily"
    __table_args__ = (
        UniqueConstraint("employee_id", "date", name="uq_attendance_employee_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    first_login_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    final_logout_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    status: Mapped[str] = mapped_column(String(40), default="Incomplete", nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manually_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    employee: Mapped[Employee] = relationship(back_populates="attendance_records")
    logout_logs: Mapped[list["LogoutLog"]] = relationship(back_populates="attendance")


class LogoutLog(Base):
    __tablename__ = "logout_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    attendance_id: Mapped[int | None] = mapped_column(
        ForeignKey("attendance_daily.id"), nullable=True, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    logout_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exception_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    attendance: Mapped[AttendanceDaily | None] = relationship(back_populates="logout_logs")
    employee: Mapped[Employee] = relationship()


class Holiday(Base):
    __tablename__ = "holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AttendanceAudit(Base):
    __tablename__ = "attendance_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attendance_id: Mapped[int] = mapped_column(ForeignKey("attendance_daily.id"), index=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    edited_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    old_values: Mapped[str] = mapped_column(Text)
    new_values: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    attendance: Mapped[AttendanceDaily] = relationship()
    actor: Mapped["User"] = relationship()


class MonthLock(Base):
    __tablename__ = "month_locks"
    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_month_locks_year_month"),
        CheckConstraint("month >= 1 AND month <= 12", name="ck_month_locks_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    locked_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    locked_by: Mapped["User | None"] = relationship(foreign_keys=[locked_by_user_id])
    updated_by: Mapped["User | None"] = relationship(foreign_keys=[updated_by_user_id])


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'employee')", name="ck_users_role"),
        CheckConstraint(
            "(role = 'admin' AND employee_id IS NULL) OR "
            "(role = 'employee' AND employee_id IS NOT NULL)",
            name="ck_users_role_employee",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(
        String(320), unique=True, index=True, nullable=True
    )
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    admin_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), unique=True, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    employee: Mapped[Employee | None] = relationship(back_populates="user")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now
    )


class ScanAttempt(Base):
    __tablename__ = "scan_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, index=True
    )
    kiosk_id: Mapped[str | None] = mapped_column(
        ForeignKey("kiosks.kiosk_id"), nullable=True, index=True
    )
    scan_type: Mapped[str] = mapped_column(String(20), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    employee: Mapped[Employee | None] = relationship()
    kiosk: Mapped[Kiosk | None] = relationship()
