"""Графический интерфейс (tkinter).

До импорта tkinter нужно, чтобы Tcl/Tk нашёл свои библиотеки.

В портативной сборке раскладка повторяет обычную установку Python (DLLs\tcl86t.dll и tcl\tcl8.6
рядом), и Tcl находит файлы сам — вмешиваться не нужно. В standalone-сборках Python для разработки
они лежат в lib/ базового Python, и тогда путь приходится задавать переменными окружения.

Путь в переменной окружения не должен содержать кириллицу: Tcl читает переменные в системной
однобайтовой кодировке, и на нерусской Windows такой путь портится. Поэтому на Windows берётся
короткое имя пути (8.3), а если получить его не удалось — переменная не задаётся вовсе,
чтобы не мешать самостоятельному поиску Tcl.
"""

import glob
import os
import sys


def _short_path(path: str) -> str:
    """Короткое имя пути Windows (только латиница и цифры). На других системах — как есть."""
    if os.name != "nt" or path.isascii():
        return path
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(1024)
        length = ctypes.windll.kernel32.GetShortPathNameW(path, buffer, len(buffer))
        short = buffer.value if length else ""
    except Exception:
        short = ""
    return short if short and short.isascii() else ""


def _found_by_tcl_itself() -> bool:
    """Раскладка как в обычной установке Python: Tcl найдёт свои файлы без подсказок."""
    for base in (os.path.dirname(sys.executable), sys.base_prefix):
        if glob.glob(os.path.join(base, "DLLs", "tcl86*.dll")) and glob.glob(os.path.join(base, "tcl", "tcl8.*")):
            return True
    return False


def ensure_tcl_paths() -> None:
    if _found_by_tcl_itself():
        return
    bases = {sys.base_prefix, sys.prefix, os.path.dirname(sys.executable)}
    for env, pattern, marker in (("TCL_LIBRARY", "tcl8.*", "init.tcl"), ("TK_LIBRARY", "tk8.*", "tk.tcl")):
        if os.environ.get(env):
            continue
        for base in bases:
            found = [d for sub in ("lib", "tcl") for d in glob.glob(os.path.join(base, sub, pattern))
                     if os.path.isfile(os.path.join(d, marker))]
            if found:
                usable = _short_path(sorted(found)[-1])
                if usable:
                    os.environ[env] = usable
                break


ensure_tcl_paths()
