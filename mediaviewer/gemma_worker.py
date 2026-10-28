"""Процесс EmbeddingGemma 2: запускается питоном из окружения модели (ONNX Runtime с CUDA), см. gemma.py.

Не импортирует ничего из программы — в этом окружении есть только numpy, onnxruntime и tokenizers.
Запрос и ответ: 4 байта длины + JSON-заголовок, следом nbytes сырых байт.
  {"op": "texts", "texts": [...]}                → эмбеддинги float32 [n, dim]
  {"op": "images", "shapes": [[h, w], ...]}     + картинки RGB uint8 подряд → эмбеддинги float32 [n, dim]
Картинки уже приведены к нужному размеру (стороны кратны 48). Подготовка повторяет transformers.js (gemma4).
"""
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np

PATCH, POOL = 16, 3


def patchify(img: np.ndarray, max_soft: int):
    """Картинка → квадраты 16×16 [max_patches, 768], их координаты [max_patches, 2] (−1 — пусто), число токенов."""
    h, w = img.shape[:2]
    a = img.astype(np.float32) / 255.0
    ph, pw = h // PATCH, w // PATCH
    p = a[:ph * PATCH, :pw * PATCH].reshape(ph, PATCH, pw, PATCH, 3).transpose(0, 2, 1, 3, 4).reshape(ph * pw, -1)
    max_patches = max_soft * POOL ** 2
    patches = np.zeros((max_patches, PATCH * PATCH * 3), np.float32)
    patches[:len(p)] = p
    pos = np.full((max_patches, 2), -1, np.int64)
    cols, rows = np.meshgrid(np.arange(pw), np.arange(ph))
    pos[:ph * pw, 0], pos[:ph * pw, 1] = cols.ravel(), rows.ravel()
    return patches, pos, (ph * pw) // POOL ** 2


def main() -> None:
    model_dir = Path(sys.argv[1])
    # Ответы идут через stdout — всё, что библиотеки сами печатают в stdout, уводим в журнал (stderr)
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    inp = sys.stdin.buffer

    def send(hdr: dict, data: bytes = b"") -> None:
        h = json.dumps(dict(hdr, nbytes=len(data))).encode()
        out.write(struct.pack("<I", len(h)) + h + data)
        out.flush()

    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()  # библиотеки CUDA и cuDNN из пакетов nvidia-* этого окружения
        ort.set_default_logger_severity(3)
        so = ort.SessionOptions()
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        providers = ["CPUExecutionProvider"]
        if "CUDAExecutionProvider" in ort.get_available_providers():
            # kSameAsRequested: без запаса «на вырост», иначе пачки картинок быстро съедают всю видеопамять
            providers.insert(0, ("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}))
        try:
            vision = ort.InferenceSession(str(model_dir / "onnx/vision_encoder.onnx"), so, providers=providers)
            text = ort.InferenceSession(str(model_dir / "onnx/model.onnx"), so, providers=providers)
        except Exception:  # noqa: BLE001 — CUDA есть в пакете, но не заводится (нет драйвера NVIDIA)
            vision = ort.InferenceSession(str(model_dir / "onnx/vision_encoder.onnx"), so,
                                          providers=["CPUExecutionProvider"])
            text = ort.InferenceSession(str(model_dir / "onnx/model.onnx"), so, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        hidden = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))["text_config"]["hidden_size"]
    except Exception as e:  # noqa: BLE001
        send({"error": f"не удалось загрузить модель: {e}"[:400]})
        return
    vin = [i.name for i in vision.get_inputs()]
    tin = {i.name for i in text.get_inputs()}
    tout = [o.name for o in text.get_outputs()]
    cuda = vision.get_providers()[0] == "CUDAExecutionProvider"
    send({"device": "видеокарта (CUDA)" if cuda else "процессор"})

    def run_text(texts: list[str], image_features: np.ndarray | None = None) -> np.ndarray:
        encs = tok.encode_batch(texts)
        n = max(len(e.ids) for e in encs)
        ids = np.zeros((len(encs), n), np.int64)
        mask = np.zeros((len(encs), n), np.int64)
        for i, e in enumerate(encs):
            ids[i, :len(e.ids)] = e.ids
            mask[i, :len(e.ids)] = 1
        empty = np.zeros((0, hidden), np.float32)
        feeds = {"input_ids": ids, "attention_mask": mask, "image_features": empty, "video_features": empty,
                 "audio_features": empty}
        if image_features is not None:
            feeds["image_features"] = image_features
        res = dict(zip(tout, text.run(None, {k: v for k, v in feeds.items() if k in tin})))
        e = res["sentence_embedding"].astype(np.float32)
        return e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-8)

    def run_images(shapes: list, data: bytes) -> np.ndarray:
        imgs, at = [], 0
        for h, w in shapes:
            imgs.append(np.frombuffer(data, np.uint8, h * w * 3, at).reshape(h, w, 3))
            at += h * w * 3
        max_soft = max((h // PATCH) * (w // PATCH) // POOL ** 2 for h, w in shapes)
        parts = [patchify(im, max_soft) for im in imgs]
        feats = vision.run(None, {vin[0]: np.stack([p[0] for p in parts]), vin[1]: np.stack([p[1] for p in parts])})[0]
        ns = [p[2] for p in parts]
        if feats.ndim == 3:  # [картинка, токен, признак] — у каждой берём только её токены
            feats = np.concatenate([f[:n] for f, n in zip(feats, ns)])
        texts = ["<|image>" + "<|image|>" * n + "<image|>" for n in ns]
        return run_text(texts, feats.astype(np.float32))

    while True:
        head = inp.read(4)
        if len(head) < 4:
            return  # программа закрылась
        hdr = json.loads(inp.read(struct.unpack("<I", head)[0]))
        data = inp.read(hdr["nbytes"]) if hdr.get("nbytes") else b""
        try:
            emb = run_text(hdr["texts"]) if hdr["op"] == "texts" else run_images(hdr["shapes"], data)
            send({"shape": list(emb.shape)}, np.ascontiguousarray(emb, np.float32).tobytes())
        except Exception as e:  # noqa: BLE001
            send({"error": str(e)[:400]})


if __name__ == "__main__":
    main()
