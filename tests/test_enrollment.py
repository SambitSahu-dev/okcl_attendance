import base64
import hashlib
from datetime import UTC, datetime

import cv2
import numpy as np
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.models import Consent, FaceEmbedding
from app.services.biometrics import FaceEmbeddingCipher, FaceEmbeddingEncryptionError
from app.services.face_enrollment import EnrollmentError, enroll_employee_faces
from app.services.login_images import FileSystemLoginImageStore
from vision.types import FaceDetection


def _capture() -> str:
    success, buffer = cv2.imencode(
        ".jpg", np.full((80, 80, 3), 127, dtype=np.uint8)
    )
    assert success
    return base64.b64encode(buffer).decode("ascii")


class FakeDetector:
    def __init__(self, count: int = 1) -> None:
        self.count = count

    def detect(self, _frame):
        detection = FaceDetection(1, 2, 20, 20, 0.99, (), tuple(range(15)))
        return [detection] * self.count


class FakeEmbedder:
    def embed(self, _frame, _detection):
        vector = np.zeros(128, dtype=np.float32)
        vector[0] = 1
        return vector


@pytest.fixture
def embedding_cipher() -> FaceEmbeddingCipher:
    return FaceEmbeddingCipher(Fernet.generate_key().decode("ascii"))


@pytest.mark.parametrize("capture_count", [3, 5, 10])
def test_enrollment_records_consent_and_only_encrypted_embeddings(
    db_session, employee, embedding_cipher, capture_count
) -> None:
    consent, count = enroll_employee_faces(
        db_session,
        employee_id=employee.id,
        consent_granted=True,
        captures=[_capture() for _ in range(capture_count)],
        detector=FakeDetector(),
        embedder=FakeEmbedder(),
        cipher=embedding_cipher,
    )

    embeddings = db_session.scalars(select(FaceEmbedding)).all()
    assert count == len(embeddings) == capture_count
    assert consent.granted is True
    assert consent.consented_at.tzinfo is not None
    assert all(
        np.array_equal(
            embedding_cipher.decrypt(employee.id, embedding.embedding_data),
            np.eye(1, 128, dtype=np.float32).reshape(-1),
        )
        for embedding in embeddings
    )
    assert all(not embedding.embedding_data.startswith("{") for embedding in embeddings)


def test_declined_consent_is_recorded_and_creates_no_embeddings(
    db_session, employee, embedding_cipher
) -> None:
    with pytest.raises(EnrollmentError, match="explicit employee consent"):
        enroll_employee_faces(
            db_session,
            employee_id=employee.id,
            consent_granted=False,
            captures=[],
            detector=FakeDetector(),
            embedder=FakeEmbedder(),
            cipher=embedding_cipher,
        )

    consent = db_session.scalar(select(Consent))
    assert consent is not None and consent.granted is False
    assert db_session.scalars(select(FaceEmbedding)).all() == []


def test_enrollment_rejects_capture_without_a_face(db_session, employee, embedding_cipher):
    with pytest.raises(EnrollmentError, match="exactly one"):
        enroll_employee_faces(
            db_session,
            employee_id=employee.id,
            consent_granted=True,
            captures=[_capture(), _capture(), _capture()],
            detector=FakeDetector(count=0),
            embedder=FakeEmbedder(),
            cipher=embedding_cipher,
        )
    assert db_session.scalars(select(FaceEmbedding)).all() == []


def test_enrollment_requires_three_to_ten_single_face_captures(
    db_session, employee, embedding_cipher
) -> None:
    with pytest.raises(EnrollmentError, match="3 to 10"):
        enroll_employee_faces(
            db_session,
            employee_id=employee.id,
            consent_granted=True,
            captures=[_capture(), _capture()],
            detector=FakeDetector(),
            embedder=FakeEmbedder(),
            cipher=embedding_cipher,
        )
    assert db_session.scalars(select(FaceEmbedding)).all() == []

    with pytest.raises(EnrollmentError, match="3 to 10"):
        enroll_employee_faces(
            db_session,
            employee_id=employee.id,
            consent_granted=True,
            captures=[_capture() for _ in range(11)],
            detector=FakeDetector(),
            embedder=FakeEmbedder(),
            cipher=embedding_cipher,
        )
    assert db_session.scalars(select(FaceEmbedding)).all() == []

    with pytest.raises(EnrollmentError, match="exactly one"):
        enroll_employee_faces(
            db_session,
            employee_id=employee.id,
            consent_granted=True,
            captures=[_capture(), _capture(), _capture()],
            detector=FakeDetector(count=2),
            embedder=FakeEmbedder(),
            cipher=embedding_cipher,
        )
    assert db_session.scalars(select(FaceEmbedding)).all() == []


def test_face_embedding_cipher_rejects_tampering_and_wrong_employee(
    embedding_cipher,
) -> None:
    vector = np.eye(1, 128, dtype=np.float32).reshape(-1)
    encrypted = embedding_cipher.encrypt(7, vector)

    with pytest.raises(FaceEmbeddingEncryptionError, match="wrong employee"):
        embedding_cipher.decrypt(8, encrypted)
    with pytest.raises(FaceEmbeddingEncryptionError, match="could not be decrypted"):
        embedding_cipher.decrypt(7, encrypted[:-3] + "abc")


def test_login_image_store_watermarks_and_hashes_saved_jpeg(tmp_path, kiosk) -> None:
    image_store = FileSystemLoginImageStore(tmp_path)
    frame = np.full((160, 320, 3), 100, dtype=np.uint8)
    captured_at = datetime(2026, 11, 24, 8, 15, tzinfo=UTC)

    stored = image_store.save_login_image(
        employee_id=5,
        kiosk=kiosk,
        captured_at=captured_at,
        frame=frame,
    )

    image_path = tmp_path / "storage" / "images" / "5" / "2026-11-24.jpg"
    image_bytes = image_path.read_bytes()
    saved_image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert stored.path == "storage/images/5/2026-11-24.jpg"
    assert stored.sha256 == hashlib.sha256(image_bytes).hexdigest()
    assert not np.array_equal(saved_image, frame)
    with pytest.raises(FileExistsError):
        image_store.save_login_image(
            employee_id=5,
            kiosk=kiosk,
            captured_at=captured_at,
            frame=frame,
        )

