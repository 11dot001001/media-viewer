"""Чтение медиафайлов: метаданные (EXIF/ffprobe), миниатюры, перцептивные хэши, резкость."""
from __future__ import annotations

import calendar
import datetime as dt
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except ImportError:  # pragma: no cover
    HEIF_OK = False

try:
    import rawpy
except ImportError:  # pragma: no cover
    rawpy = None

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
RAW_EXT = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".mts", ".m2ts", ".webm", ".wmv"}
ALL_EXT = IMAGE_EXT | RAW_EXT | VIDEO_EXT

# Что браузер умеет показать сам; остальное отдаём через JPEG-превью
BROWSER_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
BROWSER_VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mov"}

THUMB_SIZE = 400
ANALYSIS_SIZE = 1024
PREVIEW_SIZE = 2560

_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


def media_kind(ext: str) -> str | None:
    if ext in IMAGE_EXT or ext in RAW_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return None


# ---------------------------------------------------------------- даты

def ts_from_naive(d: dt.datetime) -> float:
    """Локальное «настенное» время -> число секунд (как будто это UTC). Так все даты сравнимы между собой."""
    return calendar.timegm(d.timetuple()) + d.microsecond / 1e6


def _valid(d: dt.datetime) -> bool:
    return 1990 <= d.year <= 2100


def parse_exif_datetime(value, subsec=None) -> float | None:
    if not value:
        return None
    m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", str(value).strip("\x00 "))
    if not m:
        return None
    try:
        d = dt.datetime(*map(int, m.groups()))
    except ValueError:
        return None
    if not _valid(d):
        return None
    ts = ts_from_naive(d)
    digits = re.sub(r"\D", "", str(subsec or ""))
    if digits:
        ts += float("0." + digits[:6])
    return ts


_NAME_DT = re.compile(r"(?<!\d)(20\d{2}|19\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_ T.]?(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})")
_NAME_D = re.compile(r"(?<!\d)(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")


def date_from_name(name: str) -> float | None:
    """IMG_20231012_153012, Screenshot_2023-10-12-15-30-12, photo_2023-10-12_15-30-12, IMG-20231012-WA0001…"""
    for rx in (_NAME_DT, _NAME_D):
        m = rx.search(name)
        if not m:
            continue
        parts = list(map(int, m.groups())) + [0, 0, 0]
        try:
            d = dt.datetime(*parts[:6])
        except ValueError:
            continue
        if _valid(d):
            return ts_from_naive(d)
    return None


def _parse_iso(value: str) -> float | None:
    """Дата из метаданных видео. Время с часовым поясом переводим в локальное."""
    if not value:
        return None
    s = value.strip().replace(" ", "T", 1)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)  # +0300 -> +03:00
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    return ts_from_naive(d) if _valid(d) else None


# ---------------------------------------------------------------- EXIF

def _clean(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    s = str(v).replace("\x00", "").strip()
    return s or None


def _gps(exif) -> tuple[float | None, float | None]:
    try:
        g = exif.get_ifd(0x8825)
    except Exception:
        return None, None
    if not g or 2 not in g or 4 not in g:
        return None, None

    def conv(v):
        d, m, s = (float(x) for x in v)
        return d + m / 60 + s / 3600

    try:
        lat, lon = conv(g[2]), conv(g[4])
    except (TypeError, ValueError, ZeroDivisionError):
        return None, None
    if not (np.isfinite(lat) and np.isfinite(lon)):
        return None, None
    if str(g.get(1, "N")).upper().startswith("S"):
        lat = -lat
    if str(g.get(3, "E")).upper().startswith("W"):
        lon = -lon
    if (abs(lat) < 1e-6 and abs(lon) < 1e-6) or abs(lat) > 90 or abs(lon) > 180:
        return None, None
    return lat, lon


def read_meta(img: Image.Image | None) -> dict:
    meta = {"make": None, "model": None, "software": None, "ts": None, "lat": None, "lon": None,
            "orientation": 1, "screenshot_hint": False, "has_exif": False}
    if img is None:
        return meta
    try:
        ex = img.getexif()
    except Exception:
        ex = None
    if ex:
        meta["has_exif"] = True
        meta["make"] = _clean(ex.get(271))
        meta["model"] = _clean(ex.get(272))
        meta["software"] = _clean(ex.get(305))
        try:
            meta["orientation"] = int(ex.get(274) or 1)
        except (TypeError, ValueError):
            pass
        try:
            sub = ex.get_ifd(0x8769)
        except Exception:
            sub = {}
        meta["ts"] = (parse_exif_datetime(sub.get(36867), sub.get(37521))
                      or parse_exif_datetime(sub.get(36868), sub.get(37522))
                      or parse_exif_datetime(ex.get(306), sub.get(37520)))
        comment = _clean(sub.get(37510)) or ""
        if "screenshot" in comment.lower():
            meta["screenshot_hint"] = True
        meta["lat"], meta["lon"] = _gps(ex)
    xmp = img.info.get("XML:com.adobe.xmp") or img.info.get("xmp") or ""
    if isinstance(xmp, bytes):
        xmp = xmp.decode("utf-8", "ignore")
    if "Screenshot" in xmp:
        meta["screenshot_hint"] = True
    return meta


# ---------------------------------------------------------------- открытие изображений

def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel("A"))
        return bg
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        a = np.asarray(img, dtype=np.float32)
        a = (a / max(a.max(), 1) * 255).astype(np.uint8)
        return Image.fromarray(a).convert("RGB")
    return img.convert("RGB") if img.mode != "RGB" else img


def _open_raw(path: Path) -> tuple[Image.Image, Image.Image | None, tuple[int, int] | None]:
    """RAW: картинка из встроенного JPEG-превью (быстро), EXIF — через Pillow (TIFF-структура)."""
    meta_img = None
    try:
        meta_img = Image.open(path)
    except Exception:
        pass
    if rawpy is None:
        raise RuntimeError("rawpy не установлен — RAW не поддерживается")
    with rawpy.imread(str(path)) as raw:
        size = (raw.sizes.width, raw.sizes.height)
        img = None
        try:
            th = raw.extract_thumb()
            if th.format == rawpy.ThumbFormat.JPEG:
                img = Image.open(io.BytesIO(th.data))
                img.load()
            elif th.format == rawpy.ThumbFormat.BITMAP:
                img = Image.fromarray(th.data)
        except Exception:
            img = None
        if img is None:
            img = Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True))
    return img, meta_img, size


def open_oriented(path: Path, ext: str, max_side: int, fast: bool = False) -> tuple[Image.Image, dict, tuple[int, int]]:
    """Открыть, повернуть по EXIF, уменьшить до max_side. Возвращает (RGB, мета, исходный размер с учётом поворота).

    fast: уменьшать грубее, но в 2–3 раза быстрее (для анализа, не для показа).
    """
    if ext in RAW_EXT:
        img, meta_img, size = _open_raw(path)
        meta = read_meta(meta_img or img)
        if meta_img is not None:
            meta_img.close()
    else:
        img = Image.open(path)
        meta = read_meta(img)
        size = img.size
        img.draft("RGB", (max_side, max_side))  # ускоряет декодирование JPEG в разы
    orient = 1 if ext in (".heic", ".heif", ".avif") else meta["orientation"]  # libheif уже повернул
    if orient in (5, 6, 7, 8):
        size = (size[1], size[0])
    img = _to_rgb(img)
    if orient in _TRANSPOSE:
        img = img.transpose(_TRANSPOSE[orient])
    if max(img.size) > max_side:
        if fast:
            img.thumbnail((max_side, max_side), Image.Resampling.BILINEAR, reducing_gap=2.0)
        else:
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return img, meta, size


# ---------------------------------------------------------------- хэши и резкость

def _dct_matrix(n: int = 32) -> np.ndarray:
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n)) * np.sqrt(2 / n)
    m[0] /= np.sqrt(2)
    return m


_DCT32 = _dct_matrix(32)


def _bits_to_int(bits: np.ndarray) -> int:
    v = 0
    for b in bits.ravel():
        v = (v << 1) | int(b)
    return v - (1 << 64) if v >= (1 << 63) else v  # хранится как signed int64 в SQLite


def phash(gray: Image.Image) -> int:
    a = np.asarray(gray.resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_DCT32 @ a @ _DCT32.T)[:8, :8].ravel()
    return _bits_to_int(low > np.median(low[1:]))


def dhash(gray: Image.Image) -> int:
    a = np.asarray(gray.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    return _bits_to_int(a[:, 1:] > a[:, :-1])


def sharpness(gray: Image.Image) -> float:
    """Дисперсия лапласиана: чем больше, тем резче. Считается на одинаковом масштабе (ANALYSIS_SIZE)."""
    a = np.asarray(gray, dtype=np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    lap = -4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
    h, w = lap.shape
    center = lap[h // 5: h - h // 5, w // 5: w - w // 5]  # центр кадра обычно важнее фона
    return float(0.5 * lap.var() + 0.5 * center.var())


def _jpeg(img: Image.Image, quality: int = 82) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- анализ файлов

def analyze_image(path: Path, ext: str) -> dict:
    img, meta, (w, h) = open_oriented(path, ext, ANALYSIS_SIZE)
    gray = img.convert("L")
    thumb = img.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    return {
        "width": w, "height": h,
        "ts": meta["ts"], "make": meta["make"], "model": meta["model"], "software": meta["software"],
        "lat": meta["lat"], "lon": meta["lon"], "duration": None,
        "phash": phash(gray), "dhash": dhash(gray), "sharpness": sharpness(gray),
        "screenshot_hint": meta["screenshot_hint"], "has_exif": meta["has_exif"],
        "thumb": _jpeg(thumb),
    }


def _ffprobe(path: Path) -> dict:
    if not FFPROBE:
        return {}
    r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, timeout=60, creationflags=NO_WINDOW)
    try:
        return json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
    except json.JSONDecodeError:
        return {}


def video_frame(path: Path, at: float, size: int = THUMB_SIZE) -> Image.Image | None:
    if not FFMPEG:
        return None
    for t in (at, 0.0):
        r = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                            "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", "pipe:1"],
                           capture_output=True, timeout=60, creationflags=NO_WINDOW)
        if r.stdout:
            try:
                img = Image.open(io.BytesIO(r.stdout))
                img.load()
                return img.convert("RGB")
            except Exception:
                pass
    return None


def analyze_video(path: Path) -> dict:
    data = _ffprobe(path)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"), {})
    tags = {}
    for src in (vs.get("tags", {}), fmt.get("tags", {})):
        tags.update({k.lower(): v for k, v in src.items()})

    w, h = vs.get("width"), vs.get("height")
    rot = tags.get("rotate")
    for sd in vs.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = sd["rotation"]
    try:
        if w and h and abs(int(float(rot or 0))) % 180 == 90:
            w, h = h, w
    except ValueError:
        pass
    try:
        duration = float(fmt.get("duration") or vs.get("duration") or 0) or None
    except ValueError:
        duration = None

    ts = _parse_iso(tags.get("com.apple.quicktime.creationdate", "")) or _parse_iso(tags.get("creation_time", ""))
    lat = lon = None
    loc = tags.get("com.apple.quicktime.location.iso6709") or tags.get("location") or tags.get("location-eng")
    if loc:
        m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", loc)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if abs(lat) < 1e-6 and abs(lon) < 1e-6:
                lat = lon = None

    res = {
        "width": w, "height": h, "ts": ts,
        "make": tags.get("com.apple.quicktime.make") or tags.get("com.android.manufacturer"),
        "model": tags.get("com.apple.quicktime.model") or tags.get("com.android.model"),
        "software": tags.get("com.apple.quicktime.software") or tags.get("encoder"),
        "lat": lat, "lon": lon, "duration": duration,
        "phash": None, "dhash": None, "sharpness": None,
        "screenshot_hint": False, "has_exif": bool(ts),
        "thumb": None,
    }
    frame = video_frame(path, min(1.0, (duration or 0) * 0.25))
    if frame is not None:
        gray = frame.convert("L")
        res["phash"], res["dhash"] = phash(gray), dhash(gray)
        res["thumb"] = _jpeg(frame)
    return res


def make_preview(path: Path, ext: str, dest: Path) -> None:
    """JPEG для форматов, которые браузер не показывает (HEIC, RAW, TIFF)."""
    img, _, _ = open_oriented(path, ext, PREVIEW_SIZE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    img.save(tmp, "JPEG", quality=88)
    tmp.replace(dest)
