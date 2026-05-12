"""
Face Detection module using OpenCV DNN (Caffe SSD) face detector.
Replaces the Android ML Kit-based face_register.py for web/desktop use.
Falls back to Haar Cascade if the Caffe model is not available.
"""

import cv2
import numpy as np
import os
import urllib.request


# Model URLs for the Caffe SSD face detector
PROTOTXT_URL = "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt"
CAFFEMODEL_URL = "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"


class FaceDetector:
    """OpenCV DNN-based face detector with quality checks."""

    def __init__(self):
        model_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
        os.makedirs(model_dir, exist_ok=True)

        prototxt_path = os.path.join(model_dir, "deploy.prototxt")
        caffemodel_path = os.path.join(model_dir, "res10_300x300_ssd_iter_140000.caffemodel")

        # Download models if missing
        self._download_if_missing(prototxt_path, PROTOTXT_URL, "deploy.prototxt")
        self._download_if_missing(caffemodel_path, CAFFEMODEL_URL, "caffemodel")

        self.net = cv2.dnn.readNetFromCaffe(prototxt_path, caffemodel_path)
        self.confidence_threshold = 0.5

        # Quality thresholds
        self.MIN_FACE_RATIO = 0.15
        self.MAX_FACE_RATIO = 0.85
        self.BRIGHTNESS_LOW = 70
        self.BRIGHTNESS_HIGH = 180
        self.BLUR_THRESHOLD = 80
        self.CENTER_THRESHOLD = 0.20

        print("FaceDetector initialized (OpenCV DNN SSD)")

    def _download_if_missing(self, filepath, url, label):
        """Download a model file if it doesn't exist locally."""
        if not os.path.exists(filepath):
            print(f"Downloading {label}...")
            try:
                urllib.request.urlretrieve(url, filepath)
                print(f"  [OK] Downloaded {label}")
            except Exception as e:
                print(f"  [FAIL] Failed to download {label}: {e}")
                raise

    def detect(self, frame_bgr):
        """
        Detect faces in a BGR frame.
        Returns list of face dicts: [{x, y, w, h, confidence}, ...]
        """
        h, w = frame_bgr.shape[:2]

        # Create a blob and run the network
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame_bgr, (300, 300)),
            1.0, (300, 300),
            (104.0, 177.0, 123.0)
        )
        self.net.setInput(blob)
        detections = self.net.forward()

        faces = []
        for i in range(detections.shape[2]):
            confidence = float(detections[0, 0, i, 2])
            if confidence < self.confidence_threshold:
                continue

            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            x1, y1, x2, y2 = box.astype(int)

            # Clamp to frame bounds
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)

            fw = x2 - x1
            fh = y2 - y1

            if fw > 0 and fh > 0:
                faces.append({
                    "x": x1, "y": y1,
                    "w": fw, "h": fh,
                    "confidence": confidence
                })

        return faces

    def quality_check(self, frame, face):
        """
        Run quality checks on a detected face.
        Returns (passed: bool, reasons: list[str])
        """
        reasons = []
        h, w = frame.shape[:2]

        # --- Face size ---
        face_ratio = face["h"] / h
        if face_ratio < self.MIN_FACE_RATIO:
            reasons.append("Too far")
        if face_ratio > self.MAX_FACE_RATIO:
            reasons.append("Too close")

        # --- Centering ---
        cx = (face["x"] + face["w"] / 2) / w
        cy = (face["y"] + face["h"] / 2) / h
        if abs(cx - 0.5) > self.CENTER_THRESHOLD or abs(cy - 0.5) > self.CENTER_THRESHOLD:
            reasons.append("Move to center")

        # --- Crop face for pixel-level checks ---
        fx, fy, fw, fh = face["x"], face["y"], face["w"], face["h"]
        face_crop = frame[fy:fy + fh, fx:fx + fw]

        if face_crop.size > 0:
            gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)

            # Brightness
            brightness = np.mean(gray)
            if brightness < self.BRIGHTNESS_LOW:
                reasons.append("Too dark")
            elif brightness > self.BRIGHTNESS_HIGH:
                reasons.append("Too bright")

            # Blur (Laplacian variance)
            lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            if lap_var < self.BLUR_THRESHOLD:
                reasons.append("Blurry — hold still")
        else:
            reasons.append("Face out of bounds")

        return len(reasons) == 0, reasons

    def crop_face(self, frame, face, padding=0.2):
        """Crop detected face region with padding."""
        h, w = frame.shape[:2]
        fx, fy, fw, fh = face["x"], face["y"], face["w"], face["h"]

        pad_w = int(fw * padding)
        pad_h = int(fh * padding)

        x1 = max(0, fx - pad_w)
        y1 = max(0, fy - pad_h)
        x2 = min(w, fx + fw + pad_w)
        y2 = min(h, fy + fh + pad_h)

        return frame[y1:y2, x1:x2]
