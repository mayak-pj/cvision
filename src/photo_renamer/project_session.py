"""Логика первого окна — без окон: анализ проекта, группы и папки, отметки, строки таблицы.

Модуль не импортирует tkinter.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import grouping
from .cache import PhotoCache

# Галочка эталона: её ставит человек щелчком по столбцу, а программа — на самую
# переименованную папку группы
MARK_REFERENCE = "✓"
MARK_YES = "да"
MARK_NO = "нет"


class Cancelled(Exception):
    """Задача прервана пользователем."""


def percent(share: float) -> int:
    """Доля в процентах. 100 пишется только когда названо всё: 99.6 % — это ещё не готово."""
    value = int(round(share * 100))
    return min(value, 99) if share < 1 else value


@dataclass
class Row:
    """Строка таблицы групп: столбцы reference, rename, count, status, similarity."""

    text: str
    values: Tuple
    tags: Tuple[str, ...] = ()
    group: Optional[int] = None
    folder: Optional[grouping.FolderInfo] = None
    children: List["Row"] = field(default_factory=list)


class ProjectSession:
    def __init__(self, app_config):
        self.app_config = app_config
        self.analysis: Optional[grouping.ProjectAnalysis] = None
        self.project_root = ""

    # --- состояние между запусками ----------------------------------------------------------

    def _state_path(self) -> str:
        return os.path.join(os.path.dirname(self.app_config.cache_path()), "state.json")

    def load_state(self) -> dict:
        try:
            with open(self._state_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_state(self, project: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._state_path()), exist_ok=True)
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump({"project": project}, f, ensure_ascii=False)
        except OSError:
            pass

    # --- анализ -------------------------------------------------------------------------------

    def analysis_job(self, root: str, make_cache: Callable = PhotoCache) -> Callable:
        """Задача для фонового потока: только чтение файлов, ни одного обращения к окну.

        Возвращает job(post, cancelled). post получает ("folder", номер, всего, путь)
        и ("progress", готово, всего); при отмене задача поднимает Cancelled.
        """
        app_config = self.app_config

        def job(post, cancelled):
            cache = make_cache(app_config.cache_path(), app_config.cache_max_mb)

            def folder_started(number, total, path):
                post(("folder", number, total, path))
                if cancelled():
                    raise Cancelled()

            def photo_read(done, total):
                post(("progress", done, total))
                if cancelled():
                    raise Cancelled()

            extractor = app_config.extractor()
            try:
                return grouping.analyze_project(
                    [root], extractor, app_config.match.for_features(extractor.encoder is not None),
                    app_config.group_threshold,
                    feature_source=cache.extract_many, sort=app_config.sort, date_source=cache.shot_time,
                    on_folder=folder_started, on_progress=photo_read)
            finally:
                cache.close()

        return job

    def summary(self) -> str:
        a = self.analysis
        need = sum(not g.reference_is_ready for g in a.groups)
        text = "Папок с фото: %d, групп: %d. " % (len(a.folders), len(a.groups))
        text += ("Нужно создать эталонов: %d." % need) if need else "Все эталоны готовы."
        if a.errors:
            text += " Недоступных папок: %d." % len(a.errors)
        return text

    # --- отображение ------------------------------------------------------------------------

    def display_path(self, path: str) -> str:
        root = self.project_root
        if root:
            try:
                rel = os.path.relpath(path, root)
            except ValueError:  # другой диск на Windows
                return path
            if not rel.startswith(".."):
                return os.path.join(os.path.basename(os.path.normpath(root)), rel) if rel != "." else \
                    os.path.basename(os.path.normpath(root))
        return path

    @staticmethod
    def rename_mark(group: grouping.FolderGroup, folder: grouping.FolderInfo) -> str:
        """Отметка «переименовывать ли папку». У эталона её нет: он образец, а не цель."""
        if folder is group.reference:
            return ""
        return MARK_YES if folder.will_rename else MARK_NO

    @staticmethod
    def folder_status(folder: grouping.FolderInfo) -> str:
        """Насколько папка переименована. В столбце только число: его смысл написан
        в заголовке «Обработано», а слово в каждой строке лишнее."""
        status = "%d%%" % percent(folder.named_share)
        if folder.unreadable:
            status += "   не прочитано фото: %d" % folder.unreadable
        return status

    def rows(self) -> List[Row]:
        """Строки таблицы: группы с папками внутри, в конце — недоступные папки."""
        if self.analysis is None:
            return []
        rows: List[Row] = []
        for k, group in enumerate(self.analysis.groups):
            ready = "эталон готов" if group.reference_is_ready else "нужно создать эталон"
            parent = Row("Группа %d — %s" % (k + 1, ready), ("", "", "", "папок: %d" % len(group.folders), ""),
                         ("group",) if group.reference_is_ready else ("group", "not_ready"), group=k)
            rows.append(parent)
            for folder in group.folders:
                similarity = grouping.similarity_label(grouping.similarity_to_reference(self.analysis, folder))
                tags = []
                if folder is group.reference:
                    tags.append("reference")
                    if not folder.is_reference:
                        tags.append("not_ready")
                if similarity == "низкое":
                    tags.append("low")
                parent.children.append(Row(self.display_path(folder.path),
                                           (MARK_REFERENCE if folder is group.reference else "",
                                            self.rename_mark(group, folder),
                                            folder.count, self.folder_status(folder), similarity),
                                           tuple(tags), group=k, folder=folder))
        for e in self.analysis.errors:
            rows.append(Row("Недоступна: %s" % self.display_path(e.path), ("", "", "", e.message, ""), ("low",)))
        return rows

    def available_actions(self, folder: Optional[grouping.FolderInfo], group: Optional[int]) -> Dict[str, bool]:
        """Какие действия доступны для выбранной строки. В процессе анализа folder и group — None."""
        is_ref = folder is not None and self.analysis.groups[group].reference is folder
        return {
            "make_reference": folder is not None and not is_ref,
            "move": folder is not None,
            "open": folder is not None,
            "edit_reference": group is not None,
            "rename_group": group is not None and self.analysis.groups[group].reference_is_ready,
        }

    # --- действия ------------------------------------------------------------------------------

    def toggle_rename(self, folder: grouping.FolderInfo) -> bool:
        """Снимает или ставит отметку «переименовывать эту папку». False — у эталона её нет.

        Нужна, когда программа сочла папку уже названной, а на деле её не переименовывали
        (или наоборот — папку назвали вручную и трогать её не надо).
        """
        group = self.analysis.groups[grouping.group_of(self.analysis, folder)]
        if folder is group.reference:
            return False
        folder.include = not folder.will_rename
        return True

    def move_targets(self, folder: grouping.FolderInfo) -> List[Tuple[int, str, bool]]:
        """Куда можно перенести папку: (номер группы, подпись, доступно ли)."""
        current = grouping.group_of(self.analysis, folder)
        return [(k, "Группа %d (папок: %d)" % (k + 1, len(group.folders)), k != current)
                for k, group in enumerate(self.analysis.groups)]

    def move_folder(self, folder: grouping.FolderInfo, target: Optional[int]) -> None:
        grouping.move_folder(self.analysis, folder, target)

    def make_reference(self, folder: grouping.FolderInfo) -> None:
        grouping.set_reference(self.analysis, folder)

    def refresh(self, make_cache: Callable = PhotoCache) -> None:
        cache = make_cache(self.app_config.cache_path(), self.app_config.cache_max_mb)
        try:
            grouping.refresh_names(self.analysis, sort=self.app_config.sort, date_source=cache.shot_time)
        finally:
            cache.close()
