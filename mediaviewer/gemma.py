"""EmbeddingGemma 2 (Google, Apache 2.0) — вторая модель по смыслу на выбор вместо SigLIP 2.

Лучше понимает русский язык, но тяжелее: ~1,8 ГБ модели и считает только на видеокартах NVIDIA через CUDA
(на DirectML граф не запускается, на процессоре — 1–2 снимка в секунду). На RTX 5080 ~30 снимков в секунду вместе с чтением миниатюр.

ONNX Runtime с CUDA и основной (DirectML) в одном окружении не уживаются, поэтому модель работает в отдельном
процессе (gemma_worker.py) со своим окружением в папке модели: оно ставится той же кнопкой «Скачать».
Здесь — сторона программы: подготовка картинок, разговор с процессом и пересчёт сходства в категории и оценки.
"""
from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from . import ai

REPO = "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/main/"
FILES = ("tokenizer.json", "config.json", "onnx/model.onnx", "onnx/model.onnx_data", "onnx/vision_encoder.onnx",
         "onnx/vision_encoder.onnx_data")
MODEL_SIZE = 1790 * 1024 ** 2
RUNTIME_SIZE = 2 * 1024 ** 3  # ONNX Runtime с библиотеками CUDA и cuDNN
WORKER = Path(__file__).resolve().parent / "gemma_worker.py"
if sys.platform == "darwin":
    RUNTIME_PACKAGES = ("onnxruntime", "tokenizers", "numpy")
else:
    RUNTIME_PACKAGES = ("onnxruntime-gpu[cuda,cudnn]>=1.24", "tokenizers", "numpy")

# Картинка режется на квадраты 16 px, соседние 3×3 сливаются в один «токен». Миниатюры в индексе 400 px:
# при 140 токенах это ~2 Мп после увеличения. По качеству поиска не хуже полных 280 токенов, а в 2,4 раза быстрее.
PATCH, POOL = 16, 3
SOFT_TOKENS = 140
QUERY = "task: search result | query: "

# Пороги категорий по косинусу (у каждой категории свой), подобраны по разметке SigLIP на снимках из поездки:
# совпадает с ней на 81 %, расходится в основном на ночных и тёмных кадрах.
THRESHOLDS = {"people": 0.618, "animals": 0.609, "food": 0.612, "nature": 0.642, "city": 0.625, "transport": 0.627,
              "docs": 0.632, "screens": 0.65, "memes": 0.642, "junk": 0.68}
# Красота кадра: те же два описания, что у SigLIP; разница косинусов приведена к шкале SigLIP (те же доли снимков ✨)
AES_SCALE, AES_SHIFT = 57.92, -2.942
# Поиск: оставляем снимки не дальше этого от лучшего по косинусу (в среднем столько же, сколько оставляет SigLIP)
SEARCH_GAP = 0.045


def target_size(h: int, w: int) -> tuple[int, int]:
    """Размер, к которому приводится картинка: ~SOFT_TOKENS·9 квадратов, стороны кратны 48 (как в transformers)."""
    max_patches = SOFT_TOKENS * POOL ** 2
    factor = (max_patches * PATCH ** 2 / (h * w)) ** 0.5
    side = POOL * PATCH
    th, tw = int(factor * h // side) * side, int(factor * w // side) * side
    max_side = SOFT_TOKENS * side
    if th == 0:
        th, tw = side, min(int(w / h) * side, max_side)
    elif tw == 0:
        tw, th = side, min(int(h / w) * side, max_side)
    return th, tw


class GemmaManager(ai.Manager):
    """Как обычный Manager, но вместо пакетов основного окружения ставит своё окружение с CUDA в папку модели."""

    def __init__(self, data_dir: Path):
        super().__init__(data_dir, ai.GEMMA, {f: REPO + f for f in FILES}, MODEL_SIZE,
                         lambda d: GemmaEngine(d, self.python))
        self.runtime = self.dir / "runtime"
        self.python = str(self.runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))

    def packages_ok(self) -> bool:
        return (self.runtime / "ok").is_file()

    def info(self) -> dict:
        return dict(super().info(), download_size=MODEL_SIZE + (0 if self.packages_ok() else RUNTIME_SIZE))

    def _install_packages(self) -> None:
        self.job = {"phase": "packages", "done": 0, "total": 0,
                    "message": "Установка ONNX Runtime с CUDA (≈ 2 ГБ, несколько минут)…"}
        if not Path(self.python).is_file():
            r = subprocess.run([sys.executable, "-m", "venv", str(self.runtime)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                raise RuntimeError("Не удалось создать окружение: " + (r.stderr or r.stdout).strip()[-300:])
        ai.pip(self.python, *RUNTIME_PACKAGES)
        (self.runtime / "ok").write_text("ok", encoding="utf-8")


class GemmaEngine:
    """Модель в отдельном процессе. Сообщения: 4 байта длины + JSON-заголовок, следом сырые байты (nbytes)."""

    batch = 4

    def __init__(self, model_dir: Path, python: str):
        self._log = open(model_dir / "worker.log", "wb")
        self.proc = subprocess.Popen(
            [python, "-I", str(WORKER), str(model_dir)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._lock = threading.Lock()
        self._labels: tuple[np.ndarray, list[int]] | None = None
        self._aes: np.ndarray | None = None
        hdr, _ = self._recv()
        self.device = hdr["device"]

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        if self.alive:
            try:
                self.proc.stdin.close()  # процесс сам выходит, когда вход закрыт
                self.proc.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait(10)
        self._log.close()

    # ------------------------------------------------------------ обмен с процессом

    def _recv(self) -> tuple[dict, bytes]:
        def read(n: int) -> bytes:
            buf = self.proc.stdout.read(n)
            if len(buf) != n:
                self.proc.wait(5)
                raise RuntimeError("Процесс EmbeddingGemma завершился: " + self._log_tail())
            return buf

        hdr = json.loads(read(struct.unpack("<I", read(4))[0]))
        if "error" in hdr:
            raise RuntimeError("EmbeddingGemma: " + hdr["error"])
        return hdr, read(hdr["nbytes"]) if hdr.get("nbytes") else b""

    def _call(self, hdr: dict, payload: bytes = b"") -> np.ndarray:
        with self._lock:
            h = json.dumps(dict(hdr, nbytes=len(payload))).encode()
            try:
                self.proc.stdin.write(struct.pack("<I", len(h)) + h + payload)
                self.proc.stdin.flush()
            except OSError as e:
                raise RuntimeError("Процесс EmbeddingGemma не отвечает: " + self._log_tail()) from e
            res, data = self._recv()
        return np.frombuffer(data, dtype=np.float32).reshape(res["shape"]).copy()

    def _log_tail(self) -> str:
        try:
            lines = (Path(self._log.name).read_text(encoding="utf-8", errors="replace").strip().splitlines())
            return " ".join(lines[-3:])[-400:] or "без сообщения об ошибке"
        except OSError:
            return "без сообщения об ошибке"

    # ------------------------------------------------------------ эмбеддинги

    def prepare(self, data: bytes) -> np.ndarray:
        img = Image.open(io.BytesIO(data))
        img.draft("RGB", (1024, 1024))
        img = img.convert("RGB")
        th, tw = target_size(img.height, img.width)
        if (th, tw) != (img.height, img.width):
            img = img.resize((tw, th), Image.Resampling.BICUBIC)
        return np.asarray(img, dtype=np.uint8)

    def embed_batch(self, prepared: list[np.ndarray]) -> np.ndarray:
        return self._call({"op": "images", "shapes": [list(p.shape[:2]) for p in prepared]},
                          b"".join(np.ascontiguousarray(p).tobytes() for p in prepared))

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        return self._call({"op": "texts", "texts": [QUERY + t for t in texts]})

    # ------------------------------------------------------------ категории, красота, поиск

    def label_matrix(self) -> tuple[np.ndarray, list[int]]:
        if self._labels is None:
            prompts, owner = [], []
            for i, (_, _, texts, _) in enumerate(ai.LABELS):
                for t in texts:
                    prompts.append(f"a photo of {t}" if not t.startswith(("a photo", "a screenshot")) else t)
                    owner.append(i)
            self._labels = (self.embed_texts(prompts), owner)
        return self._labels

    def classify(self, emb: np.ndarray) -> list[tuple[str, float]]:
        mat, owner = self.label_matrix()
        sims = emb @ mat.T
        best = np.full((len(emb), len(ai.LABELS)), -1.0, dtype=np.float32)
        for j, i in enumerate(owner):
            best[:, i] = np.maximum(best[:, i], sims[:, j])
        # Косинусы у категорий разные по природе — сравниваем запас над порогом своей категории
        margin = best - np.array([THRESHOLDS[lbl[0]] for lbl in ai.LABELS], dtype=np.float32)
        res = []
        for m, b in zip(margin, best):
            i = int(m.argmax())
            res.append((ai.LABELS[i][0], float(b[i])) if m[i] > 0 else (ai.OTHER, float(b.max())))
        return res

    def aesthetic(self, emb: np.ndarray) -> np.ndarray:
        if self._aes is None:
            self._aes = self.embed_texts(list(ai.AES_PROMPTS))
        d = emb @ self._aes[0] - emb @ self._aes[1]
        return 1.0 / (1.0 + np.exp(-(AES_SCALE * d + AES_SHIFT)))

    @staticmethod
    def keep_hits(sims: np.ndarray, order: np.ndarray) -> tuple[np.ndarray, list[int]]:
        top = float(sims[order[0]])
        return sims, [int(j) for j in order if sims[j] >= top - SEARCH_GAP]


def manager(data_dir: Path) -> GemmaManager:
    return GemmaManager(data_dir)
