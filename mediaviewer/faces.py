"""Лица на снимке: поиск (YuNet) и точки лица (MediaPipe Face Mesh) — чтобы заметить закрытые глаза и смаз лица.

Обе модели маленькие (5 МБ), считаются на процессоре через ONNX Runtime.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

DET_SIZE = 640  # YuNet: вход 640×640
MESH_SIZE = 256  # Face Mesh: вход 256×256
MIN_SCORE = 0.75
MIN_FACE = 40  # лица меньше (в пикселях кадра) не оцениваем: глаз там не разглядеть

# Точки век в Face Mesh: (верх, низ) по трём вертикалям и (уголок, уголок) для ширины глаза
_EYES = (
    ([(159, 145), (158, 153), (160, 144)], (33, 133)),  # глаз слева на снимке
    ([(386, 374), (385, 380), (387, 373)], (362, 263)),  # глаз справа на снимке
)


REC_SIZE = 112  # SFace: выровненное лицо 112×112
# Куда SFace ждёт глаза, нос и уголки рта на выровненном лице (как в OpenCV FaceRecognizerSF.alignCrop)
_REC_POINTS = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655],
                        [70.7299, 92.2041]], dtype=np.float64)


class FaceModel:
    def __init__(self, det_path: Path, mesh_path: Path, rec_path: Path | None = None):
        import onnxruntime as ort

        ort.set_default_logger_severity(3)
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1  # оценка и так идёт во многих потоках сразу
        self.det = ort.InferenceSession(str(det_path), so, providers=["CPUExecutionProvider"])
        self.mesh = ort.InferenceSession(str(mesh_path), so, providers=["CPUExecutionProvider"])
        self.rec = ort.InferenceSession(str(rec_path), so, providers=["CPUExecutionProvider"]) if rec_path else None
        self._det_out = [o.name for o in self.det.get_outputs()]

    # ------------------------------------------------------------ поиск лиц

    def detect(self, img: Image.Image) -> list[dict]:
        """Лица на RGB-снимке: рамка, уверенность и 5 точек (глаза, нос, уголки рта) в пикселях снимка."""
        w, h = img.size
        scale = DET_SIZE / max(w, h)
        small = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BILINEAR)
        canvas = np.zeros((DET_SIZE, DET_SIZE, 3), dtype=np.float32)
        a = np.asarray(small, dtype=np.float32)
        canvas[:a.shape[0], :a.shape[1]] = a[:, :, ::-1]  # YuNet обучен на BGR
        out = dict(zip(self._det_out, self.det.run(None, {"input": canvas.transpose(2, 0, 1)[None]})))
        boxes, scores, kps = [], [], []
        for stride in (8, 16, 32):
            n = DET_SIZE // stride
            cls = np.clip(out[f"cls_{stride}"][0, :, 0], 0, 1)
            obj = np.clip(out[f"obj_{stride}"][0, :, 0], 0, 1)
            score = np.sqrt(cls * obj)
            keep = np.nonzero(score >= MIN_SCORE)[0]
            if not len(keep):
                continue
            r, c = np.divmod(keep, n)
            bb = out[f"bbox_{stride}"][0, keep]
            cx, cy = (c + bb[:, 0]) * stride, (r + bb[:, 1]) * stride
            bw, bh = np.exp(bb[:, 2]) * stride, np.exp(bb[:, 3]) * stride
            boxes.append(np.stack([cx - bw / 2, cy - bh / 2, bw, bh], 1))
            scores.append(score[keep])
            k = out[f"kps_{stride}"][0, keep].reshape(-1, 5, 2)
            kps.append(np.stack([(k[:, :, 0] + c[:, None]) * stride, (k[:, :, 1] + r[:, None]) * stride], 2))
        if not boxes:
            return []
        boxes, scores, kps = np.concatenate(boxes) / scale, np.concatenate(scores), np.concatenate(kps) / scale
        return [{"box": boxes[i], "score": float(scores[i]), "kps": kps[i]} for i in _nms(boxes, scores)]

    # ------------------------------------------------------------ глаза

    def eyes(self, img: Image.Image, face: dict) -> dict | None:
        """Насколько открыты глаза (EAR: высота глаза / ширина) и резкость лица. None — лицо не распознано."""
        x, y, bw, bh = face["box"]
        (rx, ry), (lx, ly) = face["kps"][0], face["kps"][1]
        angle = math.degrees(math.atan2(ly - ry, lx - rx))
        cx, cy = x + bw / 2, y + bh / 2
        side = max(bw, bh) * 1.5
        # вырезаем с запасом, выравниваем глаза по горизонтали, потом берём квадрат вокруг лица
        big = side * 1.5
        region = img.crop((round(cx - big / 2), round(cy - big / 2), round(cx + big / 2), round(cy + big / 2)))
        region = region.rotate(angle, resample=Image.Resampling.BILINEAR, center=(big / 2, big / 2))
        off = (big - side) / 2
        crop = region.crop((round(off), round(off), round(off + side), round(off + side)))
        crop = crop.resize((MESH_SIZE, MESH_SIZE), Image.Resampling.BILINEAR)
        a = np.asarray(crop, dtype=np.float32) / 255.0
        pts, presence, _ = self.mesh.run(None, {"input_12": a[None]})
        if 1 / (1 + math.exp(-float(presence.ravel()[0]))) < 0.5:
            return None
        p = pts.reshape(478, 3)[:, :2]
        ears = []
        for pairs, (c1, c2) in _EYES:
            width = np.linalg.norm(p[c1] - p[c2])
            height = np.mean([np.linalg.norm(p[u] - p[d]) for u, d in pairs])
            ears.append(float(height / max(width, 1e-6)))
        gray = np.asarray(crop.convert("L"), dtype=np.float32)
        lap = -4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
        return {"ear": ears, "sharp": float(lap[64:192, 48:208].var()), "size": float(max(bw, bh))}

    # ------------------------------------------------------------ кто это

    def align(self, img: Image.Image, face: dict) -> Image.Image:
        """Лицо 112×112, повёрнутое и отмасштабированное так, чтобы глаза, нос и рот встали на свои места."""
        m = _similarity(np.asarray(face["kps"], dtype=np.float64), _REC_POINTS)
        inv = np.linalg.inv(np.vstack([m, [0, 0, 1]]))[:2]  # PIL ждёт отображение «выход → вход»
        return img.transform((REC_SIZE, REC_SIZE), Image.Transform.AFFINE, tuple(inv.ravel()),
                             resample=Image.Resampling.BILINEAR)

    def embed(self, img: Image.Image, face: dict) -> np.ndarray:
        """Отпечаток лица SFace (128 чисел, длина 1): у одного человека на разных снимках косинус ≳ 0.36."""
        a = np.asarray(self.align(img, face), dtype=np.float32).transpose(2, 0, 1)[None]  # RGB 0–255
        e = self.rec.run(None, {"data": a})[0][0]
        return e / max(float(np.linalg.norm(e)), 1e-8)


def _similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Поворот + масштаб + сдвиг (2×3), переводящий точки src в dst по методу наименьших квадратов (Umeyama)."""
    ms, md = src.mean(0), dst.mean(0)
    s, d = src - ms, dst - md
    u, sig, vt = np.linalg.svd(d.T @ s / len(src))
    fix = np.diag([1.0, np.sign(np.linalg.det(u @ vt))])
    rot = u @ fix @ vt
    scale = np.trace(np.diag(sig) @ fix) / s.var(0).sum()
    return np.hstack([scale * rot, (md - scale * rot @ ms)[:, None]])


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float = 0.3) -> list[int]:
    order = list(np.argsort(-scores))
    keep = []
    x1, y1 = boxes[:, 0], boxes[:, 1]
    x2, y2 = x1 + boxes[:, 2], y1 + boxes[:, 3]
    area = boxes[:, 2] * boxes[:, 3]
    while order:
        i = order.pop(0)
        keep.append(int(i))
        if not order:
            break
        rest = np.array(order)
        ix = np.maximum(0, np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]))
        iy = np.maximum(0, np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]))
        inter = ix * iy
        order = [j for j, o in zip(order, inter / (area[i] + area[rest] - inter)) if o < iou]
    return keep
