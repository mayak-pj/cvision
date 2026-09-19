"""Графический интерфейс (tkinter).

До импорта tkinter нужно, чтобы Tcl/Tk нашёл свои библиотеки. В портативной сборке для Windows
(embeddable Python) они лежат в папке tcl\\ рядом с python.exe, в standalone-сборках Python
для разработки — в lib/ базового Python. Если переменные TCL_LIBRARY/TK_LIBRARY не заданы,
ищем эти папки сами.
"""

import glob
import os
import sys


def ensure_tcl_paths() -> None:
    bases = {sys.base_prefix, sys.prefix, os.path.dirname(sys.executable)}
    for env, pattern in (("TCL_LIBRARY", "tcl8.*"), ("TK_LIBRARY", "tk8.*")):
        if os.environ.get(env):
            continue
        for base in bases:
            found = [d for sub in ("lib", "tcl") for d in glob.glob(os.path.join(base, sub, pattern))
                     if os.path.isfile(os.path.join(d, "init.tcl" if env == "TCL_LIBRARY" else "tk.tcl"))]
            if found:
                os.environ[env] = sorted(found)[-1]
                break


ensure_tcl_paths()
