import base64
import binascii
from datetime import UTC, datetime

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Consent, Employee, FaceEmbedding
from app.services.biometrics import (
    SFACE_MODEL_VERSION,
    FaceEmbeddingCipher,
)
from vision.interfaces import FaceDetector, FaceEmbedder

CONSENT_TEXT_VERSION = "1.0"
MIN_ENROLLMENT_CAPTURES = 3
MAX_ENROLLMENT_CAPTURES = 10
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_WIDTH = 1920
MAX_CAPTURE_HEIGHT = 1080


class EnrollmentError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def decode_capture(capture: str) -> np.ndarray:
    encoded = capture
    if capture.startswith("data:"):
        header, separator, encoded = capture.partition(",")
        if not separator or header not in {
            "data:image/jpeg;base64",
            "data:image/png;base64",
        }:
            raise EnrollmentError("Captures must be JPEG or PNG data URLs.", 422)

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise EnrollmentError("A capture is not valid base64 image data.", 422) from error

    if not image_bytes or len(image_bytes) > MAX_CAPTURE_BYTES:
        raise EnrollmentError("Each capture must be a non-empty image under 2 MB.", 413)

    frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise EnrollmentError("A capture could not be decoded as an image.", 422)
    height, width = frame.shape[:2]
    if width > MAX_CAPTURE_WIDTH or height > MAX_CAPTURE_HEIGHT:
        raise EnrollmentError("Capture dimensions must not exceed 1920x1080.", 422)
    return frame


def enroll_employee_faces(
    db: Session,
    *,
    employee_id: int,
    consent_granted: bool,
    captures: list[str],
    detector: FaceDetector,
    embedder: FaceEmbedder,
    cipher: FaceEmbeddingCipher | None,
) -> tuple[Consent, int]:
    employee = db.get(Employee, employee_id)
    if employee is None or not employee.is_active:
        raise EnrollmentError("Employee not found or inactive.", 404)

    existing_embedding = db.scalar(
        select(FaceEmbedding.id).where(FaceEmbedding.employee_id == employee_id).limit(1)
    )
    if existing_embedding is not None:
        raise EnrollmentError(
            "This employee is already enrolled. Re-enrollment is not supported here.",
            409,
        )

    consent = Consent(
        employee_id=employee_id,
        consented_at=datetime.now(UTC),
        consent_text_version=CONSENT_TEXT_VERSION,
        granted=consent_granted,
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)

    if not consent_granted:
        raise EnrollmentError("Enrollment requires explicit employee consent.", 403)
    if not MIN_ENROLLMENT_CAPTURES <= len(captures) <= MAX_ENROLLMENT_CAPTURES:
        raise EnrollmentError("Enrollment requires 3 to 10 face captures.", 422)
    if cipher is None:
        raise EnrollmentError(
            "Set FACE_EMBEDDING_KEY before enrolling employees.", 503
        )

    embedding_records = []
    for capture_number, capture in enumerate(captures, start=1):
        frame = decode_capture(capture)
        detections = detector.detect(frame)
        if len(detections) != 1:
            raise EnrollmentError(
                f"Capture {capture_number} must contain exactly one detectable face.",
                422,
            )
        vector = np.asarray(embedder.embed(frame, detections[0]), dtype=np.float32)
        if vector.size != 128 or not np.isfinite(vector).all():
            raise EnrollmentError(
                f"Capture {capture_number} produced an invalid SFace embedding.",
                422,
            )
        embedding_records.append(
            FaceEmbedding(
                employee_id=employee_id,
                embedding_data=cipher.encrypt(employee_id, vector),
                model_version=SFACE_MODEL_VERSION,
            )
        )

    db.add_all(embedding_records)
    db.commit()
    return consent, len(embedding_records)
