"""Генерирует папку с тестовыми фото/видео: серии, дубликаты, скриншоты, мессенджеры, GPS, Live Photo, RAW+JPG.

    python tools/make_test_data.py [папка]   (по умолчанию ./sample_media)
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from PIL.TiffImagePlugin import IFDRational

W, H = 2400, 1600


def scene(seed: int, shift=(0, 0), blur=0.0, size=(W, H)) -> Image.Image:
    rng = np.random.default_rng(seed)
    w, h = size
    top, bottom = rng.integers(40, 220, 3), rng.integers(40, 220, 3)
    t = np.linspace(0, 1, h)[:, None, None]
    grad = (top * (1 - t) + bottom * t).astype(np.uint8)
    img = Image.fromarray(np.repeat(grad, w, axis=1))
    d = ImageDraw.Draw(img)
    for _ in range(14):
        x, y = rng.integers(-200, w), rng.integers(-200, h)
        r = rng.integers(60, 420)
        col = tuple(int(c) for c in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            d.ellipse([x, y, x + r, y + r], fill=col)
        else:
            d.rectangle([x, y, x + r, y + r * 0.7], fill=col)
    for i in range(0, w, 24):  # мелкая текстура, чтобы было что считать резкостью
        d.line([(i, h * 0.75), (i + 12, h)], fill=(30, 30, 30), width=2)
    img = img.transform(img.size, Image.Transform.AFFINE, (1, 0, -shift[0], 0, 1, -shift[1]), fillcolor=(0, 0, 0))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    return img


def _dms(v: float):
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    s = round(((v - d) * 60 - m) * 60, 2)
    return (IFDRational(d, 1), IFDRational(m, 1), IFDRational(int(s * 100), 100))


def exif(ts: dt.datetime, make="Canon", model="Canon EOS R6", gps=None):
    ex = Image.Exif()
    ex[271], ex[272] = make, model
    ex[306] = ts.strftime("%Y:%m:%d %H:%M:%S")
    sub = ex.get_ifd(0x8769)
    sub[36867] = ts.strftime("%Y:%m:%d %H:%M:%S")
    sub[37521] = f"{ts.microsecond // 10000:02d}"
    if gps:
        g = ex.get_ifd(0x8825)
        g[1], g[2] = ("N" if gps[0] >= 0 else "S"), _dms(gps[0])
        g[3], g[4] = ("E" if gps[1] >= 0 else "W"), _dms(gps[1])
    return ex


def save_photo(path: Path, img: Image.Image, ts, gps=None, make="Canon", model="Canon EOS R6"):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=90, exif=exif(ts, make, model, gps))


def video(path: Path, ts: dt.datetime, gps=None, seconds=3, color="testsrc"):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = ["-metadata", f"creation_time={ts.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000000Z')}"]
    if gps:
        meta += ["-metadata", f"location={gps[0]:+.4f}{gps[1]:+.4f}/"]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{color}=size=1280x720:rate=30",
                    "-t", str(seconds), "-pix_fmt", "yuv420p", "-c:v", "libx264", *meta, str(path)], check=True)


MOSCOW = (55.7539, 37.6208)
SPB = (59.9398, 30.3146)
SOCHI = (43.5855, 39.7231)
KAZAN = (55.7963, 49.1088)
ALTAI = (50.8270, 87.6080)


def main(out: Path):
    if out.exists():
        shutil.rmtree(out)
    cam = out / "DCIM" / "Camera"
    base = dt.datetime(2024, 7, 14, 12, 0, 0)

    # Серия 1: 6 кадров серийной съёмки (0.3 с), резкость разная, самый резкий — 4-й
    blurs = [3.0, 2.2, 1.4, 0.0, 1.8, 2.6]
    for i, b in enumerate(blurs):
        save_photo(cam / f"IMG_{1001 + i}.JPG", scene(1, shift=(i * 6, i * 2), blur=b),
                   base + dt.timedelta(seconds=0.3 * i), MOSCOW)

    # Серия 2: 4 кадра с паузой 4 с, iPhone, Петербург
    t2 = dt.datetime(2024, 7, 20, 18, 30, 0)
    for i, b in enumerate([1.2, 0.0, 0.6, 2.0]):
        save_photo(cam / f"IMG_{2001 + i}.JPG", scene(2, shift=(i * 25, -i * 10), blur=b),
                   t2 + dt.timedelta(seconds=4 * i), SPB, "Apple", "iPhone 15 Pro")

    # Серия 3: 3 кадра, Сочи
    t3 = dt.datetime(2024, 8, 3, 9, 15, 0)
    for i, b in enumerate([0.5, 0.0, 1.5]):
        save_photo(cam / f"IMG_{3001 + i}.JPG", scene(3, shift=(-i * 15, 0), blur=b),
                   t3 + dt.timedelta(seconds=1.5 * i), SOCHI, "Apple", "iPhone 15 Pro")

    # Одиночные кадры по разным дням и местам
    singles = [
        (10, dt.datetime(2024, 7, 14, 15, 0), MOSCOW), (11, dt.datetime(2024, 7, 15, 10, 0), MOSCOW),
        (12, dt.datetime(2024, 7, 20, 20, 0), SPB), (13, dt.datetime(2024, 7, 21, 11, 0), SPB),
        (14, dt.datetime(2024, 8, 3, 13, 0), SOCHI), (15, dt.datetime(2024, 8, 4, 19, 0), SOCHI),
        (16, dt.datetime(2024, 9, 1, 12, 0), KAZAN), (17, dt.datetime(2024, 9, 10, 8, 0), ALTAI),
        (18, dt.datetime(2024, 9, 12, 16, 0), None), (19, dt.datetime(2024, 9, 12, 16, 40), None),
    ]
    for seed, ts, gps in singles:
        save_photo(cam / f"IMG_{4000 + seed}.JPG", scene(seed), ts, gps, "Apple", "iPhone 15 Pro")

    # Точная копия в другой папке
    (out / "Backup").mkdir(parents=True)
    shutil.copy2(cam / "IMG_4010.JPG", out / "Backup" / "IMG_4010.JPG")
    shutil.copy2(cam / "IMG_4014.JPG", out / "Backup" / "IMG_4014 (1).JPG")

    # Почти дубликат: уменьшенная копия без EXIF, «из WhatsApp»
    wa = out / "WhatsApp" / "WhatsApp Images"
    wa.mkdir(parents=True)
    scene(12).resize((1600, 1067)).save(wa / "IMG-20240721-WA0003.jpg", quality=70)
    scene(40).resize((1280, 853)).save(wa / "IMG-20240722-WA0007.jpg", quality=70)

    # Telegram
    tg = out / "Telegram"
    tg.mkdir(parents=True)
    scene(41, size=(1280, 960)).save(tg / "photo_2024-08-05_14-22-10.jpg", quality=80)

    # Скриншоты
    shots = out / "Pictures" / "Screenshots"
    shots.mkdir(parents=True)
    scene(50, size=(1080, 2400)).save(shots / "Screenshot_20240716-101010.png")
    scene(51, size=(1080, 2400)).save(shots / "Screenshot_20240716-101530.png")
    scene(52, size=(1179, 2556)).save(cam / "IMG_0420.PNG")  # iPhone: имя без подсказки, ловится по размеру экрана
    scene(53, size=(1920, 1080)).save(out / "Pictures" / "Снимок экрана 2024-09-02 101112.png")

    # Прочее: сохранённая картинка без EXIF
    scene(60, size=(800, 600)).save(out / "Pictures" / "meme.png")

    # Видео с датой и местом
    video(cam / "VID_20240714_130000.mp4", dt.datetime(2024, 7, 14, 13, 0).astimezone(), MOSCOW)
    video(cam / "VID_20240803_100000.mp4", dt.datetime(2024, 8, 3, 10, 0).astimezone(), SOCHI, color="mandelbrot")
    video(shots / "Screenrecorder-2024-07-16-10-20-00.mp4", dt.datetime(2024, 7, 16, 10, 20).astimezone(), seconds=2,
          color="smptebars")

    # Live Photo: HEIC/JPG + короткое MOV с тем же именем
    save_photo(cam / "IMG_5000.JPG", scene(70), dt.datetime(2024, 8, 4, 12, 0), SOCHI, "Apple", "iPhone 15 Pro")
    video(cam / "IMG_5000.MOV", dt.datetime(2024, 8, 4, 12, 0).astimezone(), SOCHI, seconds=2, color="rgbtestsrc")

    # RAW+JPG с фотоаппарата (RAW здесь — заглушка, программа показывает его как пару)
    nikon = out / "DCIM" / "100NIKON"
    save_photo(nikon / "DSC_0001.JPG", scene(80), dt.datetime(2024, 9, 1, 15, 0), KAZAN, "NIKON CORPORATION", "NIKON Z 6")
    (nikon / "DSC_0001.NEF").write_bytes(b"\0" * 1024 * 300)

    n = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"Готово: {n} файлов в {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "sample_media")
