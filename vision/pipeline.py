from vision.interfaces import (
    BlinkLivenessChecker,
    FaceDetector,
    FaceEmbedder,
    GestureRecognizer,
)
from vision.types import (
    FaceEmbedding,
    Frame,
    HandObservation,
    LivenessResult,
    VisionObservation,
)


class AttendanceVisionPipeline:
    def __init__(
        self,
        *,
        face_detector: FaceDetector,
        face_embedder: FaceEmbedder,
        liveness_checker: BlinkLivenessChecker,
        gesture_recognizer: GestureRecognizer,
    ) -> None:
        self._face_detector = face_detector
        self._face_embedder = face_embedder
        self._liveness_checker = liveness_checker
        self._gesture_recognizer = gesture_recognizer

    @property
    def liveness_prompt(self) -> str:
        return self._liveness_checker.prompt

    def new_liveness_challenge(self) -> str:
        return self._liveness_checker.new_challenge()

    def detect_and_embed(self, frame: Frame) -> tuple[FaceEmbedding, ...]:
        return tuple(
            FaceEmbedding(detection, self._face_embedder.embed(frame, detection))
            for detection in self._face_detector.detect(frame)
        )

    def check_liveness(self, frame: Frame) -> LivenessResult:
        return self._liveness_checker.process(frame)

    def detect_gestures(self, frame: Frame) -> tuple[HandObservation, ...]:
        return tuple(self._gesture_recognizer.detect(frame))

    def process(self, frame: Frame) -> VisionObservation:
        faces = self.detect_and_embed(frame)
        liveness = self.check_liveness(frame)
        hands = self.detect_gestures(frame)
        return VisionObservation(faces=faces, liveness=liveness, hands=hands)

    def close(self) -> None:
        self._liveness_checker.close()
        self._gesture_recognizer.close()
