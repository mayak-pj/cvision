"""Скачивание библиотек для Windows (выполняется на машине С ИНТЕРНЕТОМ).

Скачивает колёса (.whl) именно для Python 3.8 и 64-битной Windows, независимо от того,
на какой системе и версии Python запущен сам скрипт. Потом папку wheels переносят
вместе с дистрибутивом.

    python download_wheels.py                 # в папку wheels рядом со скриптом
    python download_wheels.py --dest D:\\wheels
"""

import argparse
import subprocess
import sys
from pathlib import Path

PYTHON_VERSION = "38"
PLATFORM = "win_amd64"


def command(requirements: Path, dest: Path):
    return [sys.executable, "-m", "pip", "download",
            "-r", str(requirements),
            "--only-binary=:all:",          # исходники не годятся: на рабочей машине нечем собирать
            "--python-version", PYTHON_VERSION,
            "--platform", PLATFORM,
            "--dest", str(dest)]


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requirements", default=str(here.parent / "requirements.txt"))
    parser.add_argument("--dest", default=str(here / "wheels"))
    parser.add_argument("--dry-run", action="store_true", help="показать команду и не скачивать")
    args = parser.parse_args(argv)

    requirements, dest = Path(args.requirements), Path(args.dest)
    if not requirements.is_file():
        print("ОШИБКА: не найден файл %s" % requirements)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    line = command(requirements, dest)
    print(" ".join(line))
    if args.dry_run:
        return 0
    result = subprocess.run(line)
    if result.returncode == 0:
        wheels = sorted(p.name for p in dest.glob("*.whl"))
        print("\nСкачано файлов: %d" % len(wheels))
        for name in wheels:
            print("    " + name)
        print("\nПроверьте, что у numpy, scipy и Pillow в имени есть cp38 и win_amd64.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
