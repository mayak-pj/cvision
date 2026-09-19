"""Разбор имён эталонной папки в структуру «категория → экземпляр → сторона» и сборка новых имён.

Примеры имён (без расширения):
    «Процессор 1 фото 2»       → категория «Процессор», экземпляр 1, сторона 2
    «Материнская плата фото 1» → категория «Материнская плата», без номера, сторона 1
    «Кулер»                    → категория «Кулер», без номера, без стороны
    «Кулер 2»                  → категория «Кулер», экземпляр 2 (второй кулер при безномерной первой)

Категорией считается всё, что стоит до номера экземпляра или слова «фото».
"""

import os
import re
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import scanner

SIDE_WORD = "фото"

_NAME_RE = re.compile(
    r"^(?P<category>.+?)"
    r"(?:\s+(?P<instance>\d+))?"
    r"(?:\s+(?P<side_word>фото)\s*(?P<side>\d+))?$",
    re.IGNORECASE,
)
# Имена камер и телефонов (DSC_0042, IMG_8921, IMG_BB_043972, IMG_AA_000414, IMG_20240501_101010):
# признак того, что файл не переименован.
_CAMERA_NAME_RE = re.compile(r"^(dsc|dscn|dcim|img|pxl|p\d|photo|image|wp)([_\-\s]*[a-z]{1,4})?[_\-\s]*\d",
                             re.IGNORECASE)


@dataclass(frozen=True)
class ParsedName:
    category: str               # как написано в имени (пробелы нормализованы)
    instance: Optional[int]     # None — номер в имени не указан
    side: Optional[int]         # None — «фото N» в имени нет
    side_word: Optional[str] = None  # написание слова «фото» как в имени


def normalize_text(text: str) -> str:
    """NFC и схлопывание пробелов. Регистр сохраняется."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def is_camera_name(filename: str) -> bool:
    return bool(_CAMERA_NAME_RE.match(normalize_text(filename)))


def is_template_name(filename: str) -> bool:
    """Имя уже в формате «Категория [N] [фото K]» (записано канонически, не имя камеры)."""
    if is_camera_name(filename):
        return False
    stem = unicodedata.normalize("NFC", os.path.splitext(filename)[0])
    parsed = parse_name(filename)
    if not re.search(r"[^\W\d_]{2}", parsed.category):  # в категории должно быть слово
        return False
    parts = [parsed.category]
    if parsed.instance is not None:
        parts.append(str(parsed.instance))
    if parsed.side is not None:
        parts.append("%s %d" % (parsed.side_word, parsed.side))
    return " ".join(parts) == stem


def category_key(category: str) -> str:
    """Ключ группировки категорий: регистр и лишние пробелы не важны."""
    return normalize_text(category).casefold()


def parse_name(filename: str) -> ParsedName:
    """Разбирает имя файла. Разбор возможен всегда: в худшем случае всё имя — это категория."""
    stem = normalize_text(os.path.splitext(filename)[0])
    match = _NAME_RE.match(stem)
    if not match:  # пустое имя вроде «.jpg»
        return ParsedName(stem, None, None)
    instance = match.group("instance")
    side = match.group("side")
    return ParsedName(
        category=match.group("category"),
        instance=int(instance) if instance is not None else None,
        side=int(side) if side is not None else None,
        side_word=match.group("side_word"),
    )


@dataclass
class RefPhoto:
    filename: str
    path: str
    parsed: ParsedName

    @property
    def instance(self) -> int:
        return self.parsed.instance or 1

    @property
    def side(self) -> int:
        return self.parsed.side or 1


@dataclass
class Category:
    """Описание категории по эталону. Из него собираются новые имена."""

    name: str                 # написание из эталона, например «Оперативная память»
    numbered: bool            # первый экземпляр пишется с номером («Процессор 1»)
    has_side_suffix: bool     # в именах есть «фото N»
    sides: int                # число сторон одного экземпляра (фото подряд)
    instances: int            # число экземпляров в эталоне
    side_word: str = SIDE_WORD
    photos: List[RefPhoto] = field(default_factory=list)

    @property
    def key(self) -> str:
        return category_key(self.name)

    def format_name(self, instance: int, side: int, ext: str = ".jpg") -> str:
        """Новое имя файла для экземпляра и стороны (нумерация с 1)."""
        parts = [self.name]
        if self.numbered or instance > 1:
            parts.append(str(instance))
        if self.has_side_suffix:
            parts.append("%s %d" % (self.side_word, side))
        return " ".join(parts) + ext


@dataclass
class Reference:
    folder: str
    photos: List[RefPhoto]
    categories: "OrderedDict[str, Category]"  # ключ category_key → Category, порядок первого появления
    warnings: List[str]
    excluded: List[str] = field(default_factory=list)  # рентгенограммы в эталоне

    def category_of(self, photo: RefPhoto) -> Category:
        return self.categories[category_key(photo.parsed.category)]

    def expected_counts(self) -> Dict[str, int]:
        """Сколько фото каждой категории в эталоне: ключ — написание категории."""
        return OrderedDict((c.name, len(c.photos)) for c in self.categories.values())


def build_reference(folder: str, filenames: List[str], excluded: Optional[List[str]] = None) -> Reference:
    """Строит эталон по списку имён файлов (без обращения к диску)."""
    photos = [RefPhoto(name, os.path.join(folder, name), parse_name(name)) for name in filenames]
    groups: "OrderedDict[str, List[RefPhoto]]" = OrderedDict()
    for photo in photos:
        groups.setdefault(category_key(photo.parsed.category), []).append(photo)

    warnings: List[str] = []
    categories: "OrderedDict[str, Category]" = OrderedDict()
    for key, group in groups.items():
        categories[key] = _build_category(group, warnings)

    for photo in photos:
        if is_camera_name(photo.filename):
            warnings.append("«%s»: похоже, файл не переименован (имя камеры)" % photo.filename)
    if not photos:
        warnings.append("В эталонной папке нет ни одного фото .jpg")
    return Reference(folder, photos, categories, warnings, list(excluded or []))


def load_reference(folder: str) -> Reference:
    """Читает эталонную папку (только имена файлов, без подпапок)."""
    scan = scanner.list_photos(folder)
    return build_reference(folder, scan.photos, scan.excluded)


def _build_category(group: List[RefPhoto], warnings: List[str]) -> Category:
    spellings = Counter(p.parsed.category for p in group)
    name = spellings.most_common(1)[0][0]
    if len(spellings) > 1:
        warnings.append("Категория «%s» записана по-разному: %s" % (name, ", ".join("«%s»" % s for s in spellings)))

    with_side = [p for p in group if p.parsed.side is not None]
    has_side_suffix = bool(with_side)
    if with_side and len(with_side) != len(group):
        warnings.append("Категория «%s»: у части фото нет «%s N»" % (name, SIDE_WORD))
    side_word = Counter(p.parsed.side_word for p in with_side).most_common(1)[0][0] if with_side else SIDE_WORD

    first_instance = [p for p in group if p.instance == 1]
    numbered = any(p.parsed.instance == 1 for p in first_instance)
    if numbered and any(p.parsed.instance is None for p in first_instance):
        warnings.append("Категория «%s»: первый экземпляр записан и с номером, и без" % name)

    by_instance: Dict[int, List[int]] = {}
    for p in group:
        by_instance.setdefault(p.instance, []).append(p.side)

    instances = sorted(by_instance)
    if instances != list(range(1, len(instances) + 1)):
        warnings.append("Категория «%s»: номера экземпляров не идут подряд с 1: %s"
                        % (name, ", ".join(map(str, instances))))

    sides_per_instance = {}
    for inst in instances:
        sides = sorted(by_instance[inst])
        if len(set(sides)) != len(sides):
            warnings.append("Категория «%s», экземпляр %d: повторяются имена фото" % (name, inst))
        if sorted(set(sides)) != list(range(1, len(set(sides)) + 1)):
            warnings.append("Категория «%s», экземпляр %d: стороны не идут подряд с 1: %s"
                            % (name, inst, ", ".join(map(str, sides))))
        sides_per_instance[inst] = max(sides)

    side_counts = Counter(sides_per_instance.values())
    sides = side_counts.most_common(1)[0][0]
    if len(side_counts) > 1:
        warnings.append("Категория «%s»: у экземпляров разное число сторон (%s), принято %d"
                        % (name, ", ".join("экз. %d: %d" % kv for kv in sorted(sides_per_instance.items())), sides))

    return Category(
        name=name,
        numbered=numbered,
        has_side_suffix=has_side_suffix,
        sides=sides,
        instances=len(instances),
        side_word=side_word,
        photos=group,
    )


def describe(reference: Reference) -> str:
    """Текстовое описание эталона для консоли и лога."""
    lines = ["Эталон: %s" % reference.folder,
             "Фото: %d, категорий: %d" % (len(reference.photos), len(reference.categories))]
    for cat in reference.categories.values():
        example = cat.format_name(1, 1)
        lines.append("  %-40s экземпляров: %d, сторон: %d, фото: %d   (пример: %s)"
                     % (cat.name, cat.instances, cat.sides, len(cat.photos), example))
    if reference.excluded:
        lines.append("Пропущено рентгенограмм: %d" % len(reference.excluded))
    for warning in reference.warnings:
        lines.append("ВНИМАНИЕ: " + warning)
    return "\n".join(lines)


def main(argv=None):
    """Проверка эталона: python -m photo_renamer.reference <эталонная папка>."""
    import argparse

    parser = argparse.ArgumentParser(description="Разбор эталонной папки (только чтение)")
    parser.add_argument("folder")
    args = parser.parse_args(argv)
    print(describe(load_reference(args.folder)))


if __name__ == "__main__":
    main()
