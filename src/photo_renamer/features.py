"""Извлечение признаков изображений: perceptual hash (pHash) и цветовая гистограмма HSV.

Файлы только читаются. Уменьшение изображения происходит в памяти, файл на диске не меняется.

Модуль расширяемый: FeatureExtractor.distance_components возвращает словарь
«имя признака → матрица расстояний». Матчер складывает их с весами, поэтому новый
признак (например, ORB из OpenCV) добавляется как ещё одна компонента без изменений матчера.
"""

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np
from PIL import Image, ImageOps
from scipy.fft import dct

from .scanner import io_path

PHASH = "phash"
HIST = "hist"


class FeatureError(Exception):
    """Не удалось прочитать или декодировать изображение."""

    def __init__(self, path: str, reason: str):
        super().__init__("%s: %s" % (path, reason))
        self.path = path
        self.reason = reason


@dataclass
class Features:
    # Хеши изображения в нескольких поворотах: форма (rotations, hash_size**2), dtype=bool.
    # Строка 0 — исходная ориентация (после учёта EXIF-ориентации).
    phash: np.ndarray
    # Нормированная гистограмма HSV, форма (h_bins * s_bins * v_bins,), сумма = 1.
    hist: np.ndarray


def load_image(path: str, work_size: int = 256) -> Image.Image:
    """Читает JPEG и возвращает RGB-изображение, у которого большая сторона не больше work_size.

    draft() заставляет декодер JPEG сразу получать изображение в масштабе 1/2, 1/4 или 1/8.
    Для фото 15 МБ это в разы быстрее и экономнее по памяти, чем полное декодирование.
    EXIF-ориентация учитывается, поэтому фото «на боку» с тегом поворота выглядит так же,
    как в просмотрщике.
    """
    try:
        with Image.open(io_path(path)) as img:
            img.draft("RGB", (work_size, work_size))
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
    except FileNotFoundError:
        raise FeatureError(path, "файл не найден")
    except PermissionError:
        raise FeatureError(path, "нет доступа к файлу")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise FeatureError(path, "не удалось прочитать изображение (%s)" % exc)
    img.thumbnail((work_size, work_size), Image.LANCZOS)
    return img


def phash_bits(img: Image.Image, hash_size: int = 8, highfreq_factor: int = 4) -> np.ndarray:
    """pHash тем же алгоритмом, что imagehash.phash: DCT 32×32, низкие частоты 8×8, сравнение с медианой."""
    img_size = hash_size * highfreq_factor
    pixels = np.asarray(img.convert("L").resize((img_size, img_size), Image.LANCZOS), dtype=np.float64)
    coeffs = dct(dct(pixels, axis=0), axis=1)
    low = coeffs[:hash_size, :hash_size]
    return (low > np.median(low)).ravel()


def hsv_histogram(img: Image.Image, bins: Tuple[int, int, int] = (12, 4, 4)) -> np.ndarray:
    """Совместная гистограмма оттенка, насыщенности и яркости, нормированная к сумме 1."""
    hsv = np.asarray(img.convert("HSV"), dtype=np.uint32).reshape(-1, 3)
    h_bins, s_bins, v_bins = bins
    index = (hsv[:, 0] * h_bins >> 8) * (s_bins * v_bins) + (hsv[:, 1] * s_bins >> 8) * v_bins + (hsv[:, 2] * v_bins >> 8)
    hist = np.bincount(index, minlength=h_bins * s_bins * v_bins).astype(np.float32)
    return hist / max(hist.sum(), 1.0)


class FeatureExtractor:
    """Извлекает признаки и считает попарные расстояния. Все расстояния нормированы в [0, 1]."""

    def __init__(
        self,
        work_size: int = 256,
        hash_size: int = 8,
        hist_bins: Tuple[int, int, int] = (12, 4, 4),
        rotations: int = 4,
    ):
        if rotations not in (1, 2, 4):
            raise ValueError("rotations должно быть 1, 2 или 4")
        self.work_size = work_size
        self.hash_size = hash_size
        self.hist_bins = tuple(hist_bins)
        # 4 — поворот фото на 90/180/270° не мешает сравнению, 2 — только 180°, 1 — без поворотов.
        self.rotations = rotations

    def from_image(self, img: Image.Image) -> Features:
        step = 4 // self.rotations
        hashes = [phash_bits(img.rotate(90 * step * r, expand=True) if r else img, self.hash_size)
                  for r in range(self.rotations)]
        return Features(phash=np.stack(hashes), hist=hsv_histogram(img, self.hist_bins))

    def from_file(self, path: str) -> Features:
        return self.from_image(load_image(path, self.work_size))

    def distance_components(self, a: Sequence[Features], b: Sequence[Features]) -> Dict[str, np.ndarray]:
        """Матрицы расстояний формы (len(a), len(b)) по каждому признаку.

        phash: доля различающихся бит (0 — одинаковые, около 0.5 — несвязанные), минимум по поворотам b.
        hist: 1 − пересечение гистограмм (0 — одинаковые цвета, 1 — совсем разные).
        """
        a_hash = np.stack([f.phash[0] for f in a])                 # (n, bits)
        b_hash = np.stack([f.phash for f in b])                    # (m, R, bits)
        diff = a_hash[:, None, None, :] != b_hash[None, :, :, :]   # (n, m, R, bits)
        phash = diff.mean(axis=-1).min(axis=-1)

        a_hist = np.stack([f.hist for f in a])
        b_hist = np.stack([f.hist for f in b])
        hist = 1.0 - np.minimum(a_hist[:, None, :], b_hist[None, :, :]).sum(axis=-1)

        return {PHASH: phash, HIST: np.clip(hist, 0.0, 1.0)}


def combine(components: Dict[str, np.ndarray], weights: Dict[str, float]) -> np.ndarray:
    """Взвешенное среднее компонент. Признаки без веса или с нулевым весом не учитываются."""
    used = {name: w for name, w in weights.items() if w > 0 and name in components}
    if not used:
        raise ValueError("Нет ни одного признака с положительным весом")
    total = sum(used.values())
    return sum(components[name] * (w / total) for name, w in used.items())


def extract_many(extractor: FeatureExtractor, paths: Sequence[str], on_progress=None):
    """Последовательно извлекает признаки. Нечитаемые файлы не прерывают работу.

    Возвращает (features, errors): features[i] — Features или None, errors — список FeatureError.
    on_progress(done, total) вызывается после каждого файла (для индикатора в GUI).
    """
    features: List = []
    errors: List[FeatureError] = []
    for i, path in enumerate(paths):
        try:
            features.append(extractor.from_file(path))
        except FeatureError as exc:
            features.append(None)
            errors.append(exc)
        if on_progress:
            on_progress(i + 1, len(paths))
    return features, errors
