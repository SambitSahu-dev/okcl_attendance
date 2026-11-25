import json
from dataclasses import dataclass
from typing import Any

import numpy as np
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Consent, Employee, FaceEmbedding, Setting

SFACE_MODEL_VERSION = "opencv-sface-2021dec-v1"
DEFAULT_FACE_MATCH_THRESHOLD = 0.363


class FaceEmbeddingEncryptionError(Exception):
    pass


class FaceEmbeddingCipher:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise FaceEmbeddingEncryptionError(
                "FACE_EMBEDDING_KEY must be a valid Fernet key."
            ) from error

    def encrypt(self, employee_id: int, vector: np.ndarray) -> str:
        normalized = _normalized_vector(vector)
        payload = json.dumps(
            {"employee_id": employee_id, "vector": normalized.tolist()},
            separators=(",", ":"),
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decrypt(self, employee_id: int, token: str) -> np.ndarray:
        try:
            payload: Any = json.loads(
                self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
            )
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FaceEmbeddingEncryptionError(
                "A stored face embedding could not be decrypted."
            ) from error

        if not isinstance(payload, dict) or payload.get("employee_id") != employee_id:
            raise FaceEmbeddingEncryptionError(
                "A stored face embedding is associated with the wrong employee."
            )
        try:
            return _normalized_vector(np.asarray(payload["vector"], dtype=np.float32))
        except (KeyError, TypeError, ValueError) as error:
            raise FaceEmbeddingEncryptionError(
                "A stored face embedding has an invalid vector."
            ) from error


@dataclass(frozen=True)
class FaceMatch:
    employee: Employee
    similarity: float


def _normalized_vector(vector: np.ndarray) -> np.ndarray:
    normalized = np.asarray(vector, dtype=np.float32).reshape(-1)
    if normalized.size == 0 or not np.isfinite(normalized).all():
        raise FaceEmbeddingEncryptionError("Face embedding must be a finite vector.")
    norm = float(np.linalg.norm(normalized))
    if norm == 0:
        raise FaceEmbeddingEncryptionError("Face embedding must not be a zero vector.")
    return normalized / norm


def get_face_match_threshold(db: Session) -> float:
    setting = db.get(Setting, "face_match_threshold")
    if setting is None:
        threshold = get_settings().face_match_threshold
    else:
        try:
            threshold = float(setting.value)
        except ValueError as error:
            raise FaceEmbeddingEncryptionError(
                "The face_match_threshold setting must be a number from 0 to 1."
            ) from error
    if not 0 <= threshold <= 1:
        raise FaceEmbeddingEncryptionError(
            "The face_match_threshold setting must be a number from 0 to 1."
        )
    return threshold


def set_face_match_threshold(db: Session, threshold: float) -> None:
    if not 0 <= threshold <= 1:
        raise ValueError("Face match threshold must be between 0 and 1.")
    setting = db.get(Setting, "face_match_threshold")
    if setting is None:
        db.add(Setting(key="face_match_threshold", value=str(threshold)))
    else:
        setting.value = str(threshold)
    db.commit()


def match_enrolled_employee(
    db: Session,
    query_vector: np.ndarray,
    cipher: FaceEmbeddingCipher,
    *,
    threshold: float,
) -> FaceMatch | None:
    query = _normalized_vector(query_vector)
    latest_consent = (
        select(
            Consent.employee_id.label("employee_id"),
            func.max(Consent.id).label("consent_id"),
        )
        .group_by(Consent.employee_id)
        .subquery()
    )
    rows = db.execute(
        select(FaceEmbedding, Employee)
        .join(Employee, Employee.id == FaceEmbedding.employee_id)
        .join(latest_consent, latest_consent.c.employee_id == Employee.id)
        .join(Consent, Consent.id == latest_consent.c.consent_id)
        .where(
            Employee.is_active.is_(True),
            Consent.granted.is_(True),
            FaceEmbedding.model_version == SFACE_MODEL_VERSION,
        )
    ).all()

    best_match = None
    best_similarity = -1.0
    for stored_embedding, employee in rows:
        reference = cipher.decrypt(employee.id, stored_embedding.embedding_data)
        if reference.shape != query.shape:
            continue
        similarity = float(np.dot(query, reference))
        if similarity > best_similarity:
            best_match = employee
            best_similarity = similarity

    if best_match is None or best_similarity < threshold:
        return None
    return FaceMatch(employee=best_match, similarity=best_similarity)
