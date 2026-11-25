import math
from collections.abc import Sequence

from vision.types import BlinkChallenge, Frame, LivenessResult

LEFT_EYE_INDICES = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_INDICES = (362, 385, 387, 263, 373, 380)


def eye_aspect_ratio(
    landmarks: Sequence[object], indices: tuple[int, int, int, int, int, int]
) -> float:
    points = [(float(landmarks[index].x), float(landmarks[index].y)) for index in indices]
    horizontal = math.dist(points[0], points[3])
    if horizontal == 0:
        return 0.0
    return (
        math.dist(points[1], points[5]) + math.dist(points[2], points[4])
    ) / (2 * horizontal)


class MediaPipeBlinkLiveness:
    def __init__(
        self,
        *,
        close_threshold: float = 0.20,
        open_threshold: float = 0.24,
        face_mesh=None,
    ) -> None:
        if close_threshold >= open_threshold:
            raise ValueError("close_threshold must be less than open_threshold.")
        self._close_threshold = close_threshold
        self._open_threshold = open_threshold
        self._face_mesh = face_mesh or self._create_face_mesh()
        self._challenge = BlinkChallenge(required_blinks=1)
        self._completed_blinks = 0
        self._eyes_closed = False

    @staticmethod
    def _create_face_mesh():
        import mediapipe as mp

        return mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    @property
    def prompt(self) -> str:
        return self._challenge.prompt

    def new_challenge(self) -> str:
        self._challenge = BlinkChallenge(required_blinks=1)
        self._completed_blinks = 0
        self._eyes_closed = False
        return self.prompt

    def process(self, frame: Frame) -> LivenessResult:
        import cv2

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = self._face_mesh.process(rgb_frame)
        faces = getattr(result, "multi_face_landmarks", None)
        if not faces:
            return self._result(face_detected=False, ear=None)

        landmarks = faces[0].landmark
        left_ear = eye_aspect_ratio(landmarks, LEFT_EYE_INDICES)
        right_ear = eye_aspect_ratio(landmarks, RIGHT_EYE_INDICES)
        ear = (left_ear + right_ear) / 2
        return self.update_from_ear(ear)

    def update_from_ear(self, ear: float, *, face_detected: bool = True) -> LivenessResult:
        if face_detected:
            if ear <= self._close_threshold:
                self._eyes_closed = True
            elif ear >= self._open_threshold and self._eyes_closed:
                self._completed_blinks += 1
                self._eyes_closed = False
        return self._result(face_detected=face_detected, ear=ear if face_detected else None)

    def _result(self, *, face_detected: bool, ear: float | None) -> LivenessResult:
        return LivenessResult(
            face_detected=face_detected,
            eye_aspect_ratio=ear,
            completed_blinks=self._completed_blinks,
            required_blinks=self._challenge.required_blinks,
            passed=face_detected
            and self._completed_blinks >= self._challenge.required_blinks,
            prompt=self.prompt,
        )

    def close(self) -> None:
        self._face_mesh.close()
