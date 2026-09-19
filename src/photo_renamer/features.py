"""Извлечение признаков изображений.

Признаки (у каждого своё расстояние, все нормированы примерно в [0, 1]):
- fg_hist — гистограмма HSV без фона по сетке 2×2. Фон оценивается по краям кадра. Главный признак:
  на реальных фото эталона он различает категории лучше остальных;
- tiny    — цветная миниатюра 16×16 (общая компоновка и цвета), косинусное расстояние;
- phash   — perceptual hash (алгоритм imagehash.phash), форма и контуры;
- hist    — гистограмма HSV всего кадра (вместе с фоном);
- lbp     — гистограмма локальных бинарных шаблонов (текстура) по сетке 2×2. Устойчива к освещению;
- hog     — гистограмма направлений контуров по сетке 4×4. Устойчива к освещению.
Веса признаков задаются в настройках. По умолчанию: fg_hist ×2 + lbp ×1 + hog ×1,
а матчер дополнительно подстраивает веса под освещение каждой папки (см. matcher).

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
FG_HIST = "fg_hist"
TINY = "tiny"
LBP = "lbp"
HOG = "hog"
ALL_FEATURES = (FG_HIST, TINY, PHASH, HIST, LBP, HOG)


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
    # Гистограммы HSV без фона по ячейкам сетки, склеенные; сумма = 1.
    fg_hist: np.ndarray
    # Миниатюра (size*size*3), центрированная и нормированная к единичной длине.
    tiny: np.ndarray
    # Гистограммы LBP по ячейкам сетки, склеенные; сумма = 1.
    lbp: np.ndarray
    # Гистограммы направлений градиента по ячейкам сетки, единичной длины.
    hog: np.ndarray


# Размер, до которого фото декодируется при чтении: хватает и для признаков (256 px),
# и для миниатюры в редакторе эталона (до 400 px). Для фото 6000×4000 draft() даёт 750×500.
DECODE_SIZE = 400
FEATURE_VERSION = "2"  # увеличить при изменении способа расчёта признаков (сбрасывает кэш признаков)


def fit(img: Image.Image, size: int) -> Image.Image:
    """Копия изображения, у которой большая сторона не больше size."""
    out = img.copy()
    out.thumbnail((size, size), Image.LANCZOS)
    return out


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


def _hsv_bins(hsv: np.ndarray, bins: Tuple[int, int, int]) -> np.ndarray:
    """Номер ячейки гистограммы для каждого пикселя (hsv — массив (N, 3) uint32)."""
    h_bins, s_bins, v_bins = bins
    return (hsv[:, 0] * h_bins >> 8) * (s_bins * v_bins) + (hsv[:, 1] * s_bins >> 8) * v_bins + (hsv[:, 2] * v_bins >> 8)


def hsv_histogram(img: Image.Image, bins: Tuple[int, int, int] = (12, 4, 4)) -> np.ndarray:
    """Совместная гистограмма оттенка, насыщенности и яркости, нормированная к сумме 1."""
    hsv = np.asarray(img.convert("HSV"), dtype=np.uint32).reshape(-1, 3)
    hist = np.bincount(_hsv_bins(hsv, bins), minlength=int(np.prod(bins))).astype(np.float32)
    return hist / max(hist.sum(), 1.0)


def foreground_mask(img: Image.Image, threshold: float = 40.0, border: int = 4) -> np.ndarray:
    """Маска «не фона». Цвет фона — медиана пикселей по краям кадра, фоном считается всё,
    что отличается от него меньше чем на threshold (евклидово расстояние в RGB)."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    edges = np.concatenate([a[:border].reshape(-1, 3), a[-border:].reshape(-1, 3),
                            a[:, :border].reshape(-1, 3), a[:, -border:].reshape(-1, 3)])
    background = np.median(edges, axis=0)
    return np.sqrt(((a - background) ** 2).sum(axis=2)) > threshold


def foreground_histogram(img: Image.Image, bins: Tuple[int, int, int] = (12, 4, 4), grid: int = 2,
                         threshold: float = 40.0) -> np.ndarray:
    """Гистограммы HSV без фона по ячейкам сетки grid×grid, склеенные и нормированные к сумме 1.
    Если в ячейке почти нет «не фона» (деталь маленькая), берутся все пиксели ячейки."""
    hsv = np.asarray(img.convert("HSV"), dtype=np.uint32)
    mask = foreground_mask(img, threshold)
    height, width = mask.shape
    n_bins = int(np.prod(bins))
    parts = []
    for gy in range(grid):
        for gx in range(grid):
            rows = slice(gy * height // grid, (gy + 1) * height // grid)
            cols = slice(gx * width // grid, (gx + 1) * width // grid)
            pixels = hsv[rows, cols].reshape(-1, 3)
            cell_mask = mask[rows, cols].ravel()
            if cell_mask.sum() >= max(20, cell_mask.size // 100):
                pixels = pixels[cell_mask]
            hist = np.bincount(_hsv_bins(pixels, bins), minlength=n_bins).astype(np.float32)
            parts.append(hist / max(hist.sum(), 1.0))
    return np.concatenate(parts) / (grid * grid)


def _gray(img: Image.Image, size: int) -> np.ndarray:
    return np.asarray(img.convert("L").resize((size, size), Image.BILINEAR), dtype=np.float32)


def lbp_histogram(img: Image.Image, grid: int = 2, size: int = 128) -> np.ndarray:
    """Локальные бинарные шаблоны: каждый пиксель сравнивается с 8 соседями (8-битный код).
    Код не меняется при изменении яркости и контраста, поэтому признак устойчив к освещению."""
    g = _gray(img, size)
    center = g[1:-1, 1:-1]
    code = np.zeros(center.shape, dtype=np.int32)
    neighbours = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]
    for bit, (dy, dx) in enumerate(neighbours):
        code |= (g[1 + dy:size - 1 + dy, 1 + dx:size - 1 + dx] >= center).astype(np.int32) << bit
    height, width = code.shape
    parts = []
    for gy in range(grid):
        for gx in range(grid):
            cell = code[gy * height // grid:(gy + 1) * height // grid, gx * width // grid:(gx + 1) * width // grid]
            hist = np.bincount(cell.ravel(), minlength=256).astype(np.float32)
            parts.append(hist / max(hist.sum(), 1.0))
    return np.concatenate(parts) / (grid * grid)


def hog_vector(img: Image.Image, bins: int = 12, grid: int = 4, size: int = 128) -> np.ndarray:
    """Упрощённый HOG: гистограммы направлений градиента (взвешенные по силе) по ячейкам сетки."""
    g = _gray(img, size)
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1] = g[2:] - g[:-2]
    magnitude = np.hypot(gx, gy)
    direction = np.minimum(((np.arctan2(gy, gx) % np.pi) / np.pi * bins).astype(np.int64), bins - 1)
    step = size // grid
    parts = []
    for i in range(grid):
        for j in range(grid):
            cell = (slice(i * step, (i + 1) * step), slice(j * step, (j + 1) * step))
            hist = np.bincount(direction[cell].ravel(), weights=magnitude[cell].ravel(), minlength=bins)
            parts.append(hist / (np.linalg.norm(hist) + 1e-6))
    v = np.concatenate(parts)
    return (v / (np.linalg.norm(v) + 1e-6)).astype(np.float32)


def balance_by_background(img: Image.Image, target: float = 128.0, border: int = 4,
                          limits: Tuple[float, float] = (0.5, 2.0)) -> Image.Image:
    """«Баланс белого по фону»: каналы RGB умножаются так, чтобы фон (медиана краёв кадра)
    стал нейтрально-серым. Выравнивает разное освещение и баланс белого между съёмками.
    Меняется только копия в памяти."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    edges = np.concatenate([a[:border].reshape(-1, 3), a[-border:].reshape(-1, 3),
                            a[:, :border].reshape(-1, 3), a[:, -border:].reshape(-1, 3)])
    gains = np.clip(target / np.maximum(np.median(edges, axis=0), 1.0), *limits)
    return Image.fromarray(np.clip(a * gains, 0, 255).astype(np.uint8))


def tiny_vector(img: Image.Image, size: int = 16) -> np.ndarray:
    """Цветная миниатюра size×size: вектор без среднего, единичной длины (для косинусного расстояния)."""
    v = np.asarray(img.convert("RGB").resize((size, size), Image.BOX), dtype=np.float32).ravel()
    v -= v.mean()
    return v / (np.linalg.norm(v) + 1e-6)


class FeatureExtractor:
    """Извлекает признаки и считает попарные расстояния. Все расстояния нормированы в [0, 1]."""

    def __init__(
        self,
        work_size: int = 256,
        hash_size: int = 8,
        hist_bins: Tuple[int, int, int] = (12, 4, 4),
        rotations: int = 1,
        grid: int = 2,
        tiny_size: int = 16,
        background_threshold: float = 40.0,
        white_balance: bool = False,
    ):
        if rotations not in (1, 2, 4):
            raise ValueError("rotations должно быть 1, 2 или 4")
        self.work_size = work_size
        self.hash_size = hash_size
        self.hist_bins = tuple(hist_bins)
        # 4 — поворот фото на 90/180/270° не мешает сравнению pHash, 2 — только 180°, 1 — без поворотов.
        # На реальных фото повороты ухудшили различение категорий, поэтому по умолчанию 1.
        self.rotations = rotations
        self.grid = grid
        self.tiny_size = tiny_size
        self.background_threshold = background_threshold
        self.white_balance = white_balance

    def from_image(self, img: Image.Image) -> Features:
        if self.white_balance:
            img = balance_by_background(img)
        step = 4 // self.rotations
        hashes = [phash_bits(img.rotate(90 * step * r, expand=True) if r else img, self.hash_size)
                  for r in range(self.rotations)]
        return Features(
            phash=np.stack(hashes),
            hist=hsv_histogram(img, self.hist_bins),
            fg_hist=foreground_histogram(img, self.hist_bins, self.grid, self.background_threshold),
            tiny=tiny_vector(img, self.tiny_size),
            lbp=lbp_histogram(img),
            hog=hog_vector(img),
        )

    def from_file(self, path: str) -> Features:
        return self.from_loaded(load_image(path, max(self.work_size, DECODE_SIZE)))

    def from_loaded(self, img: Image.Image) -> Features:
        """Признаки из изображения, прочитанного load_image(path, DECODE_SIZE).
        Единая точка для обычного чтения и для кэша: признаки получаются одинаковыми."""
        return self.from_image(fit(img, self.work_size))

    def version(self) -> str:
        """Строка, однозначно описывающая способ расчёта признаков (ключ кэша признаков)."""
        return "v%s|%d|%d|%s|%d|%d|%d|%g|%d" % (
            FEATURE_VERSION, self.work_size, self.hash_size, ",".join(map(str, self.hist_bins)), self.rotations,
            self.grid, self.tiny_size, self.background_threshold, int(self.white_balance))

    def distance_components(self, a: Sequence[Features], b: Sequence[Features]) -> Dict[str, np.ndarray]:
        """Матрицы расстояний формы (len(a), len(b)) по каждому признаку.

        phash: доля различающихся бит (0 — одинаковые, около 0.5 — несвязанные), минимум по поворотам b.
        hist, fg_hist: 1 − пересечение гистограмм (0 — одинаковые цвета, 1 — совсем разные).
        tiny, hog: (1 − косинус) / 2.
        lbp: 1 − пересечение гистограмм.
        """
        a_hash = np.stack([f.phash[0] for f in a])                 # (n, bits)
        b_hash = np.stack([f.phash for f in b])                    # (m, R, bits)
        diff = a_hash[:, None, None, :] != b_hash[None, :, :, :]   # (n, m, R, bits)
        phash = diff.mean(axis=-1).min(axis=-1)

        a_hist = np.stack([f.hist for f in a])
        b_hist = np.stack([f.hist for f in b])
        hist = 1.0 - np.minimum(a_hist[:, None, :], b_hist[None, :, :]).sum(axis=-1)

        a_fg = np.stack([f.fg_hist for f in a])
        b_fg = np.stack([f.fg_hist for f in b])
        fg_hist = 1.0 - np.minimum(a_fg[:, None, :], b_fg[None, :, :]).sum(axis=-1)

        tiny = (1.0 - np.stack([f.tiny for f in a]) @ np.stack([f.tiny for f in b]).T) / 2.0
        hog = (1.0 - np.stack([f.hog for f in a]) @ np.stack([f.hog for f in b]).T) / 2.0
        a_lbp = np.stack([f.lbp for f in a])
        b_lbp = np.stack([f.lbp for f in b])
        lbp = 1.0 - np.minimum(a_lbp[:, None, :], b_lbp[None, :, :]).sum(axis=-1)

        return {
            PHASH: phash,
            HIST: np.clip(hist, 0.0, 1.0),
            FG_HIST: np.clip(fg_hist, 0.0, 1.0),
            TINY: np.clip(tiny, 0.0, 1.0),
            LBP: np.clip(lbp, 0.0, 1.0),
            HOG: np.clip(hog, 0.0, 1.0),
        }


def component_scales(components: Dict[str, np.ndarray], different: np.ndarray, target: float = 0.5) -> Dict[str, float]:
    """Множители, приводящие признаки к общему масштабу: среднее расстояние между фото
    разных категорий эталона становится равным target. Тогда веса и пороги не зависят от того,
    в каких единицах признак измеряет расстояние."""
    scales = {}
    for name, matrix in components.items():
        values = matrix[different]
        mean = float(values.mean()) if values.size else 0.0
        scales[name] = target / mean if mean > 1e-6 else 1.0
    return scales


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
