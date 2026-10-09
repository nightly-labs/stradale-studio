"""Local OCR and deterministic text replacement. Coordinates are source pixels."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).parent
cv2.setNumThreads(1)
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def decode(data):
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 24_000_000:
            raise ValueError("Use an image with no more than 24 million pixels.")
        return np.array(ImageOps.exif_transpose(source).convert("RGB"))


def detect(rgb, target):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.png"
        Image.fromarray(rgb).save(path)
        result = subprocess.run([os.environ.get("STRADALE_OCR", str(ROOT / "recognize")), str(path)],
                                capture_output=True, text=True, check=True, timeout=30)
    rows = json.loads(result.stdout)
    normalize = lambda value: "".join(c for c in value.upper() if c.isalnum())
    matches = [r for r in rows if normalize(r["text"]) == normalize(target)]
    if len(matches) != 1 or matches[0]["confidence"] < .5:
        return None
    row = max(matches, key=lambda r: r["confidence"])
    h, w = rgb.shape[:2]
    x, y, bw, bh = np.array(row["box"]) * [w, h, w, h]
    # Find the light plate face around the recognized text, not the whole car.
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    x0, y0 = max(0, int(x-bw*.4)), max(0, int(y-bh))
    x1, y1 = min(w, int(x+bw*1.4)), min(h, int(y+bh*2))
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0 or bw < 8 or bh < 3:
        return None
    _, binary = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        contour = contour + np.array([[[x0, y0]]])
        area = cv2.contourArea(contour)
        if area < bw*bh*1.05 or area > bw*bh*5:
            continue
        if cv2.pointPolygonTest(contour, (float(x+bw/2), float(y+bh/2)), False) < 0:
            continue
        poly = cv2.approxPolyDP(contour, .025*cv2.arcLength(contour, True), True)
        if len(poly) != 4:
            continue
        points = poly[:, 0, :].astype(float)
        # Plate is assumed to be upright, with moderate perspective.
        order = points[np.argsort(points[:, 1])]
        top = order[:2][np.argsort(order[:2, 0])]
        bottom = order[2:][np.argsort(order[2:, 0])]
        quad = np.array([top[0], top[1], bottom[1], bottom[0]])
        candidates.append((area, quad))
    if not candidates:
        return None
    quad = min(candidates, key=lambda item: item[0])[1]
    return {"corners": quad.tolist(), "text": row["text"], "confidence": row["confidence"]}


def render(rgb, corners, text):
    points = np.asarray(corners, dtype=np.float32)
    h, w = rgb.shape[:2]
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("Select four valid corners.")
    if (points < 0).any() or (points[:, 0] >= w).any() or (points[:, 1] >= h).any():
        raise ValueError("Keep all corners inside the image.")
    if not cv2.isContourConvex(points) or cv2.contourArea(points) < 30:
        raise ValueError("Select a flat area in clockwise order, starting at the top left.")
    text = text.strip()
    if not text or len(text) > 16 or any(not (c.isascii() and (c.isalnum() or c in ' -')) for c in text):
        raise ValueError("Use 1–16 letters, numbers, spaces, or hyphens.")
    left, top = np.maximum(0, np.floor(points.min(axis=0)).astype(int)-2)
    right, bottom = np.minimum([w, h], np.ceil(points.max(axis=0)).astype(int)+3)
    source = rgb
    rgb = source[top:bottom, left:right]
    points = points - np.array([left, top], dtype=np.float32)
    h, w = rgb.shape[:2]
    pw, ph = 840, 210
    rect = np.float32([[0, 0], [pw-1, 0], [pw-1, ph-1], [0, ph-1]])
    to_rect = cv2.getPerspectiveTransform(points, rect)
    original = cv2.warpPerspective(rgb, to_rect, (pw, ph))
    # Use the light pixels in each column to retain the plate's light gradient.
    background = np.percentile(original, 85, axis=0).astype(np.uint8)
    background = cv2.GaussianBlur(background[None, :, :], (101, 1), 0)
    plate = Image.fromarray(np.repeat(background, ph, axis=0))
    font = ImageFont.truetype(FONT, 140)
    box = font.getbbox(text)
    glyph = Image.new("L", (box[2]-box[0]+8, box[3]-box[1]+8))
    ImageDraw.Draw(glyph).text((4-box[0], 4-box[1]), text, font=font, fill=255)
    glyph = glyph.resize((int(pw*.89), int(ph*.72)), Image.Resampling.LANCZOS)
    plate.paste((25, 25, 25), ((pw-glyph.width)//2, (ph-glyph.height)//2), glyph)
    to_image = cv2.getPerspectiveTransform(rect, points)
    width = max(np.linalg.norm(points[1]-points[0]), np.linalg.norm(points[2]-points[3]))
    sigma = max(.1, pw / width * .32)
    filtered = cv2.GaussianBlur(np.array(plate), (0, 0), sigma)
    overlay = cv2.warpPerspective(filtered, to_image, (w, h), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), to_image, (w, h))
    # Never change a pixel outside the selected quadrilateral.
    polygon = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(polygon, np.round(points).astype(np.int32), 255)
    alpha = (np.minimum(mask, polygon).astype(float)/255)[..., None]
    output = source.copy()
    output[top:bottom, left:right] = np.rint(rgb*(1-alpha)+overlay*alpha).astype(np.uint8)
    return output
