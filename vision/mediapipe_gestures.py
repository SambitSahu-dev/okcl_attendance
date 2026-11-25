import math
from collections.abc import Sequence

from vision.types import Frame, HandGesture, HandObservation


def _distance(first, second) -> float:
    return math.hypot(first.x - second.x, first.y - second.y)


def classify_hand(landmarks: Sequence[object]) -> HandGesture:
    wrist = landmarks[0]
    thumb_tip, thumb_ip, thumb_mcp = landmarks[4], landmarks[3], landmarks[2]
    finger_pairs = ((8, 6), (12, 10), (16, 14), (20, 18))
    fingers_extended = [landmarks[tip].y < landmarks[pip].y for tip, pip in finger_pairs]

    palm_width = _distance(landmarks[5], landmarks[17])
    thumb_open = _distance(thumb_tip, landmarks[5]) > max(0.06, palm_width * 0.65)
    if all(fingers_extended) and thumb_open:
        return HandGesture.OPEN_PALM

    fingers_folded = [landmarks[tip].y > landmarks[pip].y for tip, pip in finger_pairs]
    thumb_up = thumb_tip.y < thumb_ip.y < thumb_mcp.y < wrist.y
    if all(fingers_folded) and thumb_up:
        return HandGesture.THUMB
    return HandGesture.OTHER


class MediaPipeHandGestureRecognizer:
    def __init__(
        self,
        *,
        max_num_hands: int = 2,
        hand_tracker=None,
    ) -> None:
        self._hand_tracker = hand_tracker or self._create_hand_tracker(max_num_hands)

    @staticmethod
    def _create_hand_tracker(max_num_hands: int):
        import mediapipe as mp

        return mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=max_num_hands,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )

    def detect(self, frame: Frame) -> tuple[HandObservation, ...]:
        import cv2

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = self._hand_tracker.process(rgb_frame)
        landmarks = getattr(result, "multi_hand_landmarks", None) or ()
        handedness = getattr(result, "multi_handedness", None) or ()
        observations = []

        for index, hand in enumerate(landmarks):
            label = "unknown"
            confidence = 0.0
            if index < len(handedness):
                classification = handedness[index].classification[0]
                label = classification.label.lower()
                confidence = float(classification.score)
            observations.append(
                HandObservation(
                    gesture=classify_hand(hand.landmark),
                    handedness=label,
                    confidence=confidence,
                )
            )
        return tuple(observations)

    def close(self) -> None:
        self._hand_tracker.close()
