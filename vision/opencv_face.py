from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

from vision.types import Embedding, FaceDetection, Frame


def _require_model(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Vision model not found: {path}")
    return str(path)


class OpenCVYuNetDetector:
    def __init__(
        self,
        model_path: Path,
        *,
        score_threshold: float = 0.8,
        nms_threshold: float = 0.3,
        top_k: int = 5000,
    ) -> None:
        self._detector = cv2.FaceDetectorYN.create(
            _require_model(model_path),
            "",
            (320, 320),
            score_threshold,
            nms_threshold,
            top_k,
        )

    def detect(self, frame: Frame) -> Sequence[FaceDetection]:
        if frame.ndim != 3 or frame.shape[0] == 0 or frame.shape[1] == 0:
            raise ValueError("Face detection requires a non-empty color frame.")

        height, width = frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, faces = self._detector.detect(frame)
        if faces is None:
            return ()

        detections = []
        for row in np.asarray(faces, dtype=np.float32):
            output = tuple(float(value) for value in row)
            landmarks = tuple(
                (output[index], output[index + 1])
                for index in range(4, 14, 2)
            )
            detections.append(
                FaceDetection(
                    x=output[0],
                    y=output[1],
                    width=output[2],
                    height=output[3],
                    score=output[14],
                    landmarks=landmarks,
                    model_output=output,
                )
            )
        return tuple(detections)


class OpenCVSFaceEmbedder:
    def __init__(self, model_path: Path) -> None:
        self._recognizer = cv2.FaceRecognizerSF.create(
            _require_model(model_path),
            "",
        )

    def embed(self, frame: Frame, detection: FaceDetection) -> Embedding:
        face_row = np.asarray([detection.model_output], dtype=np.float32)
        aligned_face = self._recognizer.alignCrop(frame, face_row)
        feature = np.asarray(self._recognizer.feature(aligned_face), dtype=np.float32)
        vector = feature.reshape(-1)
        magnitude = float(np.linalg.norm(vector))
        if magnitude == 0:
            raise ValueError("SFace returned an empty face embedding.")
        return vector / magnitude
