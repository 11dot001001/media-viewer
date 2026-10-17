"""Эвристическая классификация: фото с камеры, скриншоты, видео, мессенджеры, прочее."""
from __future__ import annotations

import re

CATEGORIES = ("photo", "screenshot", "video", "messenger", "other")

_SCREEN_NAME = re.compile(
    r"screenshot|screen[ _-]?shot|скриншот|снимок экрана|screen[ _-]?record|screenrecord|"
    r"запись экрана|rpreplay|^scr[_-]|^scrn",
    re.I,
)
_MESSENGER_NAME = re.compile(
    r"-wa\d{3,}|whatsapp|telegram|^photo_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}|^video_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}|"
    r"viber|^received_\d+|^signal-\d{4}",
    re.I,
)
_MESSENGER_DIRS = {"whatsapp", "whatsapp images", "whatsapp video", "telegram", "telegram images",
                   "telegram video", "telegram desktop", "viber", "viber images", "signal", "vk", "vkontakte"}

# Частые разрешения экранов телефонов/мониторов (без учёта ориентации)
_SCREEN_SIZES = {
    (1080, 1920), (1080, 2340), (1080, 2400), (1080, 2408), (1080, 2316), (1080, 2280), (1080, 2160), (1080, 2220),
    (1170, 2532), (1179, 2556), (1284, 2778), (1290, 2796), (1125, 2436), (1242, 2688), (828, 1792), (750, 1334),
    (1242, 2208), (1440, 2560), (1440, 3200), (1440, 3088), (1440, 3120), (720, 1280), (720, 1600), (1206, 2622),
    (1320, 2868), (1080, 2412), (1220, 2712), (1260, 2800), (1344, 2992),
    (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1536, 864), (1440, 900), (1680, 1050), (1920, 1200),
    (2560, 1600), (2880, 1800), (3024, 1964), (3456, 2234), (2736, 1824), (2256, 1504),
}


def _is_screen_size(w: int | None, h: int | None) -> bool:
    if not w or not h:
        return False
    return (w, h) in _SCREEN_SIZES or (h, w) in _SCREEN_SIZES


def classify(rel_path: str, kind: str, ext: str, info: dict) -> str:
    parts = rel_path.lower().split("/")
    name = parts[-1]
    dirs = set(parts[:-1])
    camera = bool(info.get("make") or info.get("model"))

    if _SCREEN_NAME.search(name) or any("screenshot" in d or "скриншот" in d for d in dirs):
        return "screenshot"
    if info.get("screenshot_hint"):
        return "screenshot"
    if _MESSENGER_NAME.search(name) or dirs & _MESSENGER_DIRS:
        return "messenger"
    if kind == "video":
        return "video"
    if camera:
        return "photo"
    # PNG без камеры размером с экран — почти наверняка скриншот
    if ext == ".png" and _is_screen_size(info.get("width"), info.get("height")):
        return "screenshot"
    if info.get("ts_source") == "exif":
        return "photo"
    return "other"
