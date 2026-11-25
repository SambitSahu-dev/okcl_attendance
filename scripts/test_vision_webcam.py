import argparse
from pathlib import Path

import cv2

from vision.camera import OpenCVCamera
from vision.config import VisionConfig
from vision.mediapipe_gestures import MediaPipeHandGestureRecognizer
from vision.mediapipe_liveness import MediaPipeBlinkLiveness
from vision.opencv_face import OpenCVSFaceEmbedder, OpenCVYuNetDetector
from vision.pipeline import AttendanceVisionPipeline


def _arguments() -> argparse.Namespace:
    config = VisionConfig.from_env()
    parser = argparse.ArgumentParser(
        description="Show YuNet/SFace, blink challenge, and hand-gesture webcam results."
    )
    parser.add_argument("--camera-index", type=int, default=config.camera_index)
    parser.add_argument("--yunet-model", type=Path, default=config.yunet_model)
    parser.add_argument("--sface-model", type=Path, default=config.sface_model)
    parser.add_argument(
        "--face-score-threshold",
        type=float,
        default=config.face_score_threshold,
    )
    parser.add_argument(
        "--face-nms-threshold",
        type=float,
        default=config.face_nms_threshold,
    )
    parser.add_argument(
        "--max-num-hands",
        type=int,
        default=config.max_num_hands,
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    liveness = None
    gestures = None
    try:
        detector = OpenCVYuNetDetector(
            args.yunet_model,
            score_threshold=args.face_score_threshold,
            nms_threshold=args.face_nms_threshold,
        )
        embedder = OpenCVSFaceEmbedder(args.sface_model)
        liveness = MediaPipeBlinkLiveness()
        gestures = MediaPipeHandGestureRecognizer(max_num_hands=args.max_num_hands)
        camera = OpenCVCamera(args.camera_index)
    except (FileNotFoundError, RuntimeError, ImportError) as error:
        if liveness is not None:
            liveness.close()
        if gestures is not None:
            gestures.close()
        print(f"Vision setup failed: {error}")
        return 2

    pipeline = AttendanceVisionPipeline(
        face_detector=detector,
        face_embedder=embedder,
        liveness_checker=liveness,
        gesture_recognizer=gestures,
    )
    print(f"Challenge: {pipeline.liveness_prompt}. Press C for a new challenge; Q to quit.")
    previous_summary = None

    try:
        while True:
            success, frame = camera.read()
            if not success or frame is None:
                print("Webcam stopped returning frames.")
                break

            observation = pipeline.process(frame)
            for face in observation.faces:
                x, y = int(face.detection.x), int(face.detection.y)
                width, height = int(face.detection.width), int(face.detection.height)
                cv2.rectangle(frame, (x, y), (x + width, y + height), (0, 200, 0), 2)
                cv2.putText(
                    frame,
                    f"Face {face.detection.score:.2f}",
                    (x, max(y - 8, 16)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 200, 0),
                    2,
                )

            live = observation.liveness
            cv2.putText(
                frame,
                f"{live.prompt}: {live.completed_blinks}/{live.required_blinks} "
                f"Live={live.passed}",
                (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 200, 0) if live.passed else (0, 200, 255),
                2,
            )
            gestures_text = ", ".join(hand.gesture.value for hand in observation.hands)
            if gestures_text:
                cv2.putText(
                    frame,
                    f"Gesture: {gestures_text}",
                    (10, 58),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 180, 0),
                    2,
                )

            summary = (
                len(observation.faces),
                tuple(round(face.detection.score, 2) for face in observation.faces),
                live.face_detected,
                live.completed_blinks,
                live.required_blinks,
                tuple(hand.gesture.value for hand in observation.hands),
            )
            if summary != previous_summary:
                dimensions = (
                    len(observation.faces[0].vector)
                    if observation.faces
                    else 0
                )
                print(
                    f"faces={len(observation.faces)} embedding_dimensions={dimensions} "
                    f"liveness={live.completed_blinks}/{live.required_blinks} "
                    f"passed={live.passed} gestures={gestures_text or 'none'}"
                )
                previous_summary = summary

            cv2.imshow("Attendance vision test", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("c"):
                print(f"Challenge: {pipeline.new_liveness_challenge()}.")
    finally:
        camera.release()
        liveness.close()
        gestures.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
