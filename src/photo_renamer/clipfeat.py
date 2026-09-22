"""Смысловое сходство фотографий (CLIP).

Обычные признаки (цвет, текстура, контуры) сравнивают картинки «как пиксели»: если свет или
камера другие, расстояние между папками становится больше, чем между разными деталями, и
распознавать нечего. CLIP сравнивает изображения по смыслу — «это одно и то же изделие» —
и к смене освещения и ракурса заметно устойчивее (замеры в docs/decisions.md).

Модель — часть CLIP ViT-B/32, отвечающая за изображения, в формате ONNX (opset 11). Работает
через onnxruntime на процессоре: интернет и права администратора не нужны, файл модели лежит
рядом с программой. Если onnxruntime не установлен или файла модели нет, программа работает
как прежде — на обычных признаках.

Модель кладётся командой (на машине с интернетом):

    python tools/get_clip_model.py --dest model
"""

import os
from typing import List, Optional, Sequence

import numpy as np
from PIL import Image

MODEL_FILE = "clip-vit-b32-vision.onnx"
INPUT_SIZE = 224                 # размер входа ViT-B/32
EMBEDDING_SIZE = 512
BATCH = 8                        # компромисс между скоростью и памятью на слабой машине
# Нормировка цвета, с которой обучался CLIP: менять нельзя, иначе эмбеддинги будут бессмысленными.
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def default_path(base_dir: str) -> str:
    return os.path.join(base_dir, "model", MODEL_FILE)


def runtime_available() -> bool:
    """Установлен ли onnxruntime (на рабочей машине он распаковывается вместе с программой)."""
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def preprocess(img: Image.Image) -> np.ndarray:
    """Изображение → массив (3, 224, 224), как ожидает CLIP: короткая сторона к 224, центр кадра."""
    img = img.convert("RGB")
    width, height = img.size
    scale = INPUT_SIZE / max(min(width, height), 1)
    size = (max(INPUT_SIZE, round(width * scale)), max(INPUT_SIZE, round(height * scale)))
    img = img.resize(size, Image.BICUBIC)
    left, top = (size[0] - INPUT_SIZE) // 2, (size[1] - INPUT_SIZE) // 2
    crop = np.asarray(img.crop((left, top, left + INPUT_SIZE, top + INPUT_SIZE)), dtype=np.float32) / 255.0
    return ((crop - MEAN) / STD).transpose(2, 0, 1)


class ClipEncoder:
    """Считает эмбеддинги изображений. Сессия onnxruntime создаётся один раз."""

    def __init__(self, path: str, threads: Optional[int] = None):
        import onnxruntime as ort

        options = ort.SessionOptions()
        if threads:
            options.intra_op_num_threads = int(threads)
        # Модель лежит рядом с программой и читается один раз; лишние оптимизации графа
        # на старом процессоре только удлиняют запуск.
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        self.path = path
        self.session = ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.version = "clip-vit-b32|%d" % os.path.getsize(path)

    def encode(self, images: Sequence[Image.Image]) -> np.ndarray:
        """(n, 512) единичной длины: тогда косинусное расстояние — это 1 − скалярное произведение."""
        if not images:
            return np.zeros((0, EMBEDDING_SIZE), dtype=np.float32)
        out: List[np.ndarray] = []
        for start in range(0, len(images), BATCH):
            batch = np.stack([preprocess(img) for img in images[start:start + BATCH]])
            vectors = self.session.run(None, {self.input_name: batch})[0].astype(np.float32)
            out.append(vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9))
        return np.concatenate(out)

    def encode_one(self, img: Image.Image) -> np.ndarray:
        return self.encode([img])[0]


def load(path: str, threads: Optional[int] = None) -> Optional[ClipEncoder]:
    """Загружает модель или возвращает None, если её нет и работать нужно на обычных признаках.

    Ошибка загрузки не должна ронять программу: без CLIP она работает, просто хуже.
    """
    if not path or not os.path.isfile(path) or not runtime_available():
        return None
    try:
        return ClipEncoder(path, threads)
    except Exception:
        return None


def describe(path: str) -> str:
    """Человекочитаемое состояние: что программа будет использовать."""
    if not runtime_available():
        return "CLIP не используется: не установлен onnxruntime"
    if not path or not os.path.isfile(path):
        return "CLIP не используется: нет файла модели (%s)" % (path or "путь не задан")
    return "CLIP используется: %s (%.0f МБ)" % (path, os.path.getsize(path) / 1e6)
