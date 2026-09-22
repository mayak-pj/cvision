"""Снимок имён файлов и восстановление по нему. Страховка на время испытаний.

Откат по журналу работы программы уже есть, но снимок независим от неё: он делается заранее,
хранится отдельно и возвращает имена, даже если журнал потерян или переименований было несколько.

Файлы узнаются по содержимому (размер + SHA-1 первых и последних 64 КБ), поэтому восстановление
работает после любого числа переименований. Файлы только переименовываются в своей папке:
ничего не удаляется и не перемещается.

    python tools/snapshot_names.py save <папка> --out logs/снимок.csv
    python tools/snapshot_names.py check <папка> --snapshot logs/снимок.csv
    python tools/snapshot_names.py restore <папка> --snapshot logs/снимок.csv [--dry-run]
"""

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from photo_renamer.cache import file_key  # noqa: E402
from photo_renamer.console import setup as console_setup  # noqa: E402

COLUMNS = ["Ключ", "Папка", "Имя"]


def scan(root: Path):
    """{ключ содержимого: (относительная папка, имя файла)} для всех файлов внутри root."""
    found = {}
    for folder, _, names in os.walk(str(root)):
        for name in names:
            if name.startswith("."):          # служебные файлы macOS не трогаем
                continue
            path = Path(folder) / name
            try:
                found[file_key(str(path))] = (os.path.relpath(folder, str(root)), name)
            except OSError:
                continue
    return found


def save(root: Path, out: Path) -> int:
    items = scan(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(COLUMNS)
        for key, (folder, name) in sorted(items.items(), key=lambda kv: (kv[1][0], kv[1][1])):
            writer.writerow([key, folder, name])
    print("Снимок сохранён: %s (файлов: %d)" % (out, len(items)))
    return 0


def load(snapshot: Path):
    with open(snapshot, encoding="utf-8-sig", newline="") as f:
        return {row["Ключ"]: (row["Папка"], row["Имя"]) for row in csv.DictReader(f, delimiter=";")}


def differences(root: Path, snapshot: Path):
    """[(ключ, папка, текущее имя, имя из снимка)] — только файлы, у которых имя изменилось."""
    saved = load(snapshot)
    current = scan(root)
    changed = []
    for key, (folder, name) in sorted(current.items(), key=lambda kv: (kv[1][0], kv[1][1])):
        if key in saved and saved[key][1] != name:
            changed.append((key, folder, name, saved[key][1]))
    return changed, current, saved


def check(root: Path, snapshot: Path) -> int:
    changed, current, saved = differences(root, snapshot)
    missing = set(saved) - set(current)
    added = set(current) - set(saved)
    print("В снимке файлов: %d, сейчас: %d" % (len(saved), len(current)))
    print("Переименовано: %d" % len(changed))
    for _, folder, now, was in changed[:20]:
        print("    %s: %s  ←  было %s" % (folder, now, was))
    if len(changed) > 20:
        print("    ... и ещё %d" % (len(changed) - 20))
    if missing:
        print("ВНИМАНИЕ: файлов из снимка не найдено: %d" % len(missing))
    if added:
        print("Новых файлов (в снимке их не было): %d" % len(added))
    return 0


def restore(root: Path, snapshot: Path, dry_run: bool) -> int:
    changed, current, _ = differences(root, snapshot)
    if not changed:
        print("Всё уже совпадает со снимком, восстанавливать нечего.")
        return 0
    done = failed = 0
    for key, folder, now, was in changed:
        directory = root / folder if folder != "." else root
        source, target = directory / now, directory / was
        if dry_run:
            print("    %s  →  %s" % (source, was))
            continue
        if target.exists():
            print("ПРОПУСК: имя занято — %s" % target)
            failed += 1
            continue
        try:
            os.rename(str(source), str(target))
            done += 1
        except OSError as exc:
            print("ОШИБКА: %s → %s: %s" % (source, was, exc))
            failed += 1
    if dry_run:
        print("--dry-run: ничего не изменено, файлов к восстановлению: %d" % len(changed))
    else:
        print("Восстановлено имён: %d, не удалось: %d" % (done, failed))
    return 1 if failed else 0


def main(argv=None) -> int:
    console_setup()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["save", "check", "restore"])
    parser.add_argument("root")
    parser.add_argument("--out", help="куда сохранить снимок (для save)")
    parser.add_argument("--snapshot", help="файл снимка (для check и restore)")
    parser.add_argument("--dry-run", action="store_true", help="показать, но не переименовывать")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print("ОШИБКА: папка не найдена: %s" % root)
        return 1
    if args.action == "save":
        if not args.out:
            print("ОШИБКА: укажите --out")
            return 1
        return save(root, Path(args.out))
    if not args.snapshot:
        print("ОШИБКА: укажите --snapshot")
        return 1
    snapshot = Path(args.snapshot)
    if not snapshot.is_file():
        print("ОШИБКА: снимок не найден: %s" % snapshot)
        return 1
    if args.action == "check":
        return check(root, snapshot)
    return restore(root, snapshot, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
