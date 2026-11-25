import hashlib
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import cv2

from app.models import Kiosk
from vision.types import Frame


@dataclass(frozen=True)
class StoredLoginImage:
    path: str
    sha256: str


class LoginImageStore(Protocol):
    def save_login_image(
        self,
        *,
        employee_id: int,
        kiosk: Kiosk,
        captured_at: datetime,
        frame: Frame,
    ) -> StoredLoginImage: ...

    def delete_login_image(self, path: str) -> None: ...

    def delete_employee_images(self, employee_id: int) -> None: ...


class FileSystemLoginImageStore:
    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path(__file__).resolve().parents[2]
        self._storage_root = self._project_root / "storage"

    def save_login_image(
        self,
        *,
        employee_id: int,
        kiosk: Kiosk,
        captured_at: datetime,
        frame: Frame,
    ) -> StoredLoginImage:
        if frame.ndim != 3 or frame.shape[0] == 0 or frame.shape[1] == 0:
            raise ValueError("A non-empty color frame is required for login evidence.")

        stamped_at = captured_at.astimezone(UTC)
        image = frame.copy()
        lines = (
            f"Location: {kiosk.latitude:.6f}, {kiosk.longitude:.6f}",
            f"Date/time: {stamped_at:%Y-%m-%d %H:%M:%S UTC}",
            f"Employee ID: {employee_id}",
        )
        for line_number, text in enumerate(lines):
            cv2.putText(
                image,
                text,
                (12, 28 + line_number * 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 0, 0),
                4,
                cv2.LINE_AA,
            )
            cv2.putText(
                image,
                text,
                (12, 28 + line_number * 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

        encoded, buffer = cv2.imencode(
            ".jpg",
            image,
            [cv2.IMWRITE_JPEG_QUALITY, 90],
        )
        if not encoded:
            raise OSError("OpenCV could not encode login evidence as JPEG.")

        relative_path = Path(
            "storage",
            "images",
            str(employee_id),
            f"{stamped_at:%Y-%m-%d}.jpg",
        )
        destination = self._project_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        image_bytes = buffer.tobytes()
        created = False
        try:
            with destination.open("xb") as image_file:
                created = True
                image_file.write(image_bytes)
                image_file.flush()
        except OSError:
            if created and destination.exists():
                destination.unlink()
            raise

        return StoredLoginImage(
            path=relative_path.as_posix(),
            sha256=hashlib.sha256(image_bytes).hexdigest(),
        )

    def delete_login_image(self, path: str) -> None:
        expected_prefix = "storage/images/"
        if not path.startswith(expected_prefix):
            raise ValueError("Refusing to delete an image outside storage/images.")
        destination = (self._project_root / Path(path)).resolve()
        allowed_root = (self._storage_root / "images").resolve()
        if not destination.is_relative_to(allowed_root):
            raise ValueError("Refusing to delete an image outside storage/images.")
        if destination.exists():
            destination.unlink()

    def delete_employee_images(self, employee_id: int) -> None:
        allowed_root = (self._storage_root / "images").resolve()
        employee_directory = self._storage_root / "images" / str(employee_id)
        if employee_directory.is_symlink():
            employee_directory.unlink()
            return
        resolved_directory = employee_directory.resolve()
        if (
            resolved_directory == allowed_root
            or not resolved_directory.is_relative_to(allowed_root)
        ):
            raise ValueError("Refusing to delete images outside storage/images.")
        if resolved_directory.is_dir():
            shutil.rmtree(resolved_directory)
        elif resolved_directory.exists():
            raise OSError("Employee image path is not a directory.")
