import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import Base
from app.db.migrations import ensure_compatibility_schema
from app.db.session import SessionLocal, engine
from app.models import Kiosk, Setting, User
from app.services.auth import hash_password


def _bootstrap_user(
    db: Session, setting_key: str, username: str
) -> User | None:
    marker = db.get(Setting, setting_key)
    if marker is not None:
        try:
            user_id = int(marker.value)
        except ValueError as error:
            raise RuntimeError(f"Invalid {setting_key} setting.") from error
        user = db.get(User, user_id)
        if user is None:
            raise RuntimeError(f"The account referenced by {setting_key} no longer exists.")
        return user
    return db.scalar(select(User).where(User.username == username))


def _save_bootstrap_user_id(db: Session, setting_key: str, user: User) -> None:
    marker = db.get(Setting, setting_key)
    if marker is None:
        db.add(Setting(key=setting_key, value=str(user.id)))
    else:
        marker.value = str(user.id)


def seed() -> str | None:
    Base.metadata.create_all(bind=engine)
    ensure_compatibility_schema(engine)
    settings = get_settings()
    username = settings.admin_username
    password = settings.admin_password
    generated_password = None

    with SessionLocal() as db:
        admin = _bootstrap_user(db, "bootstrap_admin_user_id", username)
        if admin is None:
            if not password:
                password = secrets.token_urlsafe(18)
                generated_password = password
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role="admin",
                    admin_role="standard",
                )
            )
            db.flush()
            admin = db.scalar(select(User).where(User.username == username))
        elif admin.role != "admin":
            raise RuntimeError("ADMIN_USERNAME is already assigned to a non-admin user.")
        elif admin.admin_role is None:
            admin.admin_role = "standard"
        if admin is not None:
            _save_bootstrap_user_id(db, "bootstrap_admin_user_id", admin)

        super_username = settings.super_admin_username
        super_password = settings.super_admin_password
        if bool(super_username) != bool(super_password):
            raise RuntimeError(
                "Set both SUPER_ADMIN_USERNAME and SUPER_ADMIN_PASSWORD to bootstrap a super-admin."
            )
        if super_username and super_password:
            super_admin = _bootstrap_user(
                db, "bootstrap_super_admin_user_id", super_username
            )
            if super_admin is None:
                db.add(
                    User(
                        username=super_username,
                        password_hash=hash_password(super_password),
                        role="admin",
                        admin_role="super_admin",
                    )
                )
                db.flush()
                super_admin = db.scalar(
                    select(User).where(User.username == super_username)
                )
            elif super_admin.role != "admin":
                raise RuntimeError(
                    "SUPER_ADMIN_USERNAME is already assigned to a non-admin user."
                )
            else:
                super_admin.admin_role = "super_admin"
            if super_admin is not None:
                _save_bootstrap_user_id(
                    db, "bootstrap_super_admin_user_id", super_admin
                )

        kiosk_id = settings.kiosk_id
        kiosk = db.get(Kiosk, kiosk_id)
        if kiosk is None:
            db.add(
                Kiosk(
                    kiosk_id=kiosk_id,
                    name=settings.kiosk_name,
                    office=settings.kiosk_office,
                    latitude=settings.kiosk_latitude,
                    longitude=settings.kiosk_longitude,
                )
            )
        db.commit()
    return generated_password


if __name__ == "__main__":
    generated = seed()
    if generated:
        print(f"Generated one-time admin password: {generated}")
    print("Seed complete.")
