"""Сборка портативного дистрибутива для Windows 7 (выполняется на машине с Windows).

Собирает папку, которую достаточно скопировать на рабочий компьютер:

    Переименование фото\\
        python\\                 embeddable Python 3.8.10 + библиотеки + файлы tkinter
        src\\photo_renamer\\      код программы
        run.py
        config.example.json, names.example.txt
        Переименование фото.bat  ярлык запуска
        Инструкция.txt

Почему так:
- на рабочем компьютере нет интернета и прав администратора, поэтому ничего не устанавливается:
  Python и библиотеки лежат внутри папки;
- в embeddable-дистрибутиве Python нет tkinter, его файлы берутся из обычной установки Python 3.8.10;
- колёса (.whl) — это zip-архивы, поэтому они просто распаковываются в Lib\\site-packages:
  pip на рабочей машине не нужен.

Запуск (см. dist_offline/README.md):
    python build_portable.py --embed python-3.8.10-embed-amd64.zip --wheels wheels
                             --tk-from "C:\\Program Files\\Python38" --out "Переименование фото"
    python build_portable.py ... --dry-run     # только проверить и показать план
"""

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

PROJECT_FILES = ["run.py", "config.example.json", "names.example.txt"]
PROJECT_DIRS = ["src"]
# Файлы tkinter, которых нет в embeddable-дистрибутиве.
# zlib1.dll есть не во всех сборках Python: официальная с python.org его содержит,
# а, например, сборка раннеров GitHub — нет, и tkinter там работает без него.
# Поэтому он копируется, только если найден.
TK_FILES = [
    ("DLLs/_tkinter.pyd", "_tkinter.pyd", True),
    ("DLLs/tcl86t.dll", "tcl86t.dll", True),
    ("DLLs/tk86t.dll", "tk86t.dll", True),
    ("DLLs/zlib1.dll", "zlib1.dll", False),
]
TK_DIRS = [("Lib/tkinter", "Lib/tkinter"), ("tcl", "tcl")]
LAUNCHER = """@echo off
rem Запуск программы. Консоль не нужна: pythonw.exe открывает окно без чёрного окна консоли.
set HERE=%~dp0
start "" "%HERE%python\\pythonw.exe" "%HERE%run.py" %*
"""
CONSOLE_LAUNCHER = """@echo off
rem То же самое, но с окном консоли: видно сообщения об ошибках.
set HERE=%~dp0
"%HERE%python\\python.exe" "%HERE%run.py" %*
pause
"""
# Содержимое python38._pth: пути поиска модулей для embeddable Python.
# «import site» обязателен, иначе не подхватятся пакеты из Lib\site-packages;
# «Lib» нужен сам по себе — там лежит tkinter (без этой строки окно не запускается);
# «..\src» — код программы. Файл читается как ASCII, поэтому без комментариев по-русски.
PTH_CONTENT = """python38.zip
.
..
..\\src
Lib
Lib\\site-packages
import site
"""


def _safe_console() -> None:
    """Русский текст не должен ронять скрипт в консоли с другой кодировкой (cp1252 и т.п.)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


class BuildError(Exception):
    pass


def check_inputs(embed: Path, wheels: Path, tk_from: Path) -> None:
    if not embed.is_file() or embed.suffix.lower() != ".zip":
        raise BuildError("Не найден архив embeddable Python: %s" % embed)
    if not wheels.is_dir() or not list(wheels.glob("*.whl")):
        raise BuildError("В папке %s нет ни одного файла .whl" % wheels)
    if tk_from is not None:
        missing = [name for name, _, required in TK_FILES if required and not (tk_from / name).is_file()]
        missing += [name for name, _ in TK_DIRS if not (tk_from / name).is_dir()]
        if missing:
            raise BuildError("В установке Python (%s) не найдено: %s" % (tk_from, ", ".join(missing)))


def wheel_list(wheels: Path):
    return sorted(wheels.glob("*.whl"))


def wrong_platform_wheels(wheels: Path):
    """Колёса не для Python 3.8 / 64-битной Windows: на рабочей машине не заработают."""
    bad = []
    for wheel in wheel_list(wheels):
        name = wheel.name.lower()
        pure = "-py3-none-any.whl" in name or "-py2.py3-none-any.whl" in name
        if pure:
            continue
        if "cp38" not in name or "win_amd64" not in name:
            bad.append(wheel.name)
    return bad


def plan(embed: Path, wheels: Path, tk_from: Path, out: Path):
    """Человекочитаемый план сборки (используется и для --dry-run)."""
    lines = ["Куда собираем: %s" % out,
             "Python: %s" % embed.name,
             "Библиотеки (%d):" % len(wheel_list(wheels))]
    lines += ["    " + wheel.name for wheel in wheel_list(wheels)]
    lines.append("Файлы tkinter: %s" % ("из " + str(tk_from) if tk_from else "НЕ КОПИРУЮТСЯ (окно не запустится)"))
    if tk_from is not None:
        optional = [name for name, _, required in TK_FILES if not required and not (tk_from / name).is_file()]
        if optional:
            lines.append("    необязательных файлов нет (обычно не мешает): " + ", ".join(optional))
    lines.append("Код программы: " + ", ".join(PROJECT_DIRS + PROJECT_FILES))
    bad = wrong_platform_wheels(wheels)
    if bad:
        lines.append("ВНИМАНИЕ: не для Python 3.8 / win_amd64: " + ", ".join(bad))
    return "\n".join(lines)


def extract_wheels(wheels: Path, site_packages: Path) -> int:
    """Колесо — это zip: распаковываем прямо в site-packages, pip на рабочей машине не нужен."""
    site_packages.mkdir(parents=True, exist_ok=True)
    count = 0
    for wheel in wheel_list(wheels):
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site_packages)
        count += 1
    return count


def copy_tkinter(tk_from: Path, python_dir: Path) -> None:
    for source, target, required in TK_FILES:
        path = tk_from / source
        if path.is_file():
            shutil.copy2(path, python_dir / target)
        elif required:
            raise BuildError("Не найден файл tkinter: %s" % path)
    for source, target in TK_DIRS:
        destination = python_dir / target
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(tk_from / source, destination)


def verify_tkinter(python_dir: Path) -> None:
    """После копирования проверяем, что окно будет чем запускать."""
    missing = [target for _, target, required in TK_FILES
               if required and not (python_dir / target).is_file()]
    missing += [target for _, target in TK_DIRS if not (python_dir / target).is_dir()]
    if missing:
        raise BuildError("В собранном Python не хватает файлов tkinter: %s" % ", ".join(missing))


def copy_project(project: Path, out: Path) -> None:
    for name in PROJECT_DIRS:
        destination = out / name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(project / name, destination,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in PROJECT_FILES:
        shutil.copy2(project / name, out / name)
    for name in ("logs", "cache"):
        (out / name).mkdir(exist_ok=True)


def write_launchers(out: Path) -> None:
    (out / "Переименование фото.bat").write_text(LAUNCHER, encoding="cp866")
    (out / "Запуск с консолью (для ошибок).bat").write_text(CONSOLE_LAUNCHER, encoding="cp866")


def build(embed: Path, wheels: Path, tk_from: Path, out: Path, project: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    python_dir = out / "python"
    python_dir.mkdir(parents=True)
    with zipfile.ZipFile(embed) as archive:
        archive.extractall(python_dir)

    pth = list(python_dir.glob("python*._pth"))
    if not pth:
        raise BuildError("В архиве Python не найден файл python*._pth")
    pth[0].write_text(PTH_CONTENT, encoding="ascii")

    extracted = extract_wheels(wheels, python_dir / "Lib" / "site-packages")
    if tk_from is not None:
        copy_tkinter(tk_from, python_dir)
    if tk_from is not None:
        verify_tkinter(python_dir)
    copy_project(project, out)
    write_launchers(out)
    shutil.copy2(project / "dist_offline" / "Инструкция.txt", out / "Инструкция.txt")
    shutil.copy2(project / "dist_offline" / "check_install.py", out / "check_install.py")
    print("Готово. Распаковано библиотек: %d. Папка: %s" % (extracted, out))
    print("Проверьте сборку на этой же машине:")
    print('    "%s\\python\\python.exe" "%s\\check_install.py"' % (out, out))


def main(argv=None) -> int:
    _safe_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--embed", required=True, help="архив python-3.8.10-embed-amd64.zip")
    parser.add_argument("--wheels", required=True, help="папка с .whl (см. download_wheels.py)")
    parser.add_argument("--tk-from", help=r'обычная установка Python 3.8.10, например C:\Program Files\Python38')
    parser.add_argument("--out", required=True, help="папка дистрибутива")
    parser.add_argument("--project", default=str(Path(__file__).resolve().parent.parent),
                        help="папка проекта (по умолчанию — этот репозиторий)")
    parser.add_argument("--dry-run", action="store_true", help="только проверить и показать план")
    args = parser.parse_args(argv)

    embed, wheels = Path(args.embed), Path(args.wheels)
    tk_from = Path(args.tk_from) if args.tk_from else None
    out, project = Path(args.out), Path(args.project)
    try:
        check_inputs(embed, wheels, tk_from)
        print(plan(embed, wheels, tk_from, out))
        if args.dry_run:
            print("\n--dry-run: ничего не записано.")
            return 0
        if os.name != "nt":
            print("\nВНИМАНИЕ: сборка рассчитана на Windows. Здесь она соберётся, "
                  "но проверить запуск можно только на Windows.")
        build(embed, wheels, tk_from, out, project)
    except BuildError as exc:
        print("ОШИБКА: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
