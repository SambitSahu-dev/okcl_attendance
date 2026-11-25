import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.models import AttendanceDaily, Kiosk
from app.services.attendance import (
    AttendanceRuleError,
    attendance_working_seconds,
    record_login,
    record_logout,
)
from app.services.biometrics import (
    FaceEmbeddingCipher,
    get_face_match_threshold,
    match_enrolled_employee,
)
from app.services.login_images import LoginImageStore
from vision.interfaces import (
    BlinkLivenessChecker,
    FaceDetector,
    FaceEmbedder,
    GestureRecognizer,
)
from vision.pipeline import AttendanceVisionPipeline
from vision.types import Frame, HandGesture

ScanOperation = Literal["login", "logout"]


class KioskScanError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ScanProgress:
    state: str
    prompt: str
    employee_id: int | None = None
    employee_name: str | None = None
    similarity: float | None = None
    attendance: AttendanceDaily | None = None
    message: str | None = None
    operation: ScanOperation | None = None
    event_at: datetime | None = None


@dataclass
class _ScanContext:
    kiosk_id: str
    pipeline: AttendanceVisionPipeline
    last_activity: float
    employee_id: int | None = None


class KioskScanCoordinator:
    def __init__(
        self,
        *,
        face_detector: FaceDetector,
        face_embedder: FaceEmbedder,
        liveness_factory: Callable[[], BlinkLivenessChecker],
        gesture_factory: Callable[[], GestureRecognizer],
        image_store: LoginImageStore,
        embedding_cipher: FaceEmbeddingCipher,
        session_ttl_seconds: int = 120,
        max_active_sessions: int = 8,
    ) -> None:
        self._face_detector = face_detector
        self._face_embedder = face_embedder
        self._liveness_factory = liveness_factory
        self._gesture_factory = gesture_factory
        self._image_store = image_store
        self._embedding_cipher = embedding_cipher
        self._session_ttl_seconds = session_ttl_seconds
        self._max_active_sessions = max_active_sessions
        self._sessions: dict[str, _ScanContext] = {}
        self._completed_employee_by_kiosk: dict[str, int] = {}
        self._departed_frames_by_kiosk: dict[str, int] = {}
        self._lock = threading.RLock()

    def _new_pipeline(self) -> AttendanceVisionPipeline:
        return AttendanceVisionPipeline(
            face_detector=self._face_detector,
            face_embedder=self._face_embedder,
            liveness_checker=self._liveness_factory(),
            gesture_recognizer=self._gesture_factory(),
        )

    def _close_context(self, context: _ScanContext) -> None:
        context.pipeline.close()

    def _expire_sessions(self, now: float) -> None:
        expired = [
            scan_id
            for scan_id, context in self._sessions.items()
            if now - context.last_activity > self._session_ttl_seconds
        ]
        for scan_id in expired:
            self._close_context(self._sessions.pop(scan_id))

    def start_scan(self, db: Session, *, kiosk_id: str) -> tuple[str, str]:
        with self._lock:
            now = time.monotonic()
            self._expire_sessions(now)
            kiosk = db.get(Kiosk, kiosk_id)
            if kiosk is None or not kiosk.is_active:
                raise KioskScanError("Kiosk not found or inactive.", 404)
            if len(self._sessions) >= self._max_active_sessions:
                raise KioskScanError("The kiosk is busy. Try again shortly.", 429)

            pipeline = self._new_pipeline()
            scan_id = secrets.token_urlsafe(24)
            self._sessions[scan_id] = _ScanContext(
                kiosk_id=kiosk_id,
                pipeline=pipeline,
                last_activity=now,
            )
            return (
                scan_id,
                f"{pipeline.liveness_prompt}. Show a thumb to check in or an open palm to check out.",
            )

    def cancel_scan(self, scan_id: str) -> None:
        with self._lock:
            context = self._sessions.pop(scan_id, None)
            if context is not None:
                self._close_context(context)

    def _reset_identity(self, context: _ScanContext) -> None:
        if context.employee_id is not None:
            context.employee_id = None
            context.pipeline.new_liveness_challenge()

    def _is_waiting_for_departure(
        self, kiosk_id: str, employee_id: int | None
    ) -> bool:
        completed_employee_id = self._completed_employee_by_kiosk.get(kiosk_id)
        if completed_employee_id is None:
            return False
        if employee_id == completed_employee_id:
            self._departed_frames_by_kiosk[kiosk_id] = 0
            return True
        if employee_id is not None:
            self._completed_employee_by_kiosk.pop(kiosk_id, None)
            self._departed_frames_by_kiosk.pop(kiosk_id, None)
            return False

        departed_frames = self._departed_frames_by_kiosk.get(kiosk_id, 0) + 1
        if departed_frames >= 3:
            self._completed_employee_by_kiosk.pop(kiosk_id, None)
            self._departed_frames_by_kiosk.pop(kiosk_id, None)
            return False
        self._departed_frames_by_kiosk[kiosk_id] = departed_frames
        return True

    def _mark_attendance_completed(self, kiosk_id: str, employee_id: int) -> None:
        self._completed_employee_by_kiosk[kiosk_id] = employee_id
        self._departed_frames_by_kiosk[kiosk_id] = 0

    def process_frame(self, db: Session, *, scan_id: str, frame: Frame) -> ScanProgress:
        with self._lock:
            now = time.monotonic()
            self._expire_sessions(now)
            context = self._sessions.get(scan_id)
            if context is None:
                raise KioskScanError("Scan session expired or not found.", 404)
            context.last_activity = now

            observation = context.pipeline.process(frame)
            faces = observation.faces
            if not faces:
                if self._is_waiting_for_departure(context.kiosk_id, None):
                    return ScanProgress(
                        state="cooldown",
                        prompt="Attendance recorded. Step away from the camera before scanning again.",
                    )
                self._reset_identity(context)
                return ScanProgress(state="no_face", prompt="Position your face in view.")
            if len(faces) != 1:
                if self._is_waiting_for_departure(context.kiosk_id, None):
                    return ScanProgress(
                        state="cooldown",
                        prompt="Attendance recorded. Step away from the camera before scanning again.",
                    )
                self._reset_identity(context)
                return ScanProgress(
                    state="multiple_faces",
                    prompt="One person at a time; ensure only your face is visible.",
                )

            threshold = get_face_match_threshold(db)
            match = match_enrolled_employee(
                db,
                faces[0].vector,
                self._embedding_cipher,
                threshold=threshold,
            )
            if match is None:
                if self._is_waiting_for_departure(context.kiosk_id, None):
                    return ScanProgress(
                        state="cooldown",
                        prompt="Attendance recorded. Step away from the camera before scanning again.",
                    )
                self._reset_identity(context)
                return ScanProgress(
                    state="face_not_recognized",
                    prompt="Face not recognized. Please try again or contact an admin.",
                )
            if self._is_waiting_for_departure(
                context.kiosk_id, match.employee.id
            ):
                return ScanProgress(
                    state="cooldown",
                    prompt="Attendance recorded. Step away from the camera before scanning again.",
                    employee_id=match.employee.id,
                    employee_name=match.employee.full_name,
                    similarity=match.similarity,
                )
            if context.employee_id != match.employee.id:
                context.employee_id = match.employee.id
                challenge = context.pipeline.new_liveness_challenge()
                return ScanProgress(
                    state="liveness",
                    prompt=(
                        f"Face recognized. {challenge}; show a thumb to check in "
                        "or an open palm to check out."
                    ),
                    employee_id=match.employee.id,
                    employee_name=match.employee.full_name,
                    similarity=match.similarity,
                )

            liveness = observation.liveness
            gestures = observation.hands
            has_thumb = any(hand.gesture == HandGesture.THUMB for hand in gestures)
            has_open_palm = any(
                hand.gesture == HandGesture.OPEN_PALM for hand in gestures
            )
            if has_thumb and has_open_palm:
                prompt = (
                    "Show one hand gesture at a time: thumb for check-in or "
                    "open palm for check-out."
                )
                if not liveness.passed:
                    prompt = f"{liveness.prompt}; {prompt}"
                return ScanProgress(
                    state="gesture",
                    prompt=prompt,
                    employee_id=match.employee.id,
                    employee_name=match.employee.full_name,
                    similarity=match.similarity,
                )
            if not liveness.passed:
                if has_thumb:
                    prompt = f"{liveness.prompt} while keeping your thumb visible."
                elif has_open_palm:
                    prompt = f"{liveness.prompt} while keeping your open palm visible."
                else:
                    prompt = (
                        f"{liveness.prompt}; show a thumb to check in or an open "
                        "palm to check out."
                    )
                return ScanProgress(
                    state="liveness",
                    prompt=prompt,
                    employee_id=match.employee.id,
                    employee_name=match.employee.full_name,
                    similarity=match.similarity,
                )

            if not has_thumb and not has_open_palm:
                return ScanProgress(
                    state="gesture",
                    prompt="Show a thumb to check in or an open palm to check out.",
                    employee_id=match.employee.id,
                    employee_name=match.employee.full_name,
                    similarity=match.similarity,
                )

            operation: ScanOperation = "login" if has_thumb else "logout"
            gesture = HandGesture.THUMB if has_thumb else HandGesture.OPEN_PALM
            try:
                kiosk_id = context.kiosk_id
                if operation == "login":
                    attendance = record_login(
                        db,
                        employee_id=match.employee.id,
                        kiosk_id=kiosk_id,
                        gesture=gesture.value,
                        liveness_passed=True,
                        image_frame=frame,
                        image_store=self._image_store,
                    )
                else:
                    attendance = record_logout(
                        db,
                        employee_id=match.employee.id,
                        kiosk_id=kiosk_id,
                        gesture=gesture.value,
                        liveness_passed=True,
                    )
            except AttendanceRuleError:
                self._mark_attendance_completed(context.kiosk_id, match.employee.id)
                raise
            finally:
                self.cancel_scan(scan_id)
            self._mark_attendance_completed(context.kiosk_id, match.employee.id)
            event_at = (
                attendance.last_login_at or attendance.first_login_at
                if operation == "login"
                else attendance.final_logout_at
            )
            return ScanProgress(
                state="complete",
                prompt="Attendance recorded.",
                employee_id=match.employee.id,
                employee_name=match.employee.full_name,
                similarity=match.similarity,
                attendance=attendance,
                message=(
                    "Login recorded"
                    if operation == "login"
                    else "Logout recorded"
                ),
                operation=operation,
                event_at=event_at,
            )

    @staticmethod
    def working_seconds(progress: ScanProgress) -> int | None:
        if progress.attendance is None:
            return None
        return attendance_working_seconds(progress.attendance)
