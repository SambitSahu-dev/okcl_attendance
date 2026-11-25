from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

import numpy as np

Frame: TypeAlias = np.ndarray
Embedding: TypeAlias = np.ndarray


@dataclass(frozen=True)
class FaceDetection:
    x: float
    y: float
    width: float
    height: float
    score: float
    landmarks: tuple[tuple[float, float], ...]
    model_output: tuple[float, ...]


@dataclass(frozen=True)
class BlinkChallenge:
    required_blinks: int

    @property
    def prompt(self) -> str:
        counts = {1: "once", 2: "twice", 3: "three times"}
        return f"Blink {counts[self.required_blinks]}"


@dataclass(frozen=True)
class LivenessResult:
    face_detected: bool
    eye_aspect_ratio: float | None
    completed_blinks: int
    required_blinks: int
    passed: bool
    prompt: str


class HandGesture(StrEnum):
    THUMB = "thumb"
    OPEN_PALM = "open_palm"
    OTHER = "other"


@dataclass(frozen=True)
class HandObservation:
    gesture: HandGesture
    handedness: str
    confidence: float


@dataclass(frozen=True)
class FaceEmbedding:
    detection: FaceDetection
    vector: Embedding


@dataclass(frozen=True)
class VisionObservation:
    faces: tuple[FaceEmbedding, ...]
    liveness: LivenessResult
    hands: tuple[HandObservation, ...]
