"""Работа с файловой системой: поиск папок с фото, фильтрация и сортировка файлов.

Модуль только читает каталоги. Никаких изменений на диске здесь не выполняется.
"""

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

PHOTO_EXTENSION = ".jpg"
EXCLUDE_SUBSTRING = "рентгенограмма"

SORT_BY_NAME = "name"
SORT_BY_EXIF = "exif"

_EXIF_IFD = 0x8769
_EXIF_DATETIME_ORIGINAL = 36867
_DIGITS_RE = re.compile(r"(\d+)")


@dataclass
class FolderScan:
    """Результат сканирования одной папки с фото."""

    path: str
    photos: List[str]  # имена файлов в порядке обработки
    excluded: List[str] = field(default_factory=list)  # файлы с «рентгенограмма» в имени
    missing_date: List[str] = field(default_factory=list)  # нет даты EXIF (только при SORT_BY_EXIF)

    @property
    def photo_paths(self) -> List[str]:
        return [os.path.join(self.path, name) for name in self.photos]


@dataclass
class ScanError:
    """Папка, которую не удалось прочитать (нет доступа, сетевой сбой и т.п.)."""

    path: str
    message: str


@dataclass
class ScanResult:
    folders: List[FolderScan]
    errors: List[ScanError]


def _normalize(name: str) -> str:
    # На macOS имена приходят в NFD, на Windows — в NFC. Сравниваем в одной форме.
    return unicodedata.normalize("NFC", name).casefold()


def is_excluded_name(filename: str) -> bool:
    """Файл с «рентгенограмма» в имени (регистр не важен) — полностью пропускается."""
    return EXCLUDE_SUBSTRING in _normalize(filename)


def is_jpg_name(filename: str) -> bool:
    """Только расширение .jpg в любом регистре. .jpeg и .png намеренно не подходят."""
    return os.path.splitext(filename)[1].lower() == PHOTO_EXTENSION


def natural_key(name: str):
    """Ключ «естественной» сортировки как в Проводнике: DSC_2 < DSC_2 (1) < DSC_10, без учёта регистра.

    Имя без расширения сравнивается отдельно, иначе «DSC_2 (1).jpg» оказалось бы раньше «DSC_2.jpg».
    """
    stem, ext = os.path.splitext(name)
    return _natural_parts(stem), _natural_parts(ext), name


def _natural_parts(text: str):
    parts = _DIGITS_RE.split(_normalize(text))
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts if p != "")


def io_path(path: str) -> str:
    """Путь для системных вызовов. На Windows длинные пути (>260 символов) требуют префикса \\\\?\\."""
    if os.name != "nt":
        return path
    return to_extended_path(path)


def to_extended_path(path: str) -> str:
    """Преобразует путь Windows в расширенную форму, если он близок к лимиту MAX_PATH."""
    if path.startswith("\\\\?\\") or len(path) < 240:
        return path
    path = os.path.abspath(path) if os.name == "nt" else path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _same_path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def read_exif_datetime(path: str) -> Optional[str]:
    """Дата съёмки DateTimeOriginal ('YYYY:MM:DD HH:MM:SS') или None.

    Pillow при открытии читает только заголовок файла, изображение не декодируется.
    """
    from PIL import Image

    try:
        with Image.open(io_path(path)) as img:
            exif = img.getexif()
            value = exif.get_ifd(_EXIF_IFD).get(_EXIF_DATETIME_ORIGINAL)
    except (OSError, ValueError, SyntaxError):
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    if not isinstance(value, str) or not value.strip(" \x00"):
        return None
    return value.strip(" \x00")


def list_photos(folder: str, sort: str = SORT_BY_NAME) -> FolderScan:
    """Список .jpg-фото папки (без подпапок) в порядке обработки.

    При sort=SORT_BY_EXIF фото без даты съёмки идут в конце, в порядке имён.
    Исключения ОС (нет доступа и т.п.) пробрасываются вызывающему.
    """
    photos, excluded, _ = _read_dir(folder)
    return _build_scan(folder, photos, excluded, sort)


def _read_dir(folder: str):
    """Одно чтение каталога (важно для сетевых дисков): фото, исключённые фото, подпапки."""
    photos, excluded, subdirs = [], [], []
    with os.scandir(io_path(folder)) as it:
        for entry in it:
            if _is_real_dir(entry):
                subdirs.append(entry.name)
            elif is_jpg_name(entry.name) and _is_regular_file(entry):
                (excluded if is_excluded_name(entry.name) else photos).append(entry.name)
    return photos, excluded, subdirs


def _build_scan(folder: str, photos: List[str], excluded: List[str], sort: str) -> FolderScan:
    if sort not in (SORT_BY_NAME, SORT_BY_EXIF):
        raise ValueError("Неизвестный способ сортировки: %r" % sort)
    photos = sorted(photos, key=natural_key)
    scan = FolderScan(path=folder, photos=photos, excluded=sorted(excluded, key=natural_key))

    if sort == SORT_BY_EXIF:
        dates = {name: read_exif_datetime(os.path.join(folder, name)) for name in photos}
        scan.missing_date = [name for name in photos if dates[name] is None]
        # sort стабилен: при равных датах сохраняется порядок имён
        photos.sort(key=lambda n: (dates[n] is None, dates[n] or ""))
    return scan


def _is_regular_file(entry: os.DirEntry) -> bool:
    try:
        return entry.is_file(follow_symlinks=False)
    except OSError:
        return False


def _is_real_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def find_photo_folders(
    root: str,
    exclude: Sequence[str] = (),
    sort: str = SORT_BY_NAME,
) -> ScanResult:
    """Рекурсивно находит все папки внутри root (включая сам root), где есть хотя бы одно фото.

    Папки из exclude (например, эталонная) пропускаются вместе со всеми подпапками.
    Символические ссылки и junction-точки не обходятся, чтобы не попасть в цикл.
    Недоступные папки не прерывают обход, а попадают в errors.
    Порядок папок детерминирован: обход в глубину, подпапки в естественном порядке имён.
    """
    excluded_keys = {_same_path_key(p) for p in exclude}
    folders: List[FolderScan] = []
    errors: List[ScanError] = []

    if not os.path.isdir(io_path(root)):
        return ScanResult(folders, [ScanError(root, "Папка не найдена или недоступна")])

    stack = [root]
    while stack:
        current = stack.pop()
        if _same_path_key(current) in excluded_keys:
            continue
        try:
            photos, excluded, subdirs = _read_dir(current)
        except OSError as exc:
            errors.append(ScanError(current, _describe_os_error(exc)))
            continue

        if photos:
            folders.append(_build_scan(current, photos, excluded, sort))
        subdirs.sort(key=natural_key, reverse=True)  # reverse: стек отдаёт первым первое имя
        stack.extend(os.path.join(current, name) for name in subdirs)

    return ScanResult(folders, errors)


def _describe_os_error(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return "Нет доступа к папке"
    if isinstance(exc, FileNotFoundError):
        return "Папка не найдена (возможно, удалена или сетевой диск отключён)"
    return "Ошибка чтения папки: %s" % (exc.strerror or exc)


def main(argv=None):
    """Проверка обхода без переименования: python -m photo_renamer.scanner <папка> [--exclude <эталон>]."""
    import argparse

    parser = argparse.ArgumentParser(description="Поиск папок с фото (только чтение)")
    parser.add_argument("root")
    parser.add_argument("--exclude", action="append", default=[], help="папка-эталон (можно несколько)")
    parser.add_argument("--sort", choices=[SORT_BY_NAME, SORT_BY_EXIF], default=SORT_BY_NAME)
    args = parser.parse_args(argv)

    result = find_photo_folders(args.root, exclude=args.exclude, sort=args.sort)
    total = 0
    for folder in result.folders:
        total += len(folder.photos)
        extra = []
        if folder.excluded:
            extra.append("рентгенограмм: %d" % len(folder.excluded))
        if folder.missing_date:
            extra.append("без даты EXIF: %d" % len(folder.missing_date))
        print("%4d фото  %s%s" % (len(folder.photos), folder.path, ("  (" + ", ".join(extra) + ")") if extra else ""))
    for error in result.errors:
        print("ОШИБКА  %s: %s" % (error.path, error.message))
    print("Итого: папок %d, фото %d, ошибок %d" % (len(result.folders), total, len(result.errors)))


if __name__ == "__main__":
    main()
