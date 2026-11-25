from functools import lru_cache

from app.config import get_settings
from app.services.biometrics import FaceEmbeddingCipher, FaceEmbeddingEncryptionError
from app.services.kiosk_scans import KioskScanCoordinator, KioskScanError
from app.services.login_images import FileSystemLoginImageStore, LoginImageStore
from vision.config import VisionConfig
from vision.mediapipe_gestures import MediaPipeHandGestureRecognizer
from vision.mediapipe_liveness import MediaPipeBlinkLiveness
from vision.opencv_face import OpenCVSFaceEmbedder, OpenCVYuNetDetector


@lru_cache(maxsize=1)
def get_face_components() -> tuple[OpenCVYuNetDetector, OpenCVSFaceEmbedder]:
    config = VisionConfig.from_env()
    return (
        OpenCVYuNetDetector(
            config.yunet_model,
            score_threshold=config.face_score_threshold,
            nms_threshold=config.face_nms_threshold,
        ),
        OpenCVSFaceEmbedder(config.sface_model),
    )


def get_face_embedding_cipher(*, required: bool = True) -> FaceEmbeddingCipher | None:
    key = get_settings().face_embedding_key
    if not key:
        if required:
            raise FaceEmbeddingEncryptionError(
                "Set FACE_EMBEDDING_KEY in .env before enrolling or scanning faces."
            )
        return None
    return FaceEmbeddingCipher(key)


def get_optional_face_embedding_cipher() -> FaceEmbeddingCipher | None:
    return get_face_embedding_cipher(required=False)


def get_login_image_store() -> LoginImageStore:
    return FileSystemLoginImageStore()


def get_face_detector() -> OpenCVYuNetDetector:
    return get_face_components()[0]


def get_face_embedder() -> OpenCVSFaceEmbedder:
    return get_face_components()[1]


@lru_cache(maxsize=1)
def get_kiosk_scan_coordinator() -> KioskScanCoordinator:
    config = VisionConfig.from_env()
    try:
        detector, embedder = get_face_components()
        cipher = get_face_embedding_cipher()
    except (FaceEmbeddingEncryptionError, FileNotFoundError) as error:
        raise KioskScanError(str(error), 503) from error
    assert cipher is not None

    return KioskScanCoordinator(
        face_detector=detector,
        face_embedder=embedder,
        liveness_factory=lambda: MediaPipeBlinkLiveness(),
        gesture_factory=lambda: MediaPipeHandGestureRecognizer(
            max_num_hands=config.max_num_hands
        ),
        image_store=get_login_image_store(),
        embedding_cipher=cipher,
    )
