from datetime import UTC, datetime

import numpy as np
from cryptography.fernet import Fernet

from app.models import Consent, FaceEmbedding
from app.services.attendance import record_login
from app.services.biometrics import SFACE_MODEL_VERSION, FaceEmbeddingCipher
from app.services.kiosk_scans import KioskScanCoordinator
from app.services.login_images import StoredLoginImage
from vision.types import FaceDetection, HandGesture, HandObservation, LivenessResult


class FakeDetector:
    def __init__(self):
        self.faces = [FaceDetection(1, 2, 20, 20, 0.99, (), tuple(range(15)))]

    def detect(self, _frame):
        return self.faces


class FakeEmbedder:
    def embed(self, _frame, _detection):
        vector = np.zeros(128, dtype=np.float32)
        vector[0] = 1
        return vector


class FakeLiveness:
    prompt = "Blink once"

    def __init__(self, events, results=(False, True, True, True, True, True)):
        self.events = events
        self.results = iter(results)

    def process(self, _frame):
        self.events.append("liveness")
        passed = next(self.results)
        return LivenessResult(True, 0.3, 0, 1, passed, self.prompt)

    def new_challenge(self):
        return self.prompt

    def close(self):
        self.events.append("liveness_closed")


class FakeGestures:
    def __init__(
        self,
        events,
        results=(
            HandGesture.OTHER,
            HandGesture.OTHER,
            HandGesture.THUMB,
            HandGesture.THUMB,
            HandGesture.THUMB,
            HandGesture.THUMB,
        ),
    ):
        self.events = events
        self.results = iter(results)

    def detect(self, _frame):
        self.events.append("gesture")
        gesture = next(self.results)
        return [HandObservation(gesture, "right", 0.99)]

    def close(self):
        self.events.append("gesture_closed")


class FakeImageStore:
    def __init__(self):
        self.saved = 0

    def save_login_image(self, **_kwargs):
        self.saved += 1
        return StoredLoginImage("storage/images/1/2026-11-24.jpg", "a" * 64)

    def delete_login_image(self, _path):
        pass


def _enroll_test_employee(db_session, employee, cipher) -> None:
    db_session.add(
        Consent(
            employee_id=employee.id,
            consented_at=datetime(2026, 11, 24, 8, 0, tzinfo=UTC),
            consent_text_version="1.0",
            granted=True,
        )
    )
    vector = np.zeros(128, dtype=np.float32)
    vector[0] = 1
    db_session.add(
        FaceEmbedding(
            employee_id=employee.id,
            embedding_data=cipher.encrypt(employee.id, vector),
            model_version=SFACE_MODEL_VERSION,
        )
    )
    db_session.commit()


def test_kiosk_runs_liveness_and_gesture_together_and_records_login(
    db_session, employee, kiosk
) -> None:
    cipher = FaceEmbeddingCipher(Fernet.generate_key().decode("ascii"))
    _enroll_test_employee(db_session, employee, cipher)
    events = []
    image_store = FakeImageStore()
    detector = FakeDetector()
    coordinator = KioskScanCoordinator(
        face_detector=detector,
        face_embedder=FakeEmbedder(),
        liveness_factory=lambda: FakeLiveness(events),
        gesture_factory=lambda: FakeGestures(events),
        image_store=image_store,
        embedding_cipher=cipher,
    )
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    scan_id, prompt = coordinator.start_scan(db_session, kiosk_id=kiosk.kiosk_id)
    assert "Blink once" in prompt
    assert "thumb to check in" in prompt
    assert "open palm to check out" in prompt

    first = coordinator.process_frame(db_session, scan_id=scan_id, frame=frame)
    assert first.state == "liveness"
    assert events == ["liveness", "gesture"]

    second = coordinator.process_frame(db_session, scan_id=scan_id, frame=frame)
    assert second.state == "gesture"
    assert events[-2:] == ["liveness", "gesture"]
    assert image_store.saved == 0

    completed = coordinator.process_frame(db_session, scan_id=scan_id, frame=frame)
    assert completed.state == "complete"
    assert completed.attendance is not None
    assert completed.attendance.image_path == "storage/images/1/2026-11-24.jpg"
    assert completed.operation == "login"
    assert completed.event_at == completed.attendance.last_login_at
    assert image_store.saved == 1
    assert events[-2:] == ["liveness_closed", "gesture_closed"]

    next_scan_id, _prompt = coordinator.start_scan(
        db_session, kiosk_id=kiosk.kiosk_id
    )
    repeated = coordinator.process_frame(db_session, scan_id=next_scan_id, frame=frame)
    assert repeated.state == "cooldown"
    assert image_store.saved == 1

    detector.faces = []
    departures = [
        coordinator.process_frame(db_session, scan_id=next_scan_id, frame=frame)
        for _ in range(3)
    ]
    assert departures[-1].state == "no_face"


def test_logout_uses_open_palm_without_saving_login_image(
    db_session, employee, kiosk, server_clock
) -> None:
    seed_store = FakeImageStore()
    record_login(
        db_session,
        employee_id=employee.id,
        kiosk_id=kiosk.kiosk_id,
        gesture="thumb",
        liveness_passed=True,
        image_frame=np.zeros((32, 32, 3), dtype=np.uint8),
        image_store=seed_store,
    )
    cipher = FaceEmbeddingCipher(Fernet.generate_key().decode("ascii"))
    _enroll_test_employee(db_session, employee, cipher)
    events = []
    logout_store = FakeImageStore()
    coordinator = KioskScanCoordinator(
        face_detector=FakeDetector(),
        face_embedder=FakeEmbedder(),
        liveness_factory=lambda: FakeLiveness(events, (True, True)),
        gesture_factory=lambda: FakeGestures(
            events, (HandGesture.OPEN_PALM, HandGesture.OPEN_PALM)
        ),
        image_store=logout_store,
        embedding_cipher=cipher,
    )
    scan_id, prompt = coordinator.start_scan(db_session, kiosk_id=kiosk.kiosk_id)
    assert "Blink once" in prompt
    first = coordinator.process_frame(
        db_session, scan_id=scan_id, frame=np.zeros((32, 32, 3))
    )
    assert first.state == "liveness"
    completed = coordinator.process_frame(
        db_session, scan_id=scan_id, frame=np.zeros((32, 32, 3))
    )

    assert completed.state == "complete"
    assert completed.attendance is not None
    assert completed.attendance.final_logout_at == server_clock[0]
    assert completed.operation == "logout"
    assert completed.event_at == server_clock[0]
    assert logout_store.saved == 0
