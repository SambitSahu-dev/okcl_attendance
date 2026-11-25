from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest

from vision.camera import OpenCVCamera
from vision.mediapipe_gestures import (
    MediaPipeHandGestureRecognizer,
    classify_hand,
)
from vision.mediapipe_liveness import (
    LEFT_EYE_INDICES,
    MediaPipeBlinkLiveness,
    eye_aspect_ratio,
)
from vision.opencv_face import OpenCVSFaceEmbedder, OpenCVYuNetDetector
from vision.pipeline import AttendanceVisionPipeline
from vision.types import (
    FaceDetection,
    HandGesture,
    HandObservation,
    LivenessResult,
)


class FakeFaceMesh:
    def __init__(self, result=None) -> None:
        self.result = result or SimpleNamespace(multi_face_landmarks=None)

    def process(self, _frame):
        return self.result

    def close(self) -> None:
        pass


def test_single_blink_challenge_counts_closed_then_open_transitions() -> None:
    checker = MediaPipeBlinkLiveness(face_mesh=FakeFaceMesh())

    assert checker.prompt == "Blink once"
    assert not checker.update_from_ear(0.30).passed
    assert checker.update_from_ear(0.10).completed_blinks == 0
    assert checker.update_from_ear(0.12).completed_blinks == 0
    result = checker.update_from_ear(0.30)
    assert result.passed
    assert result.completed_blinks == 1

    assert checker.new_challenge() == "Blink once"
    assert checker.update_from_ear(0.10).completed_blinks == 0
    assert checker.update_from_ear(0.30).passed


def test_no_face_does_not_pass_blink_challenge() -> None:
    checker = MediaPipeBlinkLiveness(face_mesh=FakeFaceMesh())

    result = checker.update_from_ear(0.1, face_detected=False)
    assert not result.face_detected
    assert result.eye_aspect_ratio is None
    assert not result.passed


def test_eye_aspect_ratio_uses_eye_landmark_distances() -> None:
    landmarks = [SimpleNamespace(x=0.0, y=0.0) for _ in range(468)]
    points = {
        LEFT_EYE_INDICES[0]: (0.0, 0.5),
        LEFT_EYE_INDICES[1]: (0.25, 0.25),
        LEFT_EYE_INDICES[2]: (0.75, 0.25),
        LEFT_EYE_INDICES[3]: (1.0, 0.5),
        LEFT_EYE_INDICES[4]: (0.75, 0.75),
        LEFT_EYE_INDICES[5]: (0.25, 0.75),
    }
    for index, (x, y) in points.items():
        landmarks[index] = SimpleNamespace(x=x, y=y)

    assert eye_aspect_ratio(landmarks, LEFT_EYE_INDICES) == pytest.approx(0.5)


def test_face_mesh_adapter_extracts_landmarks_and_updates_blink_state() -> None:
    landmarks = [SimpleNamespace(x=0.0, y=0.0) for _ in range(468)]
    for indices, start in ((LEFT_EYE_INDICES, 0.1), ((362, 385, 387, 263, 373, 380), 0.5)):
        values = (
            (start, 0.5),
            (start + 0.05, 0.4),
            (start + 0.1, 0.4),
            (start + 0.2, 0.5),
            (start + 0.1, 0.6),
            (start + 0.05, 0.6),
        )
        for index, (x, y) in zip(indices, values, strict=True):
            landmarks[index] = SimpleNamespace(x=x, y=y)
    mesh = FakeFaceMesh(
        SimpleNamespace(multi_face_landmarks=[SimpleNamespace(landmark=landmarks)])
    )
    checker = MediaPipeBlinkLiveness(face_mesh=mesh)

    result = checker.process(np.zeros((10, 10, 3), dtype=np.uint8))

    assert result.face_detected
    assert result.eye_aspect_ratio == pytest.approx(1.0)
    assert not result.passed


def _hand(*, thumb_up: bool, open_palm: bool):
    landmarks = [SimpleNamespace(x=0.5, y=0.5) for _ in range(21)]
    landmarks[0] = SimpleNamespace(x=0.50, y=0.90)
    landmarks[2] = SimpleNamespace(x=0.39, y=0.56)
    landmarks[3] = SimpleNamespace(x=0.34, y=0.46)
    landmarks[4] = SimpleNamespace(
        x=0.30 if open_palm else 0.34,
        y=0.34 if thumb_up or open_palm else 0.58,
    )
    landmarks[5] = SimpleNamespace(x=0.40, y=0.57)
    landmarks[17] = SimpleNamespace(x=0.60, y=0.57)

    finger_points = ((8, 6), (12, 10), (16, 14), (20, 18))
    for offset, (tip, pip) in enumerate(finger_points):
        x = 0.40 + offset * 0.05
        if open_palm:
            landmarks[pip] = SimpleNamespace(x=x, y=0.45)
            landmarks[tip] = SimpleNamespace(x=x, y=0.30)
        else:
            landmarks[pip] = SimpleNamespace(x=x, y=0.50)
            landmarks[tip] = SimpleNamespace(x=x, y=0.68)
    return landmarks


def test_hand_classifier_distinguishes_thumb_open_palm_and_other() -> None:
    assert classify_hand(_hand(thumb_up=True, open_palm=False)) == HandGesture.THUMB
    assert classify_hand(_hand(thumb_up=False, open_palm=True)) == HandGesture.OPEN_PALM
    assert classify_hand(_hand(thumb_up=False, open_palm=False)) == HandGesture.OTHER


def test_mediapipe_hand_adapter_returns_classification_and_confidence() -> None:
    result = SimpleNamespace(
        multi_hand_landmarks=[SimpleNamespace(landmark=_hand(thumb_up=True, open_palm=False))],
        multi_handedness=[
            SimpleNamespace(
                classification=[SimpleNamespace(label="Right", score=0.98)]
            )
        ],
    )

    class FakeTracker:
        def process(self, _frame):
            return result

        def close(self):
            pass

    recognizer = MediaPipeHandGestureRecognizer(hand_tracker=FakeTracker())
    observations = recognizer.detect(np.zeros((10, 10, 3), dtype=np.uint8))

    assert observations == (HandObservation(HandGesture.THUMB, "right", 0.98),)


def test_opencv_yunet_adapter_parses_detections_and_resizes_input(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = np.asarray(
        [[10, 20, 30, 40, 11, 21, 12, 22, 13, 23, 14, 24, 15, 25, 0.95]],
        dtype=np.float32,
    )

    class FakeYuNet:
        input_size = None

        def setInputSize(self, size):
            self.input_size = size

        def detect(self, _frame):
            return 1, row

    detector_model = FakeYuNet()
    monkeypatch.setattr(
        "vision.opencv_face.cv2.FaceDetectorYN.create",
        lambda *_args: detector_model,
    )
    model_path = tmp_path / "yunet.onnx"
    model_path.touch()
    detector = OpenCVYuNetDetector(model_path)

    detections = detector.detect(np.zeros((80, 120, 3), dtype=np.uint8))

    assert detector_model.input_size == (120, 80)
    assert detections[0].x == 10
    assert detections[0].score == pytest.approx(0.95)
    assert len(detections[0].landmarks) == 5


def test_opencv_sface_adapter_aligns_and_normalizes_embedding(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSFace:
        def alignCrop(self, frame, detection):
            assert frame.shape == (10, 10, 3)
            assert detection.shape == (1, 15)
            return "aligned-face"

        def feature(self, aligned_face):
            assert aligned_face == "aligned-face"
            return np.asarray([[3.0, 4.0]], dtype=np.float32)

    monkeypatch.setattr(
        "vision.opencv_face.cv2.FaceRecognizerSF.create",
        lambda *_args: FakeSFace(),
    )
    model_path = tmp_path / "sface.onnx"
    model_path.touch()
    embedder = OpenCVSFaceEmbedder(model_path)
    detection = FaceDetection(1, 2, 3, 4, 0.99, (), tuple(range(15)))

    embedding = embedder.embed(np.zeros((10, 10, 3), dtype=np.uint8), detection)

    assert np.allclose(embedding, np.asarray([0.6, 0.8], dtype=np.float32))


def test_camera_adapter_is_mockable_and_releases_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = np.zeros((4, 4, 3), dtype=np.uint8)

    class FakeCapture:
        released = False

        def isOpened(self):
            return True

        def read(self):
            return True, frame

        def release(self):
            self.released = True

    capture = FakeCapture()
    monkeypatch.setattr("cv2.VideoCapture", lambda _index: capture)
    camera = OpenCVCamera(2)

    success, result = camera.read()
    camera.release()

    assert success and result is frame
    assert capture.released


@dataclass
class FakeDetector:
    detection: FaceDetection

    def detect(self, frame):
        self.frame = frame
        return [self.detection]


@dataclass
class FakeEmbedder:
    vector: np.ndarray

    def embed(self, frame, detection):
        self.frame = frame
        self.detection = detection
        return self.vector


class FakeLiveness:
    prompt = "Blink once"

    def process(self, frame):
        self.frame = frame
        return LivenessResult(True, 0.3, 1, 1, True, self.prompt)

    def new_challenge(self):
        return "Blink once"


class FakeGestureRecognizer:
    def detect(self, frame):
        self.frame = frame
        return [HandObservation(HandGesture.THUMB, "right", 0.99)]


def test_pipeline_uses_mockable_components() -> None:
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    detection = FaceDetection(
        x=1,
        y=2,
        width=3,
        height=4,
        score=0.99,
        landmarks=(),
        model_output=(),
    )
    vector = np.array([0.6, 0.8], dtype=np.float32)
    detector = FakeDetector(detection)
    embedder = FakeEmbedder(vector)
    liveness = FakeLiveness()
    gestures = FakeGestureRecognizer()
    pipeline = AttendanceVisionPipeline(
        face_detector=detector,
        face_embedder=embedder,
        liveness_checker=liveness,
        gesture_recognizer=gestures,
    )

    result = pipeline.process(frame)

    assert pipeline.liveness_prompt == "Blink once"
    assert pipeline.new_liveness_challenge() == "Blink once"
    assert result.faces[0].detection is detection
    assert np.array_equal(result.faces[0].vector, vector)
    assert result.liveness.passed
    assert result.hands[0].gesture == HandGesture.THUMB
    assert detector.frame is embedder.frame is liveness.frame is gestures.frame is frame
