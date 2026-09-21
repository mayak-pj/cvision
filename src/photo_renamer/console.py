"""Безопасный вывод в консоль Windows.

Русский текст не должен ронять программу из-за кодировки консоли: на русской Windows это
cp866, на английской — cp1252, где кириллицы нет вовсе. Кодировку не меняем (иначе в обычной
консоли получится каша), а только просим заменять непечатаемые символы вместо исключения.
"""

import sys


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # поток перенаправлен или не поддерживает настройку — не беда
