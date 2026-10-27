"""Нейросеть SigLIP 2: категории по смыслу и поиск по тексту.

Пакеты и модель ставятся по кнопке со страницы «AI» (не всем они нужны), анализ папки тоже запускается вручную.
Всё считается на этом компьютере: модель видит только миниатюры из индекса, сами файлы не читаются.
"""
from __future__ import annotations

import gc
import importlib
import importlib.util
import io
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

MODEL = "siglip2-base-224"
REPO = "https://huggingface.co/onnx-community/siglip2-base-patch16-224-ONNX/resolve/main/"
# Текстовая модель fp16, а не квантованная int8: та заметно хуже понимает запросы на русском
FILES = ("tokenizer.json", "onnx/text_model_fp16.onnx", "onnx/vision_model.onnx")
MODEL_SIZE = 925 * 1024 ** 2  # примерно, для кнопки «Скачать»
REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements-ai.txt"

# Обученные параметры google/siglip2-base-patch16-224: вероятность совпадения = sigmoid(scale · cos + bias)
LOGIT_SCALE = float(np.exp(4.7244534))
LOGIT_BIAS = -16.771725
IMAGE_SIZE = 224
TEXT_LEN = 64
BATCH = 32

# Категории по смыслу: ключ, название, описания для модели (по-английски она различает точнее) и порог уверенности.
# Пороги подобраны по снимкам из поездки: у «экранов», «документов», «мемов» и особенно «случайных кадров»
# при низкой уверенности попадаются обычные снимки (ночные, с вывесками, с ногами в кадре).
LABELS = [
    ("people", "Люди", ["people", "a portrait of a person", "a selfie", "a group of friends", "a child",
                        "a person posing for a photo", "a man", "a woman"], 0.00005),
    ("animals", "Животные", ["a cat", "a dog", "a pet", "a wild animal", "a bird"], 0.0001),
    ("food", "Еда и напитки", ["food on a plate", "a meal in a restaurant", "a drink or a cup of coffee", "a dessert"],
     0.0001),
    ("nature", "Природа", ["a landscape", "mountains", "a forest", "the sea and a beach", "sky and clouds", "flowers",
                           "a blossoming tree"], 0.00005),
    ("city", "Город и здания", ["a city street", "a building", "architecture", "a temple or a church",
                                "an interior of a room", "a city at night"], 0.00005),
    ("transport", "Транспорт", ["a car", "a train", "an airplane", "a bicycle", "a view from a car window"], 0.0003),
    ("docs", "Документы и чеки", ["a document", "a receipt", "a page of printed text", "a passport or an id card",
                                  "a ticket", "a whiteboard"], 0.002),
    ("screens", "Экраны", ["a screenshot of a phone", "a photo of a computer monitor", "a screenshot of a website",
                           "a screenshot of a chat", "a screenshot of a map"], 0.002),
    ("memes", "Мемы и картинки", ["a meme with text", "a cartoon", "a drawing or an illustration", "a poster"], 0.002),
    ("junk", "Случайные кадры", ["a blurry accidental photo with motion blur", "an almost black image",
                                 "a finger covering the camera lens", "an accidental photo taken inside a pocket",
                                 "an accidental photo of the ground"], 0.01),
]
OTHER = "other"  # не подходит уверенно ни под одну категорию


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


# ---------------------------------------------------------------- установка

class Manager:
    """Установка пакетов, скачивание модели и сама модель (загружается в память при первом обращении)."""

    def __init__(self, data_dir: Path):
        self.dir = data_dir / "ai" / MODEL
        self.job = {"phase": "idle", "done": 0, "total": 0, "message": ""}
        self._thread: threading.Thread | None = None
        self._engine: Engine | None = None
        self._lock = threading.Lock()

    @staticmethod
    def packages_ok() -> bool:
        return all(importlib.util.find_spec(m) is not None for m in ("onnxruntime", "tokenizers"))

    def model_ok(self) -> bool:
        return all((self.dir / f).is_file() for f in FILES)

    def ready(self) -> bool:
        return self.packages_ok() and self.model_ok()

    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def info(self) -> dict:
        size = sum(p.stat().st_size for p in self.dir.rglob("*") if p.is_file()) if self.dir.exists() else 0
        res = {"model": MODEL, "packages": self.packages_ok(), "installed": self.model_ok(), "size": size,
               "download_size": MODEL_SIZE, "job": dict(self.job), "busy": self.busy(),
               "device": self._engine.device if self._engine else None,
               "labels": [{"key": k, "label": lbl} for k, lbl, _, _ in LABELS]}
        return res

    def install(self) -> None:
        if self.busy():
            return
        self._thread = threading.Thread(target=self._install_safe, daemon=True, name="ai-install")
        self._thread.start()

    def _install_safe(self) -> None:
        try:
            if not self.packages_ok():
                self.job = {"phase": "packages", "done": 0, "total": 0, "message": "Установка пакетов ONNX Runtime…"}
                r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                                    "-r", str(REQUIREMENTS)], capture_output=True, text=True, encoding="utf-8",
                                   errors="replace")
                importlib.invalidate_caches()
                if r.returncode != 0 or not self.packages_ok():
                    tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
                    raise RuntimeError("pip не смог установить пакеты: " + " ".join(tail))
            self._download()
            self.job = {"phase": "done", "done": 0, "total": 0, "message": "Модель установлена"}
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.job = {"phase": "error", "done": 0, "total": 0, "message": str(e)[:400]}

    def _download(self) -> None:
        todo = [f for f in FILES if not (self.dir / f).is_file()]
        sizes = {}
        for f in todo:
            with urllib.request.urlopen(urllib.request.Request(REPO + f, method="HEAD"), timeout=60) as r:
                sizes[f] = int(r.headers.get("Content-Length") or 0)
        total = sum(sizes.values())
        done = 0
        self.job = {"phase": "download", "done": 0, "total": total, "message": "Скачивание модели…"}
        for f in todo:
            dst = self.dir / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            part = dst.with_name(dst.name + ".part")
            have = part.stat().st_size if part.exists() else 0
            req = urllib.request.Request(REPO + f, headers={"Range": f"bytes={have}-"} if have else {})
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:
                    have = 0  # сервер не умеет докачку — начинаем заново
                done += have
                with open(part, "ab" if have else "wb") as out:
                    while chunk := r.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        self.job["done"] = done
            if sizes[f] and part.stat().st_size != sizes[f]:
                raise RuntimeError(f"{f}: скачано {part.stat().st_size} из {sizes[f]} байт, попробуйте ещё раз")
            part.replace(dst)

    def remove(self) -> None:
        if self.busy():
            raise RuntimeError("Дождитесь окончания установки")
        with self._lock:
            self._engine = None
        gc.collect()  # закрыть сессии ONNX Runtime, иначе Windows не даст удалить файлы модели
        shutil.rmtree(self.dir, ignore_errors=True)
        self.job = {"phase": "idle", "done": 0, "total": 0, "message": ""}

    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                if not self.ready():
                    raise RuntimeError("Модель не установлена — скачайте её на странице «AI»")
                self._engine = Engine(self.dir)
            return self._engine


# ---------------------------------------------------------------- модель

class Engine:
    def __init__(self, model_dir: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        ort.set_default_logger_severity(3)
        available = ort.get_available_providers()
        gpu = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in available]
        so = ort.SessionOptions()
        so.enable_mem_pattern = False  # требование DirectML
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.vision = None
        for providers in ([gpu[0], "CPUExecutionProvider"] if gpu else [], ["CPUExecutionProvider"]):
            if not providers:
                continue
            try:
                self.vision = ort.InferenceSession(str(model_dir / "onnx/vision_model.onnx"), so, providers=providers)
                break
            except Exception:  # noqa: BLE001 — видеокарта не подошла, считаем на процессоре
                traceback.print_exc()
        used = self.vision.get_providers()[0]
        self.device = {"CUDAExecutionProvider": "видеокарта (CUDA)", "DmlExecutionProvider": "видеокарта (DirectML)"}.get(
            used, "процессор")
        # Запрос короткий и считается редко (~30 мс) — на процессоре. Полная оптимизация графа ломается на fp16-модели.
        tso = ort.SessionOptions()
        tso.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        self.text = ort.InferenceSession(str(model_dir / "onnx/text_model_fp16.onnx"), tso,
                                         providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.enable_padding(length=TEXT_LEN, pad_id=0)
        self.tok.enable_truncation(TEXT_LEN)
        self._vin = self.vision.get_inputs()[0].name
        self._vout = self._pooled_output(self.vision)
        self._tout = self._pooled_output(self.text)
        self._labels: np.ndarray | None = None
        self._run_lock = threading.Lock()

    @staticmethod
    def _pooled_output(sess) -> int:
        names = [o.name for o in sess.get_outputs()]
        for want in ("pooler_output", "image_embeds", "text_embeds"):
            if want in names:
                return names.index(want)
        return len(names) - 1

    @staticmethod
    def _norm(x: np.ndarray) -> np.ndarray:
        return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-8)

    @staticmethod
    def preprocess(data: bytes) -> np.ndarray:
        img = Image.open(io.BytesIO(data))
        img.draft("RGB", (IMAGE_SIZE * 2, IMAGE_SIZE * 2))
        img = img.convert("RGB").resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BILINEAR)
        a = np.asarray(img, dtype=np.float32) / 127.5 - 1.0  # rescale 1/255, normalize mean 0.5 std 0.5
        return a.transpose(2, 0, 1)

    def embed_images(self, pixels: np.ndarray) -> np.ndarray:
        with self._run_lock:
            out = self.vision.run(None, {self._vin: pixels})[self._vout]
        return self._norm(out.astype(np.float32))

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        # SigLIP 2 обучали на тексте в нижнем регистре
        ids = np.array([e.ids for e in self.tok.encode_batch([t.lower() for t in texts])], dtype=np.int64)
        with self._run_lock:
            out = self.text.run(None, {"input_ids": ids})[self._tout]
        return self._norm(out.astype(np.float32))

    def label_matrix(self) -> tuple[np.ndarray, list[int]]:
        """Эмбеддинги всех описаний категорий и номер категории для каждого описания."""
        if self._labels is None:
            prompts, owner = [], []
            for i, (_, _, texts, _) in enumerate(LABELS):
                for t in texts:
                    prompts.append(f"a photo of {t}" if not t.startswith(("a photo", "a screenshot")) else t)
                    owner.append(i)
            self._labels = (self.embed_texts(prompts), owner)
        return self._labels

    def classify(self, emb: np.ndarray) -> list[tuple[str, float]]:
        mat, owner = self.label_matrix()
        probs = _sigmoid(LOGIT_SCALE * (emb @ mat.T) + LOGIT_BIAS)
        best = np.zeros((len(emb), len(LABELS)), dtype=np.float32)
        for j, i in enumerate(owner):
            best[:, i] = np.maximum(best[:, i], probs[:, j])
        # Категория с наибольшей уверенностью среди тех, что прошли свой порог
        passed = np.where(best >= np.array([lbl[3] for lbl in LABELS], dtype=np.float32), best, 0)
        res = []
        for row, top in zip(passed, best):
            i = int(row.argmax())
            res.append((LABELS[i][0], float(row[i])) if row[i] > 0 else (OTHER, float(top.max())))
        return res


# ---------------------------------------------------------------- анализ папки

class Analyzer:
    """Фоновый проход по миниатюрам открытой папки: эмбеддинг и категория для каждого снимка."""

    def __init__(self, lib, manager: Manager):
        self.lib = lib
        self.manager = manager
        self.status = {"phase": "idle", "done": 0, "total": 0, "message": "", "started": 0}
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run_safe, daemon=True, name="ai-analyze")
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run_safe(self) -> None:
        self.status = {"phase": "load", "done": 0, "total": 0, "message": "Загрузка модели…", "started": time.time()}
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.status.update(phase="error", message=f"Ошибка анализа: {e}"[:400])
            return
        self.status.update(phase="stopped" if self._cancel.is_set() else "done",
                           message="Остановлено" if self._cancel.is_set() else "Готово")
        self.lib.status["version"] += 1

    def _run(self) -> None:
        eng = self.manager.engine()
        eng.label_matrix()
        with self.lib.connect() as c:
            c.execute("DELETE FROM ai WHERE id NOT IN (SELECT id FROM media WHERE parent_id IS NULL)")
            todo = [r[0] for r in c.execute(
                """SELECT m.id FROM media m JOIN thumbs t ON t.id = m.id LEFT JOIN ai a ON a.id = m.id
                   WHERE m.parent_id IS NULL AND (a.id IS NULL OR a.model != ? OR a.mtime IS NOT m.mtime)
                   ORDER BY m.ts DESC""", (MODEL,))]
        self.status.update(phase="analyze", total=len(todo), done=0, message=f"Анализ на: {eng.device}")
        pool = ThreadPoolExecutor(4)
        try:
            with self.lib.connect() as c:
                for i in range(0, len(todo), BATCH):
                    if self._cancel.is_set():
                        return
                    ids = todo[i:i + BATCH]
                    q = ",".join("?" * len(ids))
                    rows = c.execute(f"SELECT t.id, t.data, m.mtime FROM thumbs t JOIN media m ON m.id = t.id "
                                     f"WHERE t.id IN ({q})", ids).fetchall()
                    pix = list(pool.map(lambda r: _safe_pre(r[1]), rows))
                    ok = [(r, p) for r, p in zip(rows, pix) if p is not None]
                    if ok:
                        emb = eng.embed_images(np.stack([p for _, p in ok]))
                        labels = eng.classify(emb)
                        c.executemany(
                            "INSERT OR REPLACE INTO ai(id, model, mtime, emb, label, score) VALUES(?, ?, ?, ?, ?, ?)",
                            [(r[0], MODEL, r[2], e.astype(np.float16).tobytes(), lbl, sc)
                             for (r, _), e, (lbl, sc) in zip(ok, emb, labels)])
                        c.commit()
                    self.status["done"] = min(len(todo), i + len(ids))
                    self.lib.ai_version += 1
                    if (i // BATCH) % 20 == 0:
                        self.lib.status["version"] += 1
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


def _safe_pre(data: bytes) -> np.ndarray | None:
    try:
        return Engine.preprocess(data)
    except Exception:  # noqa: BLE001 — битая миниатюра
        return None


# ---------------------------------------------------------------- поиск

def counts(lib) -> dict:
    with lib.connect() as c:
        total = c.execute("SELECT COUNT(*) FROM media m JOIN thumbs t ON t.id = m.id WHERE m.parent_id IS NULL").fetchone()[0]
        done = c.execute("""SELECT COUNT(*) FROM ai a JOIN media m ON m.id = a.id
                            WHERE a.model = ? AND a.mtime IS m.mtime""", (MODEL,)).fetchone()[0]
        labels = dict(c.execute("SELECT label, COUNT(*) FROM ai WHERE model = ? GROUP BY label", (MODEL,)).fetchall())
    return {"total": total, "done": done, "labels": labels}


def _matrix(lib) -> tuple[np.ndarray, np.ndarray]:
    cache = getattr(lib, "_ai_cache", None)
    if cache is None or cache[0] != lib.ai_version:
        with lib.connect() as c:
            rows = c.execute("SELECT id, emb FROM ai WHERE model = ?", (MODEL,)).fetchall()
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        mat = (np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), -1).astype(np.float32)
               if rows else np.zeros((0, 768), dtype=np.float32))
        cache = lib._ai_cache = (lib.ai_version, ids, mat)
    return cache[1], cache[2]


def search(lib, manager: Manager, text: str, limit: int = 600) -> list[list]:
    """Снимки, подходящие под описание, от лучшего к худшему: [[id, вероятность], ...]."""
    ids, mat = _matrix(lib)
    if not len(ids):
        return []
    q = manager.engine().embed_texts([text])[0]
    probs = _sigmoid(LOGIT_SCALE * (mat @ q) + LOGIT_BIAS)
    order = np.argsort(-probs)[:limit]
    # Порог относительный: у разных запросов вероятности отличаются на порядки
    top = float(probs[order[0]])
    keep = [int(j) for j in order if probs[j] >= max(top * 0.02, 1e-5)]
    return [[int(ids[j]), round(float(probs[j]), 6)] for j in keep]
