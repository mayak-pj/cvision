"""Точка входа. Без аргументов — окно программы, с аргументами — консольный режим (run.py --help).

Работает и в обычном, и в embeddable Python (где sys.path задаётся ._pth-файлом).
"""

import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(APP_DIR, "src"))


def main():
    if len(sys.argv) > 1:
        from photo_renamer import cli

        cli.main()
    else:
        from photo_renamer.gui import app

        app.main()


if __name__ == "__main__":
    main()
