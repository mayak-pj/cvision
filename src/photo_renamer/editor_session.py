"""Логика редактора эталона — без окон: порядок фото, переименование выделенных, отмена.

Правила самих имён — в editing.py. Модуль не импортирует tkinter.
"""

import os
from typing import List, Optional, Sequence, Tuple

from . import editing, oplog, renamer
from .names import NamesList
from .reference import is_template_name, load_reference
from .scanner import list_photos, natural_key


class EditorSession:
    def __init__(self, folder: str, app_config, names: NamesList, cache):
        self.folder = folder
        self.app_config = app_config
        self.names = names
        self.cache = cache
        self.log = oplog.RunLog(app_config.logs_path(), "эталон_" + oplog.folder_label(folder))
        self.photos: List[str] = []          # имена в порядке съёмки (порядок не меняется)
        self.shown: List[str] = []           # те же имена в порядке показа (см. display_order)
        self.history: List[List[renamer.RenameItem]] = []
        self.order_differs = False

    def load_folder(self) -> None:
        """Читает список фото. Ошибка чтения (OSError) проходит к вызывающему."""
        self.photos = []
        scan = list_photos(self.folder, sort=self.app_config.sort, date_source=self.cache.shot_time)
        self.photos = list(scan.photos)
        self.order_differs = scan.order_differs
        self.shown = self.display_order()

    def display_order(self) -> List[str]:
        """Порядок показа: названные — по именам, ещё не названные — в порядке съёмки следом.

        Так стороны и экземпляры одной детали («Кабель соединительный 1…4») стоят рядом, и
        видно, что получилось, а неназванные кадры собраны в конце — с ними и работают.
        Порядок съёмки (self.photos) при этом не меняется: по нему делятся стороны
        и нумеруются экземпляры.
        """
        named = [name for name in self.photos if is_template_name(name)]
        rest = [name for name in self.photos if not is_template_name(name)]
        return sorted(named, key=natural_key) + rest

    def items(self) -> List[Tuple[str, str]]:
        return [(name, os.path.join(self.folder, name)) for name in self.shown]

    def template_flags(self) -> List[bool]:
        return [is_template_name(name) for name in self.shown]

    def learn_from_reference(self) -> bool:
        """Если в папке уже есть названные фото, запоминаем их категории и число ракурсов.

        True — список категорий пополнился и его нужно показать заново.
        """
        try:
            reference = load_reference(self.folder)
        except OSError:
            return False
        categories = [(c.name, c.sides) for c in reference.categories.values()
                      if any(is_template_name(p.filename) for p in c.photos)]
        if not categories:
            return False
        self.names.learn_from(categories)
        self.names.save()
        return True

    def status_text(self, loading: Optional[Tuple[int, int]] = None) -> str:
        named = sum(self.template_flags())
        text = "Фото: %d, названо по шаблону: %d, осталось: %d" % (len(self.photos), named, len(self.photos) - named)
        if self.order_differs:
            text += "   (порядок съёмки не совпадает с порядком имён — показан порядок съёмки)"
        if loading:
            text += "   загрузка миниатюр: %d из %d" % loading
        return text

    def add_name(self, typed: str, sides: int) -> str:
        """Добавляет категорию в список и возвращает её имя. Неверное имя — ValueError."""
        name = self.names.add(typed, sides)
        self.names.save()
        return name

    def remove_name(self, name: str) -> None:
        self.names.remove(name)
        self.names.save()

    def plan_rename(self, selected: Sequence[str], name: str, sides: int) -> editing.Assignment:
        """Что получится, если назвать выделенные фото. На диске ничего не меняется."""
        return editing.assign(self.folder, self.photos, selected, name, sides)

    def apply_rename(self, assignment: editing.Assignment, name: str,
                     sides: int) -> Tuple[List[renamer.RenameItem], List[renamer.RenameItem]]:
        """Выполняет план и возвращает (переименованные, неудавшиеся)."""
        renamer.apply_plan(assignment.plan, self.log)
        done = [item for item in assignment.plan.items if item.result == oplog.RESULT_OK]
        failed = [item for item in assignment.plan.items if item.result == oplog.RESULT_ERROR]
        if done:
            self.history.append(done)
        self.names.remember_sides(name, sides)
        self.names.save()
        return done, failed

    def apply_renames(self, done: Sequence[renamer.RenameItem]) -> List[int]:
        """Обновляет имена и порядок показа. Возвращает, откуда в прежнем порядке взята каждая строка."""
        mapping = {item.old: item.new for item in done}
        self.photos = [mapping.get(name, name) for name in self.photos]
        was = {mapping.get(name, name): index for index, name in enumerate(self.shown)}
        self.shown = self.display_order()
        return [was[name] for name in self.shown]

    def undo(self) -> Optional[List[renamer.RenameItem]]:
        """Отменяет последнее переименование. Возвращает возвращённые пункты или None, если отменять нечего."""
        if not self.history:
            return None
        plan = editing.undo_plan(self.folder, self.history.pop())
        renamer.apply_plan(plan, self.log)
        return [item for item in plan.items if item.result == oplog.RESULT_OK]

    def close(self) -> None:
        self.log.close()
        self.cache.close()
