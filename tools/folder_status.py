"""Почему папка получила такой статус: числа, по которым программа решает.

Показывает то, на что смотрит правило «эталон / уже названа по шаблону»: сколько фото,
сколько из них названо по шаблону, какие вышли категории и чего не хватило. Нужен, когда
статус папки кажется неправильным: по числам сразу видно, какой порог не тот.

    python tools/folder_status.py <папка> [<папка> ...]
    python tools/folder_status.py <папка> --reference <эталон>   # ещё и сравнение с эталоном
    python tools/folder_status.py <папка> --names                # ещё и список неназванных фото

Ничего не меняет: только читает имена файлов.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from photo_renamer import grouping                                        # noqa: E402
from photo_renamer.reference import is_template_name, parse_name          # noqa: E402
from photo_renamer.scanner import list_photos                             # noqa: E402


def read(folder: str, out=print):
    try:
        return grouping.folder_info(list_photos(folder))
    except OSError as exc:
        out("%s — не прочитать: %s" % (folder, exc))
        return None


def report(folder: str, show_names: bool, reference=None, out=print) -> None:
    info = read(folder, out)
    if info is None:
        return
    scan = info.scan
    named = [name for name in scan.photos if is_template_name(name)]
    unnamed = [name for name in scan.photos if not is_template_name(name)]
    vocabulary = sorted({grouping._vocabulary_key(parse_name(name).category) for name in named} - {""})
    need_categories = max(grouping.REFERENCE_MIN_CATEGORIES, 0.05 * info.count)

    out("")
    out(folder)
    out("  фото: %d (пропущено рентгенограмм: %d)" % (info.count, len(scan.excluded)))
    out("  названо по шаблону: %d, не названо: %d, доля: %.0f%% (нужно %.0f%%)"
        % (len(named), len(unnamed), info.named_share * 100, grouping.REFERENCE_SHARE * 100))
    out("  разных категорий: %d (нужно %.0f) — %s"
        % (info.categories, need_categories, ", ".join(vocabulary) or "нет"))
    out("  годится в эталоны группы: %s" % ("да" if info.is_reference else "нет"))
    if not info.is_reference:
        if info.named_share < grouping.REFERENCE_SHARE:
            out("    не хватило доли названных")
        if info.categories < need_categories:
            out("    не хватило разных категорий")
    if show_names and unnamed:
        out("  не названы:")
        for name in unnamed:
            out("    %s" % name)
    if reference is None:
        out("  ВЕРДИКТ: готова папка или нет, решается в сравнении с эталоном группы —"
            " запустите с ключом --reference <эталон>")
        return
    same = os.path.normpath(folder) == os.path.normpath(reference.path)
    info.coverage = 1.0 if same else grouping.coverage_of(info, reference)
    if not same:
        missing = sorted(reference.vocabulary - info.vocabulary)
        out("  деталей эталона найдено: %.0f%% (нужно %.0f%%)"
            % (info.coverage * 100, grouping.FINISHED_COVERAGE * 100))
        if missing:
            out("    не нашлось: %s" % ", ".join(missing))
    out("  ВЕРДИКТ: %s" % (
        "уже названа — переименовываться не будет" if info.looks_finished else
        ("названа частично: %d из %d — будет переименована с предупреждением"
         % (info.named, info.count) if info.partly_named else
         "обычная папка — пойдёт в переименование")))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Почему у папки такой статус")
    parser.add_argument("folders", nargs="+", help="папки с фото")
    parser.add_argument("--names", action="store_true", help="показать неназванные фото")
    parser.add_argument("--reference", "-r", help="эталон группы: с ним сравнивается набор деталей")
    args = parser.parse_args(argv)
    reference = read(args.reference) if args.reference else None
    if reference is not None:
        reference.coverage = 1.0
        report(args.reference, args.names, reference)
    for folder in args.folders:
        report(folder, args.names, reference)


if __name__ == "__main__":
    main()
