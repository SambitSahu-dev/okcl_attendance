from collections.abc import Sequence
from typing import Protocol

from vision.types import (
    Embedding,
    FaceDetection,
    Frame,
    HandObservation,
    LivenessResult,
)


class FaceDetector(Protocol):
    def detect(self, frame: Frame) -> Sequence[FaceDetection]: ...


class FaceEmbedder(Protocol):
    def embed(self, frame: Frame, detection: FaceDetection) -> Embedding: ...


class BlinkLivenessChecker(Protocol):
    @property
    def prompt(self) -> str: ...

    def process(self, frame: Frame) -> LivenessResult: ...

    def new_challenge(self) -> str: ...

    def close(self) -> None: ...


class GestureRecognizer(Protocol):
    def detect(self, frame: Frame) -> Sequence[HandObservation]: ...

    def close(self) -> None: ...


class FrameSource(Protocol):
    def read(self) -> tuple[bool, Frame | None]: ...

    def release(self) -> None: ...
