from vision.types import Frame


class OpenCVCamera:
    def __init__(self, camera_index: int = 0) -> None:
        import cv2

        self._capture = cv2.VideoCapture(camera_index)
        if not self._capture.isOpened():
            self._capture.release()
            raise RuntimeError(f"Could not open webcam at index {camera_index}.")

    def read(self) -> tuple[bool, Frame | None]:
        success, frame = self._capture.read()
        return success, frame if success else None

    def release(self) -> None:
        self._capture.release()
