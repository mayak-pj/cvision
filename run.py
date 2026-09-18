"""Точка входа. Работает и в обычном, и в embeddable Python (где sys.path задаётся ._pth-файлом)."""

import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(APP_DIR, "src"))


def main():
    from photo_renamer import cli

    cli.main()


if __name__ == "__main__":
    main()
