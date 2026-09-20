"""Сопоставление фото целевой папки с категориями эталона.

Шаги:
1. Расстояние от каждого фото до каждой категории — минимум по эталонным фото категории.
2. Оптимальное назначение (венгерский алгоритм, scipy.optimize.linear_sum_assignment).
   У категории столько «обычных» мест, сколько её фото в эталоне, и сколько угодно
   «дополнительных» мест со штрафом extra_penalty (лишние экземпляры, дубли).
   Дальше max_distance категория запрещена, и фото остаётся неопознанным.
   Поэтому две похожие категории (например, два вида кабеля по одному в эталоне)
   не займут одну и ту же категорию.
3. % уверенности: softmax по расстояниям до категорий и до «неопознано».
   Он учитывает и близость к эталону, и отрыв от второй по близости категории.
   Учитывается и состав эталона: если все места категории заняты другими фото,
   выбрать её можно только как лишний экземпляр, то есть расстояние + extra_penalty.
4. Правило сторон: стороны одной детали сняты подряд. Идущие подряд фото
   категории с N сторонами делятся на экземпляры по N фото. Неполный экземпляр
   может быть «достроен» соседним неопознанным фото, если оно похоже на эту категорию.
5. Экземпляры нумеруются по порядку появления. Номера, занятые уже переименованными фото, пропускаются.

Модуль не обращается к диску, кроме чтения фото через FeatureExtractor.
"""

import os
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

from . import features as feat
from .reference import Category, Reference, category_key, parse_name

STATUS_OK = "ok"                        # распознано уверенно
STATUS_SIDE_RULE = "side_rule"          # отнесено к категории по правилу сторон
STATUS_CHECK = "check"                  # имя предложено, но уверенность низкая — проверьте
STATUS_MANUAL = "manual"                # категория задана пользователем
STATUS_ALREADY_NAMED = "already_named"  # имя уже в формате эталона, не трогаем
STATUS_LOW_CONFIDENCE = "low_confidence"
STATUS_NO_MATCH = "no_match"
STATUS_EXCLUDED = "excluded"            # пользователь запретил переименование
STATUS_UNREADABLE = "unreadable"

IDENTIFIED = (STATUS_OK, STATUS_SIDE_RULE, STATUS_CHECK, STATUS_MANUAL, STATUS_ALREADY_NAMED)


@dataclass
class MatchConfig:
    # Подобрано по ручной разметке реальных папок (docs/decisions.md): цвет без фона + текстура + контуры.
    weights: Dict[str, float] = field(default_factory=lambda: {feat.FG_HIST: 2.0, feat.LBP: 1.0, feat.HOG: 1.0})
    # Подстройка весов под освещение папки: вес признака делится на (рост расстояний)^adapt_power.
    # 0 — не подстраивать.
    adapt_power: float = 4.0
    # Учёт порядка съёмки: к расстоянию фото добавляется доля расстояния лучшего из соседей.
    # Одинаковые детали снимают подряд, поэтому соседи подсказывают категорию. 0 — не учитывать.
    sequence_weight: float = 0.5
    # Пауза между кадрами, по которой серия делится на разные детали (секунды). 0 — не делить.
    # На размеченных данных деление по паузам не дало выигрыша ни при каком пороге,
    # поэтому по умолчанию выключено (см. docs/decisions.md).
    instance_gap_seconds: float = 0.0
    # Строгий режим: у категорий ровно столько мест, сколько фото в эталоне, лишних мест нет.
    # "auto" — включается, когда число фото в папке совпадает с эталоном; True/False — принудительно.
    strict_counts: object = "auto"
    max_distance: float = 0.40          # дальше этого расстояния фото не относится ни к одной категории
    extra_penalty: float = 0.05         # штраф за экземпляр сверх количества в эталоне
    temperature: float = 0.05           # «резкость» % уверенности
    min_confidence: float = 50.0        # ниже — фото считается неопознанным
    top_n: int = 3                      # сколько кандидатов показывать для неопознанных
    side_fill_max_distance: float = 0.50  # до какого расстояния соседнее фото достраивает сторону
    side_fill_top_k: int = 2            # ...и только если категория среди стольких первых кандидатов фото


@dataclass
class Candidate:
    category: str
    confidence: float  # 0..100
    distance: float


@dataclass
class PhotoMatch:
    index: int
    filename: str
    status: str
    category: Optional[Category] = None
    instance: Optional[int] = None
    side: Optional[int] = None
    new_name: Optional[str] = None      # None — имя не меняется
    confidence: float = 0.0             # % уверенности для назначенной категории
    distance: Optional[float] = None
    candidates: List[Candidate] = field(default_factory=list)
    note: str = ""

    @property
    def identified(self) -> bool:
        return self.status in IDENTIFIED and self.category is not None


class ReferenceModel:
    """Эталон с посчитанными признаками. Строится один раз и используется для всех папок."""

    def __init__(self, reference: Reference, extractor: feat.FeatureExtractor, ref_features: Sequence):
        self.reference = reference
        self.extractor = extractor
        self.warnings: List[str] = []

        readable = {}
        for photo, f in zip(reference.photos, ref_features):
            if f is not None:
                readable.setdefault(category_key(photo.parsed.category), []).append((photo, f))

        self.categories: List[Category] = []
        for key, cat in reference.categories.items():
            if key in readable:
                self.categories.append(cat)
            else:
                self.warnings.append("Категория «%s»: ни одно эталонное фото не прочитано, она не будет распознаваться"
                                     % cat.name)
        self.cat_index = {c.key: i for i, c in enumerate(self.categories)}
        self.capacity = np.array([len(c.photos) for c in self.categories], dtype=int)
        self.max_sides = max([c.sides for c in self.categories] or [1])

        self.ref_features, ref_cat, ref_side = [], [], []
        for cat in self.categories:
            for photo, f in readable[cat.key]:
                self.ref_features.append(f)
                ref_cat.append(self.cat_index[cat.key])
                ref_side.append(photo.side)
        self.ref_cat = np.array(ref_cat, dtype=int)
        self.ref_side = np.array(ref_side, dtype=int)
        self.scales = self._compute_scales()
        self.levels = self._compute_levels()

    def _compute_scales(self) -> Dict[str, float]:
        """Масштабы признаков по эталону: среднее расстояние между разными категориями → 0.5."""
        different = self.ref_cat[:, None] != self.ref_cat[None, :]
        if self.extractor is None or not different.any():
            return {}
        comps = self.extractor.distance_components(self.ref_features, self.ref_features)
        return feat.component_scales(comps, different)

    def _compute_levels(self) -> Dict[str, float]:
        """«Норма» каждого признака: медиана расстояния от эталонного фото до ближайшего другого."""
        if self.extractor is None or len(self.ref_features) < 2:
            return {}
        comps = self.extractor.distance_components(self.ref_features, self.ref_features)
        levels = {}
        for name, m in comps.items():
            m = m * self.scales.get(name, 1.0)
            np.fill_diagonal(m, np.inf)
            levels[name] = float(np.median(m.min(axis=1)))
        return levels

    MIN_PHOTOS_TO_ADAPT = 8

    def adapted_weights(self, comps: Dict[str, np.ndarray], weights: Dict[str, float],
                        power: float) -> Dict[str, float]:
        """Ослабляет признаки, расстояния которых в этой папке выросли сильнее обычного
        (типично — цвет при другом освещении). Рост = медиана расстояния до ближайшего эталонного
        фото в папке / та же медиана внутри эталона, ограничен диапазоном [1, 4]."""
        n = next(iter(comps.values())).shape[0] if comps else 0
        if power <= 0 or n < self.MIN_PHOTOS_TO_ADAPT or not self.levels:
            return dict(weights)
        adapted = {}
        for name, w in weights.items():
            if w <= 0 or name not in comps or self.levels.get(name, 0) <= 1e-6:
                adapted[name] = w
                continue
            growth = float(np.median(comps[name].min(axis=1))) / self.levels[name]
            adapted[name] = w / min(max(growth, 1.0), 4.0) ** power
        return adapted

    @classmethod
    def build(cls, reference: Reference, extractor: feat.FeatureExtractor, on_progress=None,
              feature_source=None) -> "ReferenceModel":
        """feature_source(extractor, paths, on_progress) → (features, errors); по умолчанию — чтение
        без кэша (features.extract_many), в программе — PhotoCache.extract_many."""
        feature_source = feature_source or feat.extract_many
        features, errors = feature_source(extractor, [p.path for p in reference.photos], on_progress)
        model = cls(reference, extractor, features)
        model.warnings[:0] = ["Эталонное фото не прочитано: %s (%s)" % (e.path, e.reason) for e in errors]
        return model

    def distances(self, target_features: Sequence[feat.Features], weights: Dict[str, float],
                  adapt_power: float = 0.0):
        """Возвращает (cat_dist, side_dist, веса после подстройки).

        cat_dist[i, c] — расстояние фото i до категории c.
        side_dist[i, c, s-1] — до стороны s категории c (inf, если такой стороны в эталоне нет).
        """
        n, n_cat = len(target_features), len(self.categories)
        if n == 0 or n_cat == 0:
            return np.full((n, n_cat), np.inf), np.full((n, n_cat, self.max_sides), np.inf), dict(weights)
        comps = self.extractor.distance_components(target_features, self.ref_features)
        comps = {name: m * self.scales.get(name, 1.0) for name, m in comps.items()}
        used = self.adapted_weights(comps, weights, adapt_power)
        dist = feat.combine(comps, used)  # (n, число эталонных фото)

        cat_dist = np.full((n, n_cat), np.inf)
        side_dist = np.full((n, n_cat, self.max_sides), np.inf)
        for c in range(n_cat):
            in_cat = self.ref_cat == c
            cat_dist[:, c] = dist[:, in_cat].min(axis=1)
            for s in range(1, self.max_sides + 1):
                mask = in_cat & (self.ref_side == s)
                if mask.any():
                    side_dist[:, c, s - 1] = dist[:, mask].min(axis=1)
        return cat_dist, side_dist, used


@dataclass
class FolderMatch:
    folder: str
    photos: List[PhotoMatch]
    model: ReferenceModel
    config: MatchConfig
    cat_dist: np.ndarray    # строки — фото с признаками (см. feature_row)
    side_dist: np.ndarray
    feature_row: Dict[int, int]  # индекс фото → строка в cat_dist/side_dist
    warnings: List[str] = field(default_factory=list)
    strict: bool = False         # применён строгий режим (состав совпадает с эталоном)
    weights_used: Dict[str, float] = field(default_factory=dict)  # веса после подстройки под папку
    times: List[Optional[float]] = field(default_factory=list)      # время съёмки, с (для деления серий)
    fixed: Dict[int, tuple] = field(default_factory=dict)           # уже названные по шаблону фото
    manual: Dict[int, Optional[str]] = field(default_factory=dict)  # выбор пользователя: ключ категории или None

    def category_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for p in self.photos:
            if p.identified:
                counts[p.category.name] = counts.get(p.category.name, 0) + 1
        return counts

    def unidentified(self) -> List[PhotoMatch]:
        return [p for p in self.photos if not p.identified]


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def detect_already_named(reference: Reference, filename: str) -> Optional[Tuple[Category, int, int]]:
    """Если имя файла уже в формате эталона («Процессор 1 фото 2.jpg»), возвращает (категория, экз., сторона)."""
    parsed = parse_name(filename)
    cat = reference.categories.get(category_key(parsed.category))
    if cat is None:
        return None
    instance, side = parsed.instance or 1, parsed.side or 1
    if cat.format_name(instance, side, os.path.splitext(filename)[1]) != _nfc(filename) or side > cat.sides:
        return None
    return cat, instance, side


def match_folder(
    model: ReferenceModel,
    folder: str,
    filenames: Sequence[str],
    config: Optional[MatchConfig] = None,
    on_progress=None,
    feature_source=None,
    shot_times: Optional[Sequence[Optional[float]]] = None,
) -> FolderMatch:
    """Полное сопоставление папки: чтение признаков (кроме уже переименованных фото) и назначение."""
    config = config or MatchConfig()
    fixed = {i: detect_already_named(model.reference, name) for i, name in enumerate(filenames)}
    to_read = [i for i, v in fixed.items() if v is None]
    feature_source = feature_source or feat.extract_many
    features, errors = feature_source(model.extractor, [os.path.join(folder, filenames[i]) for i in to_read],
                                      on_progress)
    feature_list: List[Optional[feat.Features]] = [None] * len(filenames)
    for i, f in zip(to_read, features):
        feature_list[i] = f
    reasons = {e.path: e.reason for e in errors}
    unreadable = {i: reasons.get(os.path.join(folder, filenames[i]), "") for i in to_read if feature_list[i] is None}
    return match_features(model, folder, filenames, feature_list, config,
                          fixed={i: v for i, v in fixed.items() if v is not None}, unreadable=unreadable,
                          times=shot_times)


def match_features(
    model: ReferenceModel,
    folder: str,
    filenames: Sequence[str],
    feature_list: Sequence[Optional[feat.Features]],
    config: Optional[MatchConfig] = None,
    fixed: Optional[Dict[int, Tuple[Category, int, int]]] = None,
    unreadable: Optional[Dict[int, str]] = None,
    times: Optional[Sequence[Optional[float]]] = None,
) -> FolderMatch:
    """Сопоставление по готовым признакам. feature_list[i] = None для уже переименованных и нечитаемых."""
    config = config or MatchConfig()
    fixed = fixed or {}
    unreadable = unreadable or {}

    rows = [i for i, f in enumerate(feature_list) if f is not None and i not in fixed]
    feature_row = {i: r for r, i in enumerate(rows)}
    cat_dist, side_dist, weights_used = model.distances([feature_list[i] for i in rows], config.weights,
                                                        config.adapt_power)

    photos = [PhotoMatch(i, name, STATUS_UNREADABLE) for i, name in enumerate(filenames)]
    for i, (cat, instance, side) in fixed.items():
        photos[i] = PhotoMatch(i, filenames[i], STATUS_ALREADY_NAMED, cat, instance, side,
                               confidence=100.0, note="имя уже в формате эталона")
    for i in range(len(filenames)):
        if photos[i].status == STATUS_UNREADABLE:
            photos[i].note = "не удалось прочитать фото" + (": " + unreadable[i] if unreadable.get(i) else "")

    result = FolderMatch(folder, photos, model, config, cat_dist, side_dist, feature_row,
                         weights_used=weights_used, fixed=dict(fixed), times=list(times or []))
    solve(result)
    return result


def solve(result: FolderMatch) -> None:
    """Раскладывает по категориям все фото, кроме уже названных и выбранных пользователем.

    Вызывается заново после каждой ручной правки: выбор пользователя занимает место своей
    категории, поэтому исправление одного фото помогает правильно разложить остальные.
    """
    model = result.model
    free = [i for i in sorted(result.feature_row) if i not in result.fixed and i not in result.manual]
    used = _occupied_capacity(model, result.fixed.values())
    for key in result.manual.values():
        if key is not None and key in model.cat_index:
            used[model.cat_index[key]] += 1

    for i in free:  # состояния пересчитываются заново
        photo = result.photos[i]
        photo.status = STATUS_NO_MATCH
        photo.category = None
        photo.note = ""
        photo.candidates = []
    for i, key in result.manual.items():
        photo = result.photos[i]
        if key is None:
            photo.status, photo.category = STATUS_EXCLUDED, None
            photo.note = "исключено вручную"
        else:
            photo.status = STATUS_MANUAL
            photo.category = model.reference.categories[key]
            photo.confidence = 100.0
            photo.note = "категория задана вручную"

    if free and model.categories:
        _assign_categories(result, free, used)
    _complete_sides(result)
    renumber(result)


def _occupied_capacity(model: ReferenceModel, fixed) -> np.ndarray:
    """fixed — последовательность (категория, экземпляр, сторона) уже названных фото."""
    used = np.zeros(len(model.categories), dtype=int)
    for cat, _, _ in fixed:
        if cat.key in model.cat_index:
            used[model.cat_index[cat.key]] += 1
    return used


def apply_sequence_prior(cat_dist: np.ndarray, weight: float) -> np.ndarray:
    """Учитывает порядок съёмки: к расстоянию каждого фото добавляется доля расстояния
    лучшего из соседей по папке. Одиночная ошибка внутри серии одинаковых деталей исправляется."""
    if weight <= 0 or len(cat_dist) < 2:
        return cat_dist
    padded = np.vstack([cat_dist[:1], cat_dist, cat_dist[-1:]])
    # делим на (1 + вес): шкала расстояний сохраняется, пороги и % уверенности не «плывут»
    return (cat_dist + weight * np.minimum(padded[:-2], padded[2:])) / (1.0 + weight)


def _confidences(cat_dist_row: np.ndarray, config: MatchConfig) -> np.ndarray:
    """% уверенности по категориям: softmax(-d/T) с псевдокатегорией «неопознано» на расстоянии max_distance."""
    logits = -np.append(cat_dist_row, config.max_distance) / config.temperature
    logits -= logits.max()
    p = np.exp(logits)
    return 100.0 * p[:-1] / p.sum()


def use_strict_counts(config: MatchConfig, model: "ReferenceModel", n_photos: int, used: int = 0) -> bool:
    """Строгий режим: совпадает ли число фото в папке с числом фото в эталоне."""
    if config.strict_counts is not True and config.strict_counts != "auto":
        return False
    if config.strict_counts is True:
        return True
    return n_photos + used == int(model.capacity.sum())


def _assign_categories(result: FolderMatch, rows: List[int], used: np.ndarray) -> None:
    """rows — индексы фото (в порядке папки), которые нужно разложить по категориям."""
    model, config = result.model, result.config
    # соседей берём по всей папке (порядок съёмки), затем оставляем только нужные строки
    smoothed = apply_sequence_prior(result.cat_dist, config.sequence_weight)
    selected = [result.feature_row[i] for i in rows]
    cat_dist = smoothed[selected]
    raw_dist = result.cat_dist[selected]
    n, n_cat = cat_dist.shape
    strict = use_strict_counts(config, model, n, int(used.sum()))
    result.strict = strict

    base_cap = np.maximum(model.capacity - used, 0)
    # в строгом режиме лишних мест нет: состав папки считается таким же, как в эталоне
    extra_cap = np.zeros(n_cat, dtype=int) if strict else np.full(n_cat, n)
    col_cat = np.concatenate([np.repeat(np.arange(n_cat), base_cap), np.repeat(np.arange(n_cat), extra_cap)])
    col_cost = np.concatenate([np.zeros(base_cap.sum()), np.full(extra_cap.sum(), config.extra_penalty)])
    # max_distance — строгая граница: дальше неё категория запрещена. «Неопознано» стоит
    # max_distance + штраф, поэтому фото в пределах границы всегда получает категорию,
    # хотя бы как лишний экземпляр. Штраф влияет только на выбор между категориями.
    limit = np.inf if strict else config.max_distance
    dist = np.where(cat_dist <= limit, cat_dist, np.inf)
    cost = dist[:, col_cat] + col_cost[None, :]
    unmatched = 1e5 if strict else (config.max_distance + config.extra_penalty)
    cost = np.hstack([cost, np.full((n, n), unmatched)])
    cost = np.where(np.isfinite(cost), cost, 1e6)

    row_ind, col_ind = linear_sum_assignment(cost)
    assigned = np.full(n, -1)
    for r, col in zip(row_ind, col_ind):
        if col < len(col_cat):
            assigned[r] = col_cat[col]
    occupied = used + np.bincount(assigned[assigned >= 0], minlength=n_cat)

    for r, i in enumerate(rows):
        others = occupied - (np.arange(n_cat) == assigned[r])
        full = others >= model.capacity
        conf = _confidences(cat_dist[r] + config.extra_penalty * full, config)
        order = np.argsort(-conf, kind="stable")[: config.top_n]
        photo = result.photos[i]
        photo.candidates = [Candidate(model.categories[c].name, float(conf[c]), float(cat_dist[r, c])) for c in order]
        c = assigned[r]
        if c < 0:
            photo.status = STATUS_NO_MATCH
            photo.confidence = float(conf.max())
            photo.distance = float(raw_dist[r].min())
            photo.note = "не похоже ни на одну категорию эталона"
            continue
        photo.confidence = float(conf[c])
        photo.distance = float(raw_dist[r, c])
        if strict:
            # состав совпадает с эталоном: имя предлагается всегда, сомнительные видны в предпросмотре
            photo.status = STATUS_OK if photo.confidence >= config.min_confidence else STATUS_CHECK
            photo.category = model.categories[c]
            if photo.status == STATUS_CHECK:
                photo.note = "низкая уверенность (%.0f%%) — проверьте имя" % photo.confidence
            continue
        if photo.confidence < config.min_confidence:
            photo.status = STATUS_LOW_CONFIDENCE
            photo.note = "низкая уверенность (%.0f%%)" % photo.confidence
        else:
            photo.status = STATUS_OK
            photo.category = model.categories[c]


def _runs(photos: List[PhotoMatch]) -> List[Tuple[Category, List[int]]]:
    """Серии подряд идущих фото одной категории (уже переименованные фото серии разрывают)."""
    runs: List[Tuple[Category, List[int]]] = []
    current_key = None
    for p in photos:
        key = p.category.key if p.identified and p.status != STATUS_ALREADY_NAMED else None
        if key is not None and key == current_key:
            runs[-1][1].append(p.index)
        elif key is not None:
            runs.append((p.category, [p.index]))
        current_key = key
    return runs


def _complete_sides(result: FolderMatch) -> None:
    """Достраивает неполные экземпляры многосторонних деталей соседними неопознанными фото."""
    model, config, photos = result.model, result.config, result.photos
    changed = True
    while changed:
        changed = False
        for cat, idx in _runs(photos):
            if cat.sides < 2 or len(idx) % cat.sides == 0:
                continue
            c = model.cat_index.get(cat.key)
            if c is None:
                continue
            for neighbor in (idx[-1] + 1, idx[0] - 1):
                if not 0 <= neighbor < len(photos):
                    continue
                p = photos[neighbor]
                row = result.feature_row.get(neighbor)
                if p.status not in (STATUS_LOW_CONFIDENCE, STATUS_NO_MATCH) or row is None:
                    continue
                if result.cat_dist[row, c] > config.side_fill_max_distance:
                    continue
                # категория должна быть среди первых кандидатов самого фото: без этого правило
                # «достраивало» стороны фото, почти не похожим на категорию (ошибки на реальных папках)
                if cat.name not in [k.category for k in p.candidates[:config.side_fill_top_k]]:
                    continue
                p.status = STATUS_SIDE_RULE
                p.category = cat
                p.distance = float(result.cat_dist[row, c])
                p.confidence = next((k.confidence for k in p.candidates if k.category == cat.name),
                                    float(_confidences(result.cat_dist[row], config)[c]))
                p.note = "по правилу сторон (соседнее фото — %s)" % cat.name
                changed = True
                break
            if changed:
                break


def _time_segments(result: FolderMatch, idx: List[int]) -> List[List[int]]:
    """Делит серию по длинным паузам между кадрами: это разные детали, снятые подряд."""
    limit = result.config.instance_gap_seconds
    times = result.times
    if limit <= 0 or not times:
        return [idx]
    segments = [[idx[0]]]
    for previous, current in zip(idx, idx[1:]):
        before, after = times[previous], times[current]
        if before is not None and after is not None and after - before > limit:
            segments.append([current])
        else:
            segments[-1].append(current)
    return segments


def _split_run(result: FolderMatch, cat: Category, idx: List[int]) -> List[List[int]]:
    """Делит серию на экземпляры по cat.sides фото. Сначала серия режется по длинным паузам
    (разные детали), затем каждый кусок — по числу сторон. Неполный экземпляр ставится
    в начало или конец, смотря что лучше согласуется с расстояниями до сторон эталона."""
    sides = cat.sides
    if sides == 1:
        return [[i] for i in idx]
    segments = _time_segments(result, idx)
    if len(segments) > 1:
        out = []
        for segment in segments:
            out.extend(_split_run_by_sides(result, cat, segment))
        return out
    return _split_run_by_sides(result, cat, idx)


def _split_run_by_sides(result: FolderMatch, cat: Category, idx: List[int]) -> List[List[int]]:
    sides = cat.sides
    if sides == 1 or len(idx) <= 1:
        return [idx] if len(idx) <= 1 else [[i] for i in idx]
    full, rest = divmod(len(idx), sides)
    if rest == 0:
        return [idx[k:k + sides] for k in range(0, len(idx), sides)]

    def chunk(lengths):
        out, pos = [], 0
        for n in lengths:
            out.append(idx[pos:pos + n])
            pos += n
        return out

    options = [chunk([sides] * full + [rest]), chunk([rest] + [sides] * full)]
    c = result.model.cat_index.get(cat.key)

    def cost(groups):
        total = 0.0
        for g in groups:
            for s, i in enumerate(g):
                row = result.feature_row.get(i)
                if row is None or c is None:
                    continue
                d = result.side_dist[row, c, s]
                total += d if np.isfinite(d) else result.cat_dist[row, c]
        return total

    return min(options, key=cost)


def renumber(result: FolderMatch) -> None:
    """Пересчитывает экземпляры, стороны и новые имена. Вызывается и после ручной правки категорий."""
    photos = result.photos
    used: Dict[str, Set[int]] = {}
    for p in photos:
        if p.status == STATUS_ALREADY_NAMED:
            used.setdefault(p.category.key, set()).add(p.instance)
            p.new_name = None
        elif not p.identified:
            p.instance = p.side = p.new_name = None

    result.warnings = [w for w in result.warnings if not w.startswith("Неполный комплект")]
    next_free: Dict[str, int] = {}
    for cat, idx in _runs(photos):
        for group in _split_run(result, cat, idx):
            instance = next_free.get(cat.key, 1)
            while instance in used.get(cat.key, set()):
                instance += 1
            next_free[cat.key] = instance + 1
            for side, i in enumerate(group, start=1):
                p = photos[i]
                p.instance, p.side = instance, side
                new_name = cat.format_name(instance, side, os.path.splitext(p.filename)[1])
                p.new_name = None if new_name == _nfc(p.filename) else new_name
            if len(group) != cat.sides:
                result.warnings.append(
                    "Неполный комплект сторон: %s %d — %d фото из %d (%s)"
                    % (cat.name, instance, len(group), cat.sides, ", ".join(photos[i].filename for i in group)))


def set_category(result: FolderMatch, index: int, key: Optional[str]) -> None:
    """Ручная правка из предпросмотра: key — ключ категории или None (не переименовывать).

    После правки раскладка пересчитывается целиком: выбранная пользователем категория
    занимает своё место, поэтому одно исправление помогает правильно разложить остальные фото.
    """
    if index in result.fixed:
        return
    result.manual[index] = key
    solve(result)


def clear_category(result: FolderMatch, index: int) -> None:
    """Отменяет ручной выбор для фото и пересчитывает раскладку."""
    if result.manual.pop(index, "нет") != "нет":
        solve(result)
