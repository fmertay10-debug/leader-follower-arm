"""ArUco wrapper: works with both OpenCV 4.6 (old API) and 4.7+ (new API)."""
import cv2

DICT_ID = cv2.aruco.DICT_4X4_50


def _dict():
    return cv2.aruco.getPredefinedDictionary(DICT_ID)


def make_marker(marker_id, px):
    d = _dict()
    if hasattr(cv2.aruco, "generateImageMarker"):
        return cv2.aruco.generateImageMarker(d, marker_id, px)
    return cv2.aruco.drawMarker(d, marker_id, px)


class MarkerDetector:
    def __init__(self):
        d = _dict()
        if hasattr(cv2.aruco, "ArucoDetector"):
            self._det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
            self._old = None
        else:
            self._det = None
            self._old = (d, cv2.aruco.DetectorParameters_create())

    def detect(self, gray):
        """Returns {id: 4x2 corner array}."""
        if self._det is not None:
            corners, ids, _ = self._det.detectMarkers(gray)
        else:
            corners, ids, _ = cv2.aruco.detectMarkers(gray, self._old[0], parameters=self._old[1])
        if ids is None:
            return {}
        return {int(i): c.reshape(4, 2) for i, c in zip(ids.ravel(), corners)}
