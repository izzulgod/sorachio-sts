import base64

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from utils.logging_setup import get_logger

log = get_logger("vision.capture")


def capture_frame_base64(device_index: int = 0, max_size: int = 512) -> str | None:
    """
    Capture a single frame from the specified camera device, resize it if necessary,
    and return it as a Base64 encoded JPEG string.

    Returns None if the camera is unavailable or an error occurs.
    """
    if not HAS_CV2:
        log.warning("opencv-python is not installed. Vision features are disabled.")
        return None

    try:
        # Open the video capture device
        cap = cv2.VideoCapture(device_index)
        if not cap.isOpened():
            log.warning(f"Could not open camera device {device_index}")
            return None

        # Capture a single frame
        # Read a few frames to let the camera sensor adjust to light (warm-up)
        for _ in range(5):
            ret, frame = cap.read()

        # Release the camera immediately after grabbing the frame
        cap.release()

        if not ret or frame is None:
            log.warning("Failed to capture frame from camera.")
            return None

        # Resize the frame to save tokens and processing time
        h, w = frame.shape[:2]
        if max(h, w) > max_size:
            scale = max_size / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # Encode frame as JPEG
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
        success, buffer = cv2.imencode('.jpg', frame, encode_param)

        if not success:
            log.warning("Failed to encode frame to JPEG.")
            return None

        # Convert to Base64 string
        b64_str = base64.b64encode(buffer).decode('utf-8')
        log.debug(f"Captured frame successfully: {new_w}x{new_h}, {len(b64_str)} bytes (b64)")
        return b64_str

    except Exception as e:
        log.error(f"Error capturing frame: {e}")
        return None
