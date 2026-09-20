"""Анализ папки проекта: какие папки похожи друг на друга и сколько эталонов нужно.

1. Находятся все папки с фото (только .jpg, без рентгенограмм).
2. Папки, где почти все фото уже названы по шаблону «Категория N фото K», считаются готовыми эталонами.
3. Сходство двух папок — насколько фото одной находят близких «двойников» в другой:
   медиана расстояния до ближайшего фото другой папки, в обе стороны.
4. Папки объединяются в группы (иерархическая кластеризация, средняя связь) до порога
   group_threshold. На реальных проектах порог 0.30 разделил папки по условиям съёмки.
5. Для каждой группы предлагается эталон: готовый эталон группы, если он есть,
   иначе папка с наибольшим числом фото.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from . import features as feat
from .reference import category_key, is_template_name, parse_name
from .scanner import FolderScan, ScanError, find_photo_folders, list_photos

REFERENCE_SHARE = 0.9       # доля фото с именами по шаблону, чтобы папка считалась эталоном
REFERENCE_MIN_CATEGORIES = 3  # ...и разных категорий не меньше этого (и не меньше 5% от числа фото):
                              # иначе это общая нумерация вроде «File 1», «File 2», а не эталон


@dataclass
class FolderInfo:
    scan: FolderScan
    named_share: float = 0.0  # доля фото, уже названных по шаблону
    categories: int = 0       # число разных категорий среди этих имён
    unreadable: int = 0

    @property
    def path(self) -> str:
        return self.scan.path

    @property
    def count(self) -> int:
        return len(self.scan.photos)

    @property
    def is_reference(self) -> bool:
        return (self.count > 0 and self.named_share >= REFERENCE_SHARE
                and self.categories >= max(REFERENCE_MIN_CATEGORIES, 0.05 * self.count))


@dataclass
class FolderGroup:
    folders: List[FolderInfo]
    reference: FolderInfo              # предлагаемый эталон группы
    reference_is_ready: bool           # эталон уже переименован по шаблону
    reference_manual: bool = False     # эталон выбран пользователем (не менять автоматически)

    @property
    def targets(self) -> List[FolderInfo]:
        return [f for f in self.folders if f is not self.reference and not f.is_reference]


@dataclass
class ProjectAnalysis:
    root: str
    folders: List[FolderInfo]
    groups: List[FolderGroup]
    distances: np.ndarray              # сходство папок (меньше — похожее), порядок как в folders
    errors: List[ScanError] = field(default_factory=list)


def folder_info(scan: FolderScan) -> FolderInfo:
    named = [name for name in scan.photos if is_template_name(name)]
    categories = {category_key(parse_name(name).category) for name in named}
    return FolderInfo(scan, len(named) / max(len(scan.photos), 1), len(categories))


def folder_distances(folder_features: Sequence[Sequence[feat.Features]], extractor: feat.FeatureExtractor,
                     weights: Dict[str, float], sample_step: int = 7) -> np.ndarray:
    """Матрица «несходства» папок. Масштабы признаков — по выборке фото всех папок."""
    n = len(folder_features)
    distances = np.zeros((n, n))
    sample = [f for fs in folder_features for f in fs][::sample_step]
    if n < 2 or len(sample) < 2:
        return distances
    comps = extractor.distance_components(sample, sample)
    off_diagonal = ~np.eye(len(sample), dtype=bool)
    scales = feat.component_scales(comps, off_diagonal)
    for i in range(n):
        for j in range(i + 1, n):
            if not folder_features[i] or not folder_features[j]:
                distances[i, j] = distances[j, i] = 1.0
                continue
            c = extractor.distance_components(folder_features[i], folder_features[j])
            d = feat.combine({k: v * scales[k] for k, v in c.items()}, weights)
            distances[i, j] = distances[j, i] = (np.median(d.min(axis=1)) + np.median(d.min(axis=0))) / 2
    return distances


def cluster(distances: np.ndarray, threshold: float) -> List[int]:
    """Номер группы для каждой папки (группы нумеруются по первому появлению)."""
    n = distances.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [0]
    labels = fcluster(linkage(squareform(distances, checks=False), "average"), threshold, "distance")
    order: Dict[int, int] = {}
    return [order.setdefault(label, len(order)) for label in labels]


def make_groups(folders: List[FolderInfo], labels: List[int]) -> List[FolderGroup]:
    groups = []
    for g in range(max(labels) + 1 if labels else 0):
        members = [f for f, label in zip(folders, labels) if label == g]
        ready = [f for f in members if f.is_reference]
        if ready:
            reference = max(ready, key=lambda f: (f.named_share, f.count))
        else:
            reference = max(members, key=lambda f: f.count)
        groups.append(FolderGroup(members, reference, bool(ready)))
    return groups


def analyze_project(
    roots: Sequence[str],
    extractor: feat.FeatureExtractor,
    weights: Dict[str, float],
    threshold: float = 0.30,
    feature_source: Optional[Callable] = None,
    sort: str = "name",
    date_source=None,
    on_folder: Optional[Callable[[int, int, str], None]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> ProjectAnalysis:
    """Полный анализ: поиск папок, чтение признаков (через кэш, если передан feature_source),
    сходство и группы. on_folder(номер, всего, путь) — перед чтением каждой папки."""
    feature_source = feature_source or feat.extract_many
    scans, errors = [], []
    for root in roots:
        result = find_photo_folders(root, sort=sort, date_source=date_source)
        scans.extend(result.folders)
        errors.extend(result.errors)

    folders, folder_features = [], []
    for n, scan in enumerate(scans, start=1):
        if on_folder:
            on_folder(n, len(scans), scan.path)
        info = folder_info(scan)
        fs, errs = feature_source(extractor, scan.photo_paths, on_progress)
        info.unreadable = len(errs)
        folders.append(info)
        folder_features.append([f for f in fs if f is not None])

    distances = folder_distances(folder_features, extractor, weights)
    groups = make_groups(folders, cluster(distances, threshold))
    return ProjectAnalysis(", ".join(roots), folders, groups, distances, errors)


# --- правка групп пользователем -------------------------------------------------------------

SIMILARITY_HIGH = 0.25   # несходство с эталоном группы: до этого — «высокое» сходство
SIMILARITY_MEDIUM = 0.30  # до этого — «среднее», дальше — «низкое» (порог группировки)


def _pick_reference(group: FolderGroup) -> None:
    """Выбирает эталон группы, если пользователь не выбрал его сам (или выбранный ушёл из группы)."""
    if group.reference_manual and group.reference in group.folders:
        group.reference_is_ready = group.reference.is_reference
        return
    group.reference_manual = False
    ready = [f for f in group.folders if f.is_reference]
    if ready:
        group.reference = max(ready, key=lambda f: (f.named_share, f.count))
    else:
        group.reference = max(group.folders, key=lambda f: f.count)
    group.reference_is_ready = bool(ready)


def group_of(analysis: ProjectAnalysis, folder: FolderInfo) -> int:
    for k, g in enumerate(analysis.groups):
        if any(f is folder for f in g.folders):
            return k
    raise ValueError("папка не входит ни в одну группу")


def move_folder(analysis: ProjectAnalysis, folder: FolderInfo, target: Optional[int]) -> int:
    """Переносит папку в группу target (None — в новую группу). Пустые группы удаляются.
    Возвращает номер группы, в которой оказалась папка."""
    source = analysis.groups[group_of(analysis, folder)]
    if target is not None and analysis.groups[target] is source:
        return target
    source.folders = [f for f in source.folders if f is not folder]
    if target is None:
        destination = FolderGroup([folder], folder, folder.is_reference)
        analysis.groups.append(destination)
    else:
        destination = analysis.groups[target]
        destination.folders.append(folder)
        destination.folders.sort(key=lambda f: analysis.folders.index(f))
    if source.folders:
        _pick_reference(source)
    else:
        analysis.groups.remove(source)
    _pick_reference(destination)
    return analysis.groups.index(destination)


def set_reference(analysis: ProjectAnalysis, folder: FolderInfo) -> None:
    """Пользователь выбрал эталон группы."""
    group = analysis.groups[group_of(analysis, folder)]
    group.reference = folder
    group.reference_manual = True
    group.reference_is_ready = folder.is_reference


def refresh_names(analysis: ProjectAnalysis, sort: str = "name", date_source=None) -> None:
    """Перечитывает имена файлов (не сами фото) — например, после создания эталона в редакторе."""
    for k, info in enumerate(analysis.folders):
        try:
            fresh = folder_info(list_photos(info.path, sort=sort, date_source=date_source))
        except OSError:
            continue
        info.scan, info.named_share, info.categories = fresh.scan, fresh.named_share, fresh.categories
    for group in analysis.groups:
        _pick_reference(group)


def similarity_to_reference(analysis: ProjectAnalysis, folder: FolderInfo) -> Optional[float]:
    """Несходство папки с эталоном её группы (None для самого эталона)."""
    group = analysis.groups[group_of(analysis, folder)]
    if group.reference is folder:
        return None
    return float(analysis.distances[analysis.folders.index(folder), analysis.folders.index(group.reference)])


def similarity_label(distance: Optional[float]) -> str:
    if distance is None:
        return ""
    if distance <= SIMILARITY_HIGH:
        return "высокое"
    if distance <= SIMILARITY_MEDIUM:
        return "среднее"
    return "низкое"


def describe(analysis: ProjectAnalysis) -> str:
    lines = ["Папок с фото: %d, групп: %d" % (len(analysis.folders), len(analysis.groups))]
    for k, g in enumerate(analysis.groups, start=1):
        status = "готовый эталон" if g.reference_is_ready else "нужно переименовать вручную"
        lines.append("Группа %d (%d папок). Эталон: %s — %s" % (k, len(g.folders), g.reference.path, status))
        for f in g.folders:
            mark = "  [эталон]" if f is g.reference else ("  [уже по шаблону]" if f.is_reference else "")
            lines.append("    %4d фото  %s%s" % (f.count, f.path, mark))
    for e in analysis.errors:
        lines.append("ОШИБКА ДОСТУПА: %s — %s" % (e.path, e.message))
    return "\n".join(lines)
