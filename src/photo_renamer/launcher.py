"""Запуск окна программы (PyQt5). Если PyQt5 не загружается, об этом говорит сообщение Windows:
программа запускается через pythonw.exe, и консоли, где был бы виден текст ошибки, нет."""

import os
import sys

from . import config as cfg

QT_MISSING = ("Окно программы не запускается: не загружается PyQt5 (%s).\n\n"
              "Запустите «Проверка сборки.bat» и сообщите, что написано в строке «Окно (PyQt5)».")


def load_qt():
    """Модуль окна на Qt или None, если PyQt5 здесь не загружается."""
    try:
        from .gui_qt import app
    except ImportError as exc:
        tell(QT_MISSING % exc)
        return None
    return app


def tell(text: str) -> None:
    """Сообщение без окна программы: в консоль и, на Windows, системным окном."""
    if sys.stderr is not None:
        print(text, file=sys.stderr)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "CVision", 0x10)
        except Exception:        # noqa — сообщить больше нечем
            pass


def main() -> None:
    config, warnings = cfg.load_config()
    config.ensure_names_file()
    qt = load_qt()
    if qt is not None:
        qt.main(config, warnings)
