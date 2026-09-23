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
    # Веса, когда подключена модель CLIP: он несёт основной сигнал, цвет и текстура его удерживают.
    # Подобрано по ручной разметке (docs/decisions.md): на папке другой машины это дало 117 верных
    # имён против 20 и ни одной ошибки категории.
    weights_with_clip: Dict[str, float] = field(
        default_factory=lambda: {feat.CLIP: 4.0, feat.FG_HIST: 1.0, feat.LBP: 1.0})
    # Подстройка весов под освещение папки: вес признака делится на (рост расстояний)^adapt_power.
    # 0 — не подстраивать.
    adapt_power: float = 4.0
    # Учёт порядка съёмки: к расстоянию фото добавляется доля расстояния лучшего из соседей.
    # Одинаковые детали снимают подряд, поэтому соседи подсказывают категорию. 0 — не учитывать.
    sequence_weight: float = 0.5
    # Перенумеровывать ли уже названные фото, когда в их категории появились новые.
    # Иначе номера перестают соответствовать порядку съёмки: новые фото получают свободные
    # номера, и понять, какой ракурс к какой детали, становится невозможно.
    renumber_existing: bool = True
    # Строгий режим: у категорий ровно столько мест, сколько фото в эталоне, лишних мест нет.
    # "auto" — включается, когда число фото в папке совпадает с эталоном; True/False — принудительно.
    strict_counts: object = "auto"
    # Предлагать имя каждому фото, даже когда уверенности мало: по опыту заказчика такое имя
    # чаще верное, чем нет, а лишнее проще пометить в предпросмотре, чем вписывать вручную.
    # Сомнительные фото получают состояние «ПРОВЕРЬТЕ».
    always_propose: bool = True
    max_distance: float = 0.40          # дальше этого расстояния фото не относится ни к одной категории
    extra_penalty: float = 0.05         # штраф за экземпляр сверх количества в эталоне
    # Добавка к штрафу, если столько фото этой категории пользователь уже подтвердил сам:
    # значит, деталей такого вида больше нет, и лишний экземпляр почти наверняка ошибка.
    closed_penalty: float = 0.25
    temperature: float = 0.05           # «резкость» % уверенности
    min_confidence: float = 50.0        # ниже — фото считается неопознанным
    # Сколько кандидатов показывать в отчёте и журнале. Запоминаются все категории по порядку
    # похожести: в предпросмотре из них убираются уже предложенная и закрытые по составу,
    # и на их место должны вставать следующие.
    top_n: int = 3
    side_fill_max_distance: float = 0.50  # до какого расстояния соседнее фото достраивает сторону
    side_fill_top_k: int = 2            # ...и только если категория среди стольких первых кандидатов фото
    # Во сколько обходится пропущенная сторона детали при делении серии на экземпляры.
    # Меньше — программа охотнее признаёт, что какой-то стороны просто нет фото.
    side_skip_penalty: float = 0.10

    # Правило серий: одну и ту же деталь снимают подряд, поэтому окрестность кадра подсказывает
    # его категорию. Во втором проходе раскладки категория, за которую соседние кадры почти не
    # голосуют, получает штраф к расстоянию. 0 — не учитывать.
    block_weight: float = 0.02          # подобрано по ручной разметке (docs/decisions.md)
    block_span: int = 8                 # сколько соседних кадров с каждой стороны подсказывают категорию
    def for_features(self, has_clip: bool) -> Dict[str, float]:
        """Веса под то, что реально посчитано: с моделью CLIP — свой набор, без неё — обычный."""
        return dict(self.weights_with_clip if has_clip else self.weights)


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
        # getattr: в тестах признаки бывают подставными объектами без поля clip
        self.has_clip = bool(readable) and all(getattr(f, "clip", None) is not None
                                               for pairs in readable.values() for _, f in pairs)
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
        фото в папке / та же медиана внутри эталона, ограничен диапазоном [1, 4].

        Правило грубое: оно выбирает признак с наименьшими расстояниями, а не самый различающий
        (см. docs/decisions.md — замена требует пересчёта порогов и разметки нового проекта).
        """
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
    fixed: Dict[int, tuple] = field(default_factory=dict)           # уже названные по шаблону фото
    manual: Dict[int, Optional[str]] = field(default_factory=dict)  # выбор пользователя: ключ категории или None
    # категории, которых нет в эталоне: пользователь добавил их вручную для этой папки
    extra_categories: Dict[str, Category] = field(default_factory=dict)

    def category_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for p in self.photos:
            if p.identified:
                counts[p.category.name] = counts.get(p.category.name, 0) + 1
        return counts

    def category_by_key(self, key: str) -> Category:
        """Категория эталона или добавленная пользователем (см. add_category)."""
        category = self.model.reference.categories.get(key)
        return category if category is not None else self.extra_categories[key]

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
                          fixed={i: v for i, v in fixed.items() if v is not None}, unreadable=unreadable)


def match_features(
    model: ReferenceModel,
    folder: str,
    filenames: Sequence[str],
    feature_list: Sequence[Optional[feat.Features]],
    config: Optional[MatchConfig] = None,
    fixed: Optional[Dict[int, Tuple[Category, int, int]]] = None,
    unreadable: Optional[Dict[int, str]] = None,
) -> FolderMatch:
    """Сопоставление по готовым признакам. feature_list[i] = None для уже переименованных и нечитаемых."""
    config = config or MatchConfig()
    fixed = fixed or {}
    unreadable = unreadable or {}

    rows = [i for i, f in enumerate(feature_list) if f is not None and i not in fixed]
    feature_row = {i: r for r, i in enumerate(rows)}
    cat_dist, side_dist, weights_used = model.distances([feature_list[i] for i in rows],
                                                        config.for_features(model.has_clip),
                                                        config.adapt_power)

    photos = [PhotoMatch(i, name, STATUS_UNREADABLE) for i, name in enumerate(filenames)]
    for i, (cat, instance, side) in fixed.items():
        photos[i] = PhotoMatch(i, filenames[i], STATUS_ALREADY_NAMED, cat, instance, side,
                               confidence=100.0, note="имя уже в формате эталона")
    for i in range(len(filenames)):
        if photos[i].status == STATUS_UNREADABLE:
            photos[i].note = "не удалось прочитать фото" + (": " + unreadable[i] if unreadable.get(i) else "")

    result = FolderMatch(folder, photos, model, config, cat_dist, side_dist, feature_row,
                         weights_used=weights_used, fixed=dict(fixed))
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
            photo.category = result.category_by_key(key)
            photo.confidence = 100.0
            photo.note = "категория задана вручную"

    if free and model.categories:
        _assign_categories(result, free, used)
    _complete_sides(result)
    renumber(result)


def _confirmed_capacity(result: FolderMatch, model: ReferenceModel) -> np.ndarray:
    """Сколько фото каждой категории подтвердил человек: уже названные и заданные вручную.

    Догадки программы сюда не входят: закрывать категорию должна только уверенная пометка.
    """
    confirmed = np.zeros(len(model.categories), dtype=int)
    for cat, _, _ in result.fixed.values():
        index = model.cat_index.get(cat.key)
        if index is not None:
            confirmed[index] += 1
    for key in result.manual.values():
        index = model.cat_index.get(key) if key else None
        if index is not None:
            confirmed[index] += 1
    return confirmed


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


def block_penalty(positions: Sequence[int], anchors: Sequence[int], capacity: np.ndarray,
                  weight: float, span: int) -> np.ndarray:
    """Штраф за отрыв от серии: [фото][категория].

    Одну деталь снимают подряд, поэтому категорию фото подсказывает окрестность. Соседние кадры
    голосуют за свои категории с весом, убывающим с расстоянием, и штраф тем больше, чем меньшую
    долю голосов набрала категория. Пара ошибочных фото внутри чужой серии не удерживает сама
    себя: за неё два голоса против всей серии вокруг.

    Штраф уменьшается для категорий, которых в эталоне мало (множитель 1 - 1/capacity): у детали
    с единственным фото серии не бывает, и наказывать её не за что, а у пары сторон поддержка
    всегда мала. capacity — сколько фото категории в эталоне.

    positions — места фото в папке (порядок съёмки), anchors — категория каждого места по первому
    проходу (-1, если её нет). Фото не голосует за себя. Если рядом нет ни одного распознанного
    фото, подсказки нет и штрафа тоже нет.
    """
    n, n_cat = len(positions), len(capacity)
    if weight <= 0 or span <= 0 or n == 0:
        return np.zeros((n, n_cat))
    anchors = np.asarray(anchors, dtype=int)
    pos = np.asarray(positions, dtype=int)

    votes = np.zeros((n, n_cat))
    anchored = np.zeros(n)
    for shift in range(1, span + 1):
        k = 1.0 - shift / float(span + 1)          # чем дальше кадр, тем слабее голос
        for neighbour in (pos - shift, pos + shift):
            inside = (neighbour >= 0) & (neighbour < len(anchors))
            cats = np.where(inside, anchors[np.clip(neighbour, 0, len(anchors) - 1)], -1)
            known = cats >= 0
            votes[known, cats[known]] += k
            anchored[known] += k

    share = np.divide(votes, anchored[:, None], out=np.zeros_like(votes), where=anchored[:, None] > 0)
    scale = 1.0 - 1.0 / np.maximum(np.asarray(capacity, dtype=float), 1.0)
    return weight * scale[None, :] * (1.0 - share) * (anchored[:, None] > 0)


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
    # Категория, у которой пользователь подтвердил столько же фото, сколько их в эталоне,
    # считается закрытой: лишний экземпляр у неё стоит намного дороже обычного. Совсем
    # запрещать нельзя — в папке бывает больше деталей, чем в эталоне, — но появляться
    # «Датчик вскрытия корпуса 2» при одном отмеченном датчике больше не должен.
    closed = _confirmed_capacity(result, model) >= model.capacity
    extra_price = np.where(closed, config.extra_penalty + config.closed_penalty, config.extra_penalty)
    col_cost = np.concatenate([np.zeros(base_cap.sum()),
                               np.repeat(extra_price, extra_cap)])
    # max_distance — строгая граница: дальше неё категория запрещена. «Неопознано» стоит
    # max_distance + штраф, поэтому фото в пределах границы всегда получает категорию,
    # хотя бы как лишний экземпляр. Штраф влияет только на выбор между категориями.
    name_everything = strict or config.always_propose
    limit = np.inf if name_everything else config.max_distance
    unmatched = 1e5 if name_everything else (config.max_distance + config.extra_penalty)

    base_slots = int(base_cap.sum())

    def assign(distances: np.ndarray):
        """Возвращает (категория для каждого фото, занято ли «лишнее» место сверх состава эталона)."""
        dist = np.where(distances <= limit, distances, np.inf)
        cost = dist[:, col_cat] + col_cost[None, :]
        cost = np.hstack([cost, np.full((n, n), unmatched)])
        cost = np.where(np.isfinite(cost), cost, 1e6)
        row_ind, col_ind = linear_sum_assignment(cost)
        out = np.full(n, -1)
        spare = np.zeros(n, dtype=bool)
        for r, col in zip(row_ind, col_ind):
            if col < len(col_cat):
                out[r] = col_cat[col]
                spare[r] = col >= base_slots
        return out, spare

    assigned, spare = assign(cat_dist)
    # второй проход: теперь известно, где какая категория встречается в папке, и фото,
    # оторвавшееся от своей серии, получает штраф (правило серий)
    penalty = block_penalty(rows, _anchor_categories(result, rows, assigned), model.capacity,
                            config.block_weight, config.block_span)
    if penalty.any():
        cat_dist = cat_dist + penalty
        assigned, spare = assign(cat_dist)
    occupied = used + np.bincount(assigned[assigned >= 0], minlength=n_cat)

    for r, i in enumerate(rows):
        # категория, у которой все места эталона заняты другими фото, доступна этому фото
        # только как лишний экземпляр — на столько же она и менее правдоподобна.
        # Но своё место, доставшееся фото в раскладке, штрафом не облагается: иначе несколько
        # лишних фото обрушивали бы уверенность у всей категории, включая верно опознанные.
        others = occupied - (np.arange(n_cat) == assigned[r])
        full = others >= model.capacity
        if assigned[r] >= 0 and not spare[r]:
            full[assigned[r]] = False
        conf = _confidences(cat_dist[r] + config.extra_penalty * full, config)
        order = np.argsort(-conf, kind="stable")      # все категории по убыванию похожести
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
        if name_everything:
            # имя предлагается всегда, сомнительные видны в предпросмотре как «ПРОВЕРЬТЕ»
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


def _anchor_categories(result: FolderMatch, rows: List[int], assigned: np.ndarray) -> np.ndarray:
    """Категория каждого места папки (-1, если её нет): первый проход, уже названные фото и ручные правки."""
    model = result.model
    anchors = np.full(len(result.photos), -1, dtype=int)
    for r, i in enumerate(rows):
        if assigned[r] >= 0:
            anchors[i] = assigned[r]
    for i, (cat, _, _) in result.fixed.items():
        c = model.cat_index.get(cat.key)
        if c is not None:
            anchors[i] = c
    for i, key in result.manual.items():
        c = model.cat_index.get(key) if key else None
        if c is not None:
            anchors[i] = c
    return anchors


def _runs(photos: List[PhotoMatch], include_named: Optional[Set[str]] = None) -> List[Tuple[Category, List[int]]]:
    """Серии подряд идущих фото одной категории.

    Уже названные фото обычно серии разрывают (их не трогаем). Но если в их категории появились
    новые фото (include_named), они входят в серию наравне с остальными: только так номера
    экземпляров соответствуют порядку съёмки.
    """
    include_named = include_named or set()
    runs: List[Tuple[Category, List[int]]] = []
    current_key = None
    for p in photos:
        if not p.identified:
            key = None
        elif p.status != STATUS_ALREADY_NAMED or p.category.key in include_named:
            key = p.category.key
        else:
            key = None
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


def _side_costs(result: FolderMatch, cat: Category, idx: List[int]) -> np.ndarray:
    """Матрица [фото серии][сторона]: насколько фото похоже на эту сторону детали в эталоне."""
    c = result.model.cat_index.get(cat.key)
    costs = np.zeros((len(idx), cat.sides))
    for k, i in enumerate(idx):
        row = result.feature_row.get(i)
        if row is None or c is None:            # уже названное фото: подсказки нет, стороны равны
            continue
        for side in range(cat.sides):
            d = result.side_dist[row, c, side]
            costs[k, side] = d if np.isfinite(d) else result.cat_dist[row, c]
    # Важна не величина расстояния, а насколько сторона хуже лучшей для этого же фото: у разных
    # фото расстояния в разном масштабе, и без этого «далёкое от всего» фото перевешивало бы
    # остальные. После вычитания минимума фото без явного предпочтения ни на что не влияет.
    return costs - costs.min(axis=1, keepdims=True)


def _present_sides(result: FolderMatch, cat: Category, idx: List[int]) -> int:
    """Маска сторон, которые в этом блоке вообще встречаются (по лучшему совпадению каждого фото).

    Ничья считается за присутствие: если расстояния до сторон одинаковы, подсказки нет
    и присутствуют все стороны.
    """
    costs = _side_costs(result, cat, idx)
    present = 0
    for side in range(cat.sides):
        if costs.size and bool(np.any(costs[:, side] <= 1e-9)):
            present |= 1 << side
    return present or (1 << cat.sides) - 1


def _split_run(result: FolderMatch, cat: Category, idx: List[int],
               shot_here: Optional[int] = None) -> List[List[Tuple[int, int]]]:
    """Делит серию на экземпляры и назначает каждому фото сторону: [[(фото, сторона), ...], ...].

    Стороны детали различаются на вид (у съёмного накопителя это две стороны платы и корпус),
    поэтому разбиение ищется не «по cat.sides подряд», а по тому, на какую сторону эталона
    похоже каждое фото. Порядок сторон внутри экземпляра любой: на реальных съёмках деталь
    снимают то с корпуса, то с платы, и номера «фото 1, 2, 3» порядку съёмки не подчиняются
    (проверено на эталоне заказчика). Внутри экземпляра сторона не повторяется — по этому
    признаку и находится граница между деталями.

    Перебор всех вариантов сразу: динамическое программирование, состояние — набор сторон,
    уже занятых в текущем экземпляре. Недостающая сторона разрешена, но стоит side_skip_penalty,
    иначе выгодно было бы дробить серию на неполные экземпляры. Благодаря этому лишнее или
    недостающее фото сдвигает только свой экземпляр, а не всю серию: раньше один такой кадр
    сбивал нумерацию всех последующих деталей.

    Паузы между кадрами для деления намеренно не используются: между разными деталями пауза
    бывает такой же короткой, как между сторонами одной.
    """
    sides = cat.sides
    if sides == 1 or not idx:
        return [[(i, 1)] for i in idx]

    costs = _side_costs(result, cat, idx)
    penalty = result.config.side_skip_penalty
    states = 1 << sides
    # shot_here — стороны, которые в этом блоке снимали. Остальные сняты отдельным блоком
    # (фотограф снял сначала все платы, а корпуса потом), и за их отсутствие платить не надо.
    expected = (1 << sides) - 1 if shot_here is None else shot_here
    missing = np.array([bin(expected & ~mask).count("1") for mask in range(states)], dtype=float)

    best = np.full(states, np.inf)
    for side in range(sides):        # серия может начаться с любой стороны: что было раньше, неизвестно
        best[1 << side] = costs[0, side]
    back: List[Dict[int, Tuple[int, int, bool]]] = []   # состояние → (прошлое, сторона, новый экземпляр)

    for k in range(1, len(idx)):
        nxt, choice = np.full(states, np.inf), {}
        for mask in range(states):
            if not np.isfinite(best[mask]):
                continue
            for side in range(sides):
                bit = 1 << side
                if not mask & bit:                      # сторона свободна — то же изделие
                    value = best[mask] + costs[k, side]
                    if value < nxt[mask | bit]:
                        nxt[mask | bit] = value
                        choice[mask | bit] = (mask, side, False)
                # новое изделие: платим за стороны, которых у предыдущего не оказалось
                value = best[mask] + costs[k, side] + penalty * missing[mask]
                if value < nxt[bit]:
                    nxt[bit] = value
                    choice[bit] = (mask, side, True)
        best, back = nxt, back + [choice]

    mask = int(np.argmin(best))
    path: List[Tuple[int, bool]] = []                   # (сторона, начинает новое изделие)
    for k in range(len(idx) - 1, 0, -1):
        previous, side, is_new = back[k - 1][mask]
        path.append((side, is_new))
        mask = previous
    path.append((mask.bit_length() - 1, True))          # первое фото серии
    path.reverse()

    groups: List[List[Tuple[int, int]]] = []
    for i, (side, is_new) in zip(idx, path):
        if is_new or not groups:
            groups.append([])
        groups[-1].append((i, side + 1))
    return groups


def _merge_side_blocks(instances: List[List[List[Tuple[int, int]]]]) -> List[List[Tuple[int, int]]]:
    """Склеивает блоки, снятые «по ракурсам»: сперва все платы, потом все корпуса.

    Бывает, что фотограф снимает не деталь за деталью, а ракурс за ракурсом: сначала по две
    стороны платы у всех накопителей подряд, потом отдельным блоком — все корпуса. Тогда
    в папке получается два блока одной категории: в первом у каждого изделия стороны 1 и 2,
    во втором — сторона 3, и порядок изделий в блоках один и тот же.

    Такие блоки узнаются по признакам: изделий поровну и стороны не пересекаются. Тогда i-е
    изделие первого блока и i-е изделие второго — одна и та же деталь. Если стороны
    пересекаются (например, два блока по двум сторонам), это разные детали, и склейки нет.

    instances — списки изделий по блокам, в порядке съёмки.
    """
    merged: List[List[List[Tuple[int, int]]]] = []
    taken = set()
    for a, block in enumerate(instances):
        if a in taken:
            continue
        joined = [list(group) for group in block]
        sides = [{side for _, side in group} for group in joined]
        for b in range(a + 1, len(instances)):
            other = instances[b]
            if b in taken or len(joined) < 2 or len(other) < 2:
                continue
            pairs = min(len(joined), len(other))
            # изделий может оказаться не поровну: одного корпуса не сняли, лишний кадр попал
            # в блок. Тогда склеиваем по порядку, сколько совпало, — «первый корпус к первой
            # плате». Если блоки расходятся больше чем вдвое, это разные детали, а не ракурсы.
            if pairs * 2 < max(len(joined), len(other)):
                continue
            other_sides = [{side for _, side in group} for group in other]
            if any(sides[k] & other_sides[k] for k in range(pairs)):
                continue
            for k in range(pairs):
                joined[k].extend(other[k])
                sides[k] |= other_sides[k]
            joined.extend(list(group) for group in other[pairs:])
            sides.extend({side for _, side in group} for group in other[pairs:])
            taken.add(b)
        merged.append(joined)

    out = [group for block in merged for group in block]
    for group in out:
        group.sort(key=lambda pair: pair[0])
    out.sort(key=lambda group: group[0][0])          # нумерация — по первому фото изделия
    return out


def renumber(result: FolderMatch) -> None:
    """Пересчитывает экземпляры, стороны и новые имена. Вызывается и после ручной правки категорий."""
    photos = result.photos
    # категории, в которых появились новые фото: их нумерацию считаем заново по порядку съёмки,
    # включая уже названные фото (иначе номера не будут соответствовать порядку)
    fresh = {p.category.key for p in photos if p.identified and p.status != STATUS_ALREADY_NAMED}
    include_named = fresh if result.config.renumber_existing else set()

    used: Dict[str, Set[int]] = {}
    for p in photos:
        if p.status == STATUS_ALREADY_NAMED:
            p.new_name = None
            if p.category.key not in include_named:
                used.setdefault(p.category.key, set()).add(p.instance)
        elif not p.identified:
            p.instance = p.side = p.new_name = None

    result.warnings = [w for w in result.warnings if not w.startswith("Неполный комплект")]
    next_free: Dict[str, int] = {}
    runs: Dict[str, Tuple[Category, List[List[int]]]] = {}
    for cat, idx in _runs(photos, include_named):
        runs.setdefault(cat.key, (cat, []))[1].append(idx)

    blocks: Dict[str, Tuple[Category, List[List[List[Tuple[int, int]]]]]] = {}
    for key, (cat, idx_list) in runs.items():
        present = [_present_sides(result, cat, idx) for idx in idx_list]
        groups = []
        for k, idx in enumerate(idx_list):
            elsewhere = 0
            for j, mask in enumerate(present):
                if j != k:
                    elsewhere |= mask
            # стороны, которых здесь нет, но которые сняты в другом блоке этой же категории,
            # с этого блока не спрашиваются
            shot_here = present[k] | (((1 << cat.sides) - 1) & ~elsewhere)
            groups.append(_split_run(result, cat, idx, shot_here))
        blocks[key] = (cat, groups)

    for cat, per_block in blocks.values():
        for group in _merge_side_blocks(per_block):
            instance = next_free.get(cat.key, 1)
            while instance in used.get(cat.key, set()):
                instance += 1
            next_free[cat.key] = instance + 1
            for i, side in group:
                p = photos[i]
                p.instance, p.side = instance, side
                new_name = cat.format_name(instance, side, os.path.splitext(p.filename)[1])
                p.new_name = None if new_name == _nfc(p.filename) else new_name
                if p.new_name and p.status == STATUS_ALREADY_NAMED:
                    p.note = "перенумерация по порядку съёмки"
            if len(group) != cat.sides:
                result.warnings.append(
                    "Неполный комплект сторон: %s %d — %d фото из %d (%s)"
                    % (cat.name, instance, len(group), cat.sides,
                       ", ".join(photos[i].filename for i, _ in group)))


def add_category(result: FolderMatch, name: str, sides: int = 1) -> str:
    """Категория, которой нет в эталоне: пользователь встретил деталь, не попавшую в образец.

    Возвращает ключ, с которым её можно задать фото (set_category). Имена таких категорий
    строятся по тому же шаблону, что и эталонные: «Категория N фото K».
    """
    key = category_key(name)
    if key in result.model.reference.categories or key in result.extra_categories:
        return key
    result.extra_categories[key] = Category(name=name.strip(), numbered=False, has_side_suffix=sides > 1,
                                            sides=max(1, int(sides)), instances=0)
    return key


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
