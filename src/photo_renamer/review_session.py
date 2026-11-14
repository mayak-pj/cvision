"""Логика пакетного переименования группы папок — без окон.

Здесь всё, что не зависит от библиотеки интерфейса: очередь папок, порядок строк, ручные правки
и их отмена, подбор вариантов, число ракурсов, переименование и его отмена. Окно только показывает
состояние и передаёт сюда действия человека. Модуль не импортирует tkinter.
"""

import os
from typing import Callable, Dict, List, Optional, Tuple

from . import config as cfg, grouping, matcher, names as names_mod, oplog, renamer
from .cache import PhotoCache
from .reference import category_key, load_reference
from .report import build_report, format_report
from .scanner import natural_key

# Размер миниатюр в строках таблицы: по умолчанию крупный, меняется Ctrl+колесом и кнопками
# «−» и «+». Значение запоминается в настройках. Верхняя граница — размер миниатюры в кэше:
# крупнее пришлось бы заново читать исходные файлы (на сетевых дисках это долго).
MIN_THUMB, MAX_THUMB, THUMB_STEP = 48, 320, 24
ALTERNATIVES = 3          # сколько вариантов показывать под цифрами; следующая цифра — отказ
STATUS_TEXT = {
    matcher.STATUS_OK: "распознано",
    matcher.STATUS_SIDE_RULE: "по правилу сторон",
    matcher.STATUS_CHECK: "ПРОВЕРЬТЕ",
    matcher.STATUS_MANUAL: "выбрано вручную",
    matcher.STATUS_ALREADY_NAMED: "уже названо",
    matcher.STATUS_LOW_CONFIDENCE: "НЕ УВЕРЕНА",
    matcher.STATUS_NO_MATCH: "НЕ ПОХОЖЕ НИ НА ЧТО",
    matcher.STATUS_EXCLUDED: "не переименовывать",
    matcher.STATUS_UNREADABLE: "не прочитано",
}

SORT_ORDER, SORT_PHOTO, SORT_NEW, SORT_CONFIDENCE, SORT_STATUS = "order", "photo", "new", "confidence", "status"
SORT_KEYS = {
    SORT_ORDER: lambda p: (p.index,),
    SORT_PHOTO: lambda p: (natural_key(p.filename), p.index),
    SORT_NEW: lambda p: (natural_key(p.new_name or ""), p.index),
    SORT_CONFIDENCE: lambda p: (p.confidence, p.index),
    SORT_STATUS: lambda p: (STATUS_TEXT.get(p.status, ""), p.index),
}

KIND_DOUBT, KIND_MANUAL, KIND_SKIP = "doubt", "manual", "skip"

_ABSENT = object()                       # признак «ручной правки для этого фото не было»


def clamp_thumb_size(size: int) -> int:
    return max(MIN_THUMB, min(MAX_THUMB, int(size)))


def row_values(photo) -> Tuple[str, str, str]:
    """Что показать в строке таблицы: новое имя, уверенность, состояние."""
    new = photo.new_name or ("" if photo.identified else "— не переименовывать —")
    return new, "%.0f%%" % photo.confidence if photo.confidence else "", STATUS_TEXT.get(photo.status, "")


def row_kind(photo) -> Optional[str]:
    """Чем выделить строку: сомнительное фото, ручная правка, пропущенное или ничем."""
    if photo.status in (matcher.STATUS_LOW_CONFIDENCE, matcher.STATUS_NO_MATCH, matcher.STATUS_SIDE_RULE,
                        matcher.STATUS_CHECK):
        return KIND_DOUBT
    if photo.status == matcher.STATUS_MANUAL:
        return KIND_MANUAL
    if photo.status in (matcher.STATUS_EXCLUDED, matcher.STATUS_UNREADABLE):
        return KIND_SKIP
    return None


def is_doubtful(photo) -> bool:
    return photo.status in (matcher.STATUS_CHECK, matcher.STATUS_LOW_CONFIDENCE, matcher.STATUS_NO_MATCH,
                            matcher.STATUS_SIDE_RULE, matcher.STATUS_UNREADABLE)


def unresolved_question(unresolved: int, pending: int) -> str:
    return ("Не опознано фото: %d. Их имена останутся прежними, а у остальных номера\n"
            "будут идти по порядку съёмки без учёта пропущенных — потом будет сложно\n"
            "понять, какой ракурс к какой детали.\n\n"
            "Лучше разобрать их сейчас: включите «Показать только требующие проверки»\n"
            "и назначьте категории клавишами 1 и 2.\n\n"
            "Всё равно переименовать %d файл(ов)?") % (unresolved, pending)


class ReviewSession:
    def __init__(self, group: grouping.FolderGroup, app_config, names=None):
        self.group = group
        self.app_config = app_config
        # список категорий общий с редактором эталона: иначе два окна держат по своей копии
        # и то, что добавлено в одном, затирается сохранением из другого
        self.names = names if names is not None else names_mod.NamesList(app_config.names_path())
        self.cache = PhotoCache(app_config.cache_path(), app_config.cache_max_mb)
        self.log = oplog.RunLog(app_config.logs_path(), oplog.folder_label(group.reference.path) + "_группа")
        self.queue: List[grouping.FolderInfo] = list(group.targets)
        self.total = len(self.queue)
        self.done_folders = 0
        self.model: Optional[matcher.ReferenceModel] = None
        self.match: Optional[matcher.FolderMatch] = None
        self.folder: Optional[grouping.FolderInfo] = None
        self.categories: List[str] = []
        self.reference_categories: List[str] = []
        self.reports: List[str] = []         # отчёты по папкам: показываются одним окном в конце
        self.last_plan: Optional[renamer.RenamePlan] = None
        # по умолчанию сортируем по новому имени: одинаковые детали оказываются рядом,
        # и проверять их удобнее, чем в порядке съёмки
        self.sort_column, self.sort_reverse = SORT_NEW, False
        # (категория, подсказанное число ракурсов): по нему видно, задал ли человек своё
        self._suggested_sides: Optional[Tuple[str, int]] = None
        self._undo: List[List[tuple]] = []      # шаги ручных правок для Ctrl+Z
        self._display_order: List[int] = []     # порядок строк, зафиксированный до следующей сортировки

    # --- ход обработки ------------------------------------------------------------------

    def load_model(self, progress: Callable[[int, int], None]) -> None:
        """Читает эталон. Ошибки чтения (OSError) и отмена из progress проходят к вызывающему."""
        reference = load_reference(self.group.reference.path)
        self.model = matcher.ReferenceModel.build(reference, self.app_config.extractor(), progress,
                                                  feature_source=self.cache.extract_many)
        self.reference_categories = [c.name for c in self.model.categories]
        self.categories = self.all_categories()
        self.log.write_text("Эталон: %s\nПапок в группе: %d" % (self.group.reference.path, self.total))
        for warning in self.model.warnings:
            self.log.write_text("ВНИМАНИЕ: " + warning)

    def advance(self) -> bool:
        """Берёт следующую папку из очереди. False — папок больше нет."""
        if not self.queue:
            return False
        self.folder = self.queue.pop(0)
        self.done_folders += 1
        # правки прошлой папки здесь не отменяются: номера фото в новой папке значат другое,
        # и Ctrl+Z подставил бы категорию чужого фото
        self._undo.clear()
        return True

    def header_text(self) -> str:
        return "Папка %d из %d: %s" % (self.done_folders, self.total, self.folder.path)

    def match_current(self, progress: Callable[[int, int], None]) -> None:
        self.match = matcher.match_folder(self.model, self.folder.path, self.folder.scan.photos,
                                          self.app_config.match, progress,
                                          feature_source=self.cache.extract_many,)

    def thumbnail(self, photo):
        """Миниатюра фото папки (изображение PIL) из кэша. Ошибки чтения проходят к вызывающему."""
        return self.cache.thumbnail(os.path.join(self.folder.path, photo.filename))

    def photo_names(self) -> List[str]:
        return [p.filename for p in self.match.photos]

    # --- порядок строк ------------------------------------------------------------------

    def sort_by(self, column: str) -> None:
        """Щелчок по заголовку столбца: сортировка по нему, повторный щелчок — в обратную сторону."""
        self.sort_reverse = not self.sort_reverse if column == self.sort_column else False
        self.sort_column = column
        self.resort()

    def resort(self) -> None:
        """Пересчитывает порядок строк по выбранному столбцу.

        Между сортировками порядок зафиксирован: после правки имена меняются и у соседей
        (программа передаёт освободившееся имя другому фото), и если пересортировывать каждый
        раз, неразобранные строки прыгают выше уже сделанных. Тогда приходится возвращаться
        вверх, а работа должна идти сверху вниз.
        """
        photos = sorted(self.match.photos, key=SORT_KEYS.get(self.sort_column, SORT_KEYS[SORT_ORDER]),
                        reverse=self.sort_reverse)
        self._display_order = [photo.index for photo in photos]

    def display_photos(self) -> List:
        """Фото в порядке показа. Порядок задаётся сортировкой и держится до следующей."""
        if not self._display_order:
            return list(self.match.photos)
        return [self.match.photos[index] for index in self._display_order]

    def counts_text(self) -> str:
        total = len(self.match.photos)
        rename = sum(1 for p in self.match.photos if p.new_name)
        unknown = len(self.match.unidentified())
        check = sum(1 for p in self.match.photos if is_doubtful(p))
        text = "Фото: %d, будет переименовано: %d, проверить: %d" % (total, rename, check)
        if unknown:
            text += ", не опознано: %d" % unknown
        if self.match.strict:
            text += "   (состав совпадает с эталоном: имя предложено каждому фото)"
        if self.folder is not None and self.folder.scan.order_differs:
            text += "   (порядок съёмки не совпадает с порядком имён — используется порядок съёмки)"
        return text

    # --- правка ----------------------------------------------------------------------

    def _remember(self, indexes: List[int]) -> None:
        """Запоминает, что было до правки: по Ctrl+Z всё вернётся одним шагом."""
        step = [(index, self.match.manual.get(index, _ABSENT)) for index in indexes]
        if step:
            self._undo.append(step)

    def undo_edit(self) -> Optional[List[int]]:
        """Отменяет последнюю ручную правку: и выбор цифрой, и Enter, и кнопки, и список.

        Возвращает номера фото этой правки или None, если отменять нечего.
        """
        if not self._undo:
            return None
        step = self._undo.pop()
        for index, previous in step:
            if previous is _ABSENT:
                self.match.manual.pop(index, None)
            else:
                self.match.manual[index] = previous
        matcher.solve(self.match)
        return [index for index, _ in step]

    def confirm(self, indexes: List[int]) -> None:
        """Подтверждает предложенные имена как выбранные вручную."""
        self._remember(indexes)
        confirmed = 0
        for index in indexes:
            photo = self.match.photos[index]
            if photo.identified and photo.status != matcher.STATUS_MANUAL:
                matcher.set_category(self.match, index, photo.category.key)
                confirmed += 1
        if not confirmed:
            self._undo.pop()                  # подтверждать было нечего — шага не было

    def _settled_categories(self) -> Dict[str, int]:
        """Сколько фото каждой категории вы уже подтвердили: задали вручную или они уже названы."""
        settled: Dict[str, int] = {}
        for photo in self.match.photos:
            if photo.identified and photo.status in (matcher.STATUS_MANUAL, matcher.STATUS_ALREADY_NAMED):
                settled[photo.category.name] = settled.get(photo.category.name, 0) + 1
        return settled

    def exhausted_categories(self) -> set:
        """Категории, которых в папке, скорее всего, больше нет: вы уже подтвердили столько
        фото, сколько их в эталоне.

        Считаются только подтверждённые вами фото, а не догадки программы: иначе неверная
        догадка закрывала бы категорию, и выбрать её стало бы негде. Из списка категорий
        внизу такая категория не исчезает — её всегда можно выбрать вручную.
        """
        if self.match is None:
            return set()
        capacity = {c.name: len(c.photos) for c in self.model.categories}
        settled = self._settled_categories()
        return {name for name, count in settled.items() if count >= capacity.get(name, 0) > 0}

    def alternatives(self, photo) -> List:
        """Другие подходящие категории — без той, что уже в столбце «Новое имя», и без тех,
        что по составу эталона уже закрыты вашими же пометками.

        Предлагать то же самое, что программа и так выбрала, незачем: если имя верное, его
        просто оставляют. А категория, которой в папке ровно столько же, сколько вы уже
        отметили, третий раз встретиться не может — место в списке она занимает зря.
        """
        current = photo.category.name if photo.identified else None
        exhausted = self.exhausted_categories() - {current}
        return [c for c in photo.candidates if c.category != current and c.category not in exhausted]

    def apply_candidate(self, indexes: List[int], number: int) -> bool:
        """Каждому выбранному фото назначается его собственный вариант с этим номером.

        False — ни у одного фото такого варианта нет, ничего не изменилось.
        """
        self._remember(indexes)
        applied = 0
        for index in indexes:
            candidates = self.alternatives(self.match.photos[index])
            if len(candidates) >= number:
                matcher.set_category(self.match, index, category_key(candidates[number - 1].category))
                applied += 1
        if not applied:
            self._undo.pop()                  # ничего не изменилось — и отменять нечего
            return False
        return True

    def apply_category(self, indexes: List[int], category: Optional[str], sides_field: Optional[int] = None) -> None:
        """Назначает категорию выбранным фото; None — не переименовывать.

        sides_field — значение поля «Ракурсов» (None, если в поле мусор).
        """
        key = None
        if category is not None:
            key = self._category_key(category, len(indexes), sides_field)
        self._remember(indexes)
        for index in indexes:
            matcher.set_category(self.match, index, key)

    def _category_key(self, category: str, photos: int, sides_field: Optional[int]) -> str:
        """Ключ категории для раскладки. Заодно решает вопрос с ракурсами у своих категорий.

        Число берётся из поля «Ракурсов» — там видно, что получится, и это можно исправить
        до нажатия кнопки. Само поле подставляет то, что программа сделала бы без спроса:
        запомненное число ракурсов, а для новой категории — число выделенных фото, потому что
        несколько фото одной новой категории — это ракурсы одной детали, а не разные детали
        («Стойка многопозиционная фото 1» и «фото 2», а не «Стойка многопозиционная»
        и «Стойка многопозиционная 2» — замечание с рабочей машины).

        Число ракурсов запоминается в списке категорий. Состав эталона так не меняется:
        там ракурсы видно по самому образцу.
        """
        if category in self.reference_categories:
            return category_key(category)
        sides = max(1, sides_field if self._sides_are_set_by_hand(category, sides_field)
                    else max(self.names.sides_for(category), photos))
        key = matcher.add_category(self.match, category, sides)
        if matcher.set_sides(self.match, key, sides) or sides != self.names.sides_for(category):
            self.names.remember_sides(category, sides)
            self.names.save()
        return key

    # --- категории и ракурсы ---------------------------------------------------------

    def all_categories(self) -> List[str]:
        """Категории эталона плюс добавленные вручную (names.txt рядом с программой)."""
        extra = [name for name in self.names.names() if name not in self.reference_categories]
        return self.reference_categories + sorted(extra)

    def filter_categories(self, text: str) -> List[str]:
        return names_mod.filter_names(text, self.categories)

    def add_category(self, typed: str) -> str:
        """Добавляет категорию в список и возвращает её имя. Неверное имя — ValueError."""
        # число ракурсов здесь не решается: его показывает и даёт исправить поле
        # «Ракурсов» — оно же применяется, когда категорию назначают фото
        name = self.names.add(typed, self.names.sides_for(typed))
        self.names.save()
        if self.match is not None:
            matcher.add_category(self.match, name, self.names.sides_for(name))
        self.categories = self.all_categories()
        return name

    def remove_category(self, name: str) -> None:
        """Удаляет имя из списка. Категории самого эталона не трогаются: их задаёт образец."""
        self.names.remove(name)
        self.names.save()
        self.categories = self.all_categories()

    def is_reference_category(self, name: str) -> bool:
        return name in self.reference_categories

    def suggest_sides(self, category: str, selected: int, sides_field: Optional[int],
                      force: bool = False) -> Optional[int]:
        """Сколько ракурсов показать в поле для выбранной категории; None — поле не трогать.

        Подставляется то, что программа сделала бы сама: у категории эталона — число из
        образца (править его нечего, поле заблокировано), у своей — запомненное число или,
        если фото выделено больше, число выделенных фото. Заданное руками число не
        перебивается, пока не выбрана другая категория.
        """
        if not force and self._sides_are_set_by_hand(category, sides_field):
            return None
        value = self._auto_sides(category, selected)
        self._suggested_sides = (category, value)
        return value

    def _auto_sides(self, category: str, selected: int) -> int:
        """Сколько ракурсов программа взяла бы сама."""
        if category in self.reference_categories:
            known = (self.match.model.reference.categories.get(category_key(category))
                     if self.match is not None else None)
            return known.sides if known is not None else 1
        return max(self.names.sides_for(category), selected or 1)

    def _sides_are_set_by_hand(self, category: str, sides_field: Optional[int]) -> bool:
        """Человек сам задал число ракурсов этой категории — значит, оно важнее подсказки."""
        if self._suggested_sides is None or self._suggested_sides[0] != category or sides_field is None:
            return False
        return sides_field != self._suggested_sides[1]

    # --- переименование ------------------------------------------------------------------

    def build_plan(self) -> renamer.RenamePlan:
        return renamer.build_plan(self.match)

    def unresolved_count(self) -> int:
        """Сколько фото программа не разобрала: о них стоит спросить перед переименованием.

        Фото, помеченные «не переименовывать», и нечитаемые файлы — это осознанный выбор,
        спрашивать про них незачем.
        """
        return len([p for p in self.match.unidentified()
                    if p.status not in (matcher.STATUS_EXCLUDED, matcher.STATUS_UNREADABLE)])

    def apply_plan(self, plan: renamer.RenamePlan) -> str:
        """Переименовывает папку и возвращает итог одной строкой."""
        # обычного «переименовать N файлов?» больше нет: человек и так нажал кнопку
        # «Переименовать папку», а ошибку возвращает «Отменить переименование»
        renamer.apply_plan(plan, self.log)
        self.last_plan = plan
        report = build_report(self.match, len(self.folder.scan.excluded), self.app_config.problem_categories)
        text = format_report(report, plan, applied=True)
        self.log.write_text(text)
        # Отчёт по каждой папке не показывается: он копится и выдаётся один раз в конце,
        # иначе после каждой папки приходится закрывать окно (замечание с рабочей машины).
        self.reports.append(text)
        return ("Переименовано: %d, пропущено: %d, ошибок: %d"
                % (plan.count(oplog.RESULT_OK), plan.count(oplog.RESULT_SKIPPED), plan.count(oplog.RESULT_ERROR)))

    def undo_folder(self) -> Optional[str]:
        """Отменяет последнее переименование папки. Итог одной строкой или None, если отменять нечего."""
        if self.last_plan is None:
            return None
        done = [item for item in self.last_plan.items if item.result == oplog.RESULT_OK]
        folder = self.last_plan.folder
        plan = renamer.RenamePlan(folder,
                                  [renamer.RenameItem(i.new, i.old, i.category, None, "отмена") for i in
                                   reversed(done)])
        renamer.apply_plan(plan, self.log)
        self.last_plan = None
        if self.reports:
            self.reports.pop()
        return ("Переименование папки «%s» отменено: %d файл(ов)"
                % (os.path.basename(os.path.normpath(folder)), plan.count(oplog.RESULT_OK)))

    def skip_folder(self) -> None:
        if self.folder is not None:
            self.log.record(self.folder.path, oplog.ACTION_KEEP, oplog.RESULT_SKIPPED,
                            message="папка пропущена пользователем")

    def finish_text(self) -> str:
        # Окна с отчётом нет совсем: оно закрывало работу и его приходилось закрывать руками
        # (замечание с рабочей машины). Сами отчёты никуда не делись — они пишутся в файл
        # рядом с программой, и путь к нему виден внизу окна.
        return ("Группа обработана. Переименовано папок: %d.   Лог: %s   Отчёт: %s"
                % (len(self.reports), self.log.csv_path, self.log.txt_path))

    def close(self) -> None:
        self.log.close()
        self.cache.close()
        try:                       # выбранный размер фото должен сохраниться до следующего запуска
            cfg.save_config(self.app_config)
        except OSError:
            pass
