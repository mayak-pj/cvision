"""Настройки программы: config.json рядом с программой.

Файла может не быть, тогда действуют значения по умолчанию. Неверные значения не прерывают
работу: берётся значение по умолчанию и выдаётся предупреждение. Ключи, начинающиеся
с «_», считаются комментариями.
"""

import json
import os
from dataclasses import dataclass, field, fields
from typing import List, Optional, Tuple

from . import clipfeat
from .features import ALL_FEATURES, FeatureExtractor
from .matcher import MatchConfig
from .scanner import SORT_BY_EXIF, SORT_BY_NAME

# src/photo_renamer/config.py → папка программы (где лежит run.py)
APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_NAME = "config.json"


@dataclass
class AppConfig:
    match: MatchConfig = field(default_factory=MatchConfig)
    work_size: int = 256
    hash_size: int = 8
    hist_bins: Tuple[int, int, int] = (12, 4, 4)
    rotations: int = 1
    white_balance: bool = False
    sort: str = SORT_BY_EXIF   # порядок съёмки надёжнее порядка имён (см. docs/decisions.md)
    problem_categories: List[str] = field(default_factory=list)
    logs_dir: str = "logs"
    cache_dir: str = "cache"
    names_file: str = "names.txt"
    theme: str = "light"       # light | dark | system (переключается лампочкой в окне)
    cache_max_mb: float = 2048
    # Файл модели CLIP рядом с программой. Если файла нет или не установлен onnxruntime,
    # программа работает на обычных признаках (см. clipfeat.py).
    clip_model: str = os.path.join("model", clipfeat.MODEL_FILE)
    clip_threads: int = 0        # 0 — на усмотрение onnxruntime (обычно все ядра)
    group_threshold: float = 0.30
    review_thumb: int = 96     # высота строки-миниатюры в предпросмотре (Ctrl+колесо меняет)

    def names_path(self) -> str:
        return self.names_file if os.path.isabs(self.names_file) else os.path.join(APP_DIR, self.names_file)

    def cache_path(self) -> str:
        folder = self.cache_dir if os.path.isabs(self.cache_dir) else os.path.join(APP_DIR, self.cache_dir)
        return os.path.join(folder, "photos.sqlite")

    def clip_path(self) -> str:
        return self.clip_model if os.path.isabs(self.clip_model) else os.path.join(APP_DIR, self.clip_model)

    def extractor(self) -> FeatureExtractor:
        return FeatureExtractor(self.work_size, self.hash_size, self.hist_bins, self.rotations,
                                white_balance=self.white_balance, encoder=self.encoder())

    def encoder(self):
        """Модель CLIP или None. Загружается один раз на объект настроек: сессия тяжёлая."""
        if not hasattr(self, "_encoder"):
            self._encoder = clipfeat.load(self.clip_path(), self.clip_threads or None)
        return self._encoder

    def logs_path(self) -> str:
        return self.logs_dir if os.path.isabs(self.logs_dir) else os.path.join(APP_DIR, self.logs_dir)


def default_path() -> str:
    return os.path.join(APP_DIR, CONFIG_NAME)


def _number(lo, hi, integer=False):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "ожидается число"
        if integer and int(value) != value:
            return "ожидается целое число"
        if not lo <= value <= hi:
            return "допустимо от %s до %s" % (lo, hi)
        return None
    return check


def _check_weights(value):
    if not isinstance(value, dict) or not value:
        return "ожидается объект вида {\"fg_hist\": 2, \"tiny\": 1}"
    unknown = set(value) - set(ALL_FEATURES)
    if unknown:
        return "неизвестные признаки: %s" % ", ".join(sorted(unknown))
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in value.values()):
        return "веса должны быть неотрицательными числами"
    if not any(v > 0 for v in value.values()):
        return "хотя бы один вес должен быть больше 0"
    return None


def _check_bins(value):
    if (not isinstance(value, list) or len(value) != 3
            or any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 64 for v in value)):
        return "ожидается список из трёх целых чисел от 1 до 64"
    return None


def _check_str_list(value):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return "ожидается список строк"
    return None


# ключ → (где хранится, проверка)
_MATCH_KEYS = {
    "min_confidence": _number(0, 100),
    "always_propose": lambda v: None if isinstance(v, bool) else "ожидается true или false",
    "max_distance": _number(0.01, 1.0),
    "extra_penalty": _number(0, 1.0),
    "closed_penalty": _number(0, 2.0),
    "temperature": _number(0.001, 1.0),
    "top_n": _number(1, 10, integer=True),
    "side_fill_max_distance": _number(0, 1.0),
    "side_fill_top_k": _number(1, 10, integer=True),
    "side_skip_penalty": _number(0, 2.0),
    "weights": _check_weights,
    "weights_with_clip": _check_weights,
    "sequence_weight": _number(0, 3),
    "renumber_existing": lambda v: None if isinstance(v, bool) else "ожидается true или false",
    "strict_counts": lambda v: None if isinstance(v, bool) or v == "auto" else "допустимо true, false или \"auto\"",
    "adapt_power": _number(0, 16),
    "block_weight": _number(0, 1.0),
    "block_span": _number(1, 100, integer=True),
    "learn_from_confirmed": lambda v: None if isinstance(v, bool) else "ожидается true или false",
    "example_penalty": _number(1.0, 4.0),
}
_APP_KEYS = {
    "work_size": _number(32, 2048, integer=True),
    "hash_size": _number(4, 32, integer=True),
    "hist_bins": _check_bins,
    "rotations": lambda v: None if v in (1, 2, 4) and not isinstance(v, bool) else "допустимо 1, 2 или 4",
    "white_balance": lambda v: None if isinstance(v, bool) else "ожидается true или false",
    "sort": lambda v: None if v in (SORT_BY_NAME, SORT_BY_EXIF) else "допустимо \"name\" или \"exif\"",
    "problem_categories": _check_str_list,
    "logs_dir": lambda v: None if isinstance(v, str) and v.strip() else "ожидается путь к папке",
    "theme": lambda v: None if v in ("light", "dark", "system") else
             "допустимо \"light\", \"dark\" или \"system\"",
    "names_file": lambda v: None if isinstance(v, str) and v.strip() else "ожидается путь к файлу",
    "cache_dir": lambda v: None if isinstance(v, str) and v.strip() else "ожидается путь к папке",
    "cache_max_mb": _number(50, 1024 * 1024),
    "clip_model": lambda v: None if isinstance(v, str) else "ожидается путь к файлу модели",
    "clip_threads": _number(0, 64, integer=True),
    "group_threshold": _number(0.01, 1.0),
    "review_thumb": _number(48, 400, integer=True),
}


# Настройки, которых больше нет. Файл настроек остаётся у пользователя от прежних версий,
# и ругаться на них незачем: программа просто их не читает.
_OBSOLETE_KEYS = {
    "reference_gap_warning",   # предупреждение «папка мало похожа на эталон» убрано
    "side_order_penalty",      # плата за съёмку сторон не по порядку: правило отменено
}


def from_dict(data: dict) -> Tuple[AppConfig, List[str]]:
    config = AppConfig()
    warnings: List[str] = []
    if not isinstance(data, dict):
        return config, ["Настройки должны быть JSON-объектом, используются значения по умолчанию"]
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if key in _OBSOLETE_KEYS:
            continue
        check = _MATCH_KEYS.get(key) or _APP_KEYS.get(key)
        if check is None:
            warnings.append("Неизвестная настройка «%s» пропущена" % key)
            continue
        error = check(value)
        if error:
            warnings.append("Настройка «%s» = %r: %s. Используется значение по умолчанию" % (key, value, error))
            continue
        if key == "hist_bins":
            value = tuple(value)
        elif key == "weights":
            value = {k: float(v) for k, v in value.items()}
        elif key in ("top_n", "side_fill_top_k"):
            value = int(value)
        target = config.match if key in _MATCH_KEYS else config
        setattr(target, key, value)
    return config, warnings


def to_dict(config: AppConfig) -> dict:
    data = {f.name: getattr(config.match, f.name) for f in fields(MatchConfig)}
    for f in fields(AppConfig):
        if f.name != "match":
            data[f.name] = getattr(config, f.name)
    data["hist_bins"] = list(config.hist_bins)
    return data


def load_config(path: Optional[str] = None) -> Tuple[AppConfig, List[str]]:
    """Возвращает (настройки, предупреждения). Отсутствие файла — не ошибка."""
    path = path or default_path()
    if not os.path.exists(path):
        return AppConfig(), []
    try:
        # utf-8-sig: Блокнот Windows 7 сохраняет UTF-8 с BOM
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        return AppConfig(), ["Не удалось прочитать %s (%s), используются значения по умолчанию" % (path, exc)]
    return from_dict(data)


def save_config(config: AppConfig, path: Optional[str] = None) -> None:
    path = path or default_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_dict(config), f, ensure_ascii=False, indent=2)
        f.write("\n")
