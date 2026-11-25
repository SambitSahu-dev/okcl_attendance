import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_DIR = PROJECT_ROOT / "models" / "vision"


@dataclass(frozen=True)
class VisionConfig:
    yunet_model: Path
    sface_model: Path
    camera_index: int = 0
    face_score_threshold: float = 0.8
    face_nms_threshold: float = 0.3
    max_num_hands: int = 2

    @classmethod
    def from_env(cls) -> "VisionConfig":
        file_values = dotenv_values(PROJECT_ROOT / ".env")

        def configured(name: str, default: str) -> str:
            return os.environ.get(name, file_values.get(name) or default)

        return cls(
            yunet_model=Path(
                configured(
                    "VISION_YUNET_MODEL",
                    str(DEFAULT_MODEL_DIR / "face_detection_yunet_2023mar.onnx"),
                )
            ),
            sface_model=Path(
                configured(
                    "VISION_SFACE_MODEL",
                    str(DEFAULT_MODEL_DIR / "face_recognition_sface_2021dec.onnx"),
                )
            ),
            camera_index=int(configured("VISION_CAMERA_INDEX", "0")),
            face_score_threshold=float(configured("VISION_FACE_SCORE_THRESHOLD", "0.8")),
            face_nms_threshold=float(configured("VISION_FACE_NMS_THRESHOLD", "0.3")),
            max_num_hands=int(configured("VISION_MAX_NUM_HANDS", "2")),
        )
