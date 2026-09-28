"""Общие настройки тестов.

Тесты не должны мешать работать за машиной. Каждый тест интерфейса создаёт настоящее окно
Tk, и без этих настроек прогон дважды портит жизнь:

1. Питон с Tk становится обычной программой macOS и выходит на передний план. Полноэкранный
   браузер при этом сворачивается на рабочий стол — и так десяток раз за прогон. Лечится
   политикой активации NSApplication: «Prohibited» — программа не выходит на передний план
   и не отбирает фокус. Поставить её можно только ПОСЛЕ создания первого окна Tk: до этого
   NSApplication ещё не создан, а если создать его самим, Tk падает (ему нужен свой класс).
2. Сами окна мелькают на экране. Прятать их (`withdraw`) мало: чтобы до окна доходили
   нажатия клавиш, ему нужен фокус, а фокус бывает только у показанного окна. Поэтому
   каждому окну сразу ставится полная прозрачность: окно живёт как обычно — размеры, фокус,
   события, — но на экране не видно ничего.

Настоящую программу это не касается: `run.py` открывает окно как обычно.
"""

import ctypes
import ctypes.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

try:
    import tkinter as tk
except ImportError:                     # без tkinter тесты интерфейса и так пропускаются
    tk = None

NS_APPLICATION_ACTIVATION_POLICY_PROHIBITED = 2


def _stay_in_the_background() -> bool:
    """Запрещает процессу выходить на передний план (только macOS)."""
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.objc_getClass.restype, objc.objc_getClass.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
    objc.sel_registerName.restype, objc.sel_registerName.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
    send = objc.objc_msgSend
    send.restype, send.argtypes = ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]
    app = send(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
    set_policy = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(
        ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)
    return bool(set_policy(app, objc.sel_registerName(b"setActivationPolicy:"),
                           NS_APPLICATION_ACTIVATION_POLICY_PROHIBITED))


def _make_windows_quiet() -> None:
    state = {"policy_set": sys.platform != "darwin"}

    def quiet(original, is_root):
        def __init__(self, *args, **kwargs):
            original(self, *args, **kwargs)
            if is_root and not state["policy_set"]:
                state["policy_set"] = True          # одного раза на процесс достаточно
                try:
                    _stay_in_the_background()
                except Exception:                   # не та система или нет библиотеки
                    pass
            try:
                self.attributes("-alpha", 0.0)
            except tk.TclError:                     # система без прозрачности — окно будет видно
                pass
        return __init__

    tk.Tk.__init__ = quiet(tk.Tk.__init__, True)
    tk.Toplevel.__init__ = quiet(tk.Toplevel.__init__, False)


if tk is not None:
    _make_windows_quiet()


def press(widget, sequence, **kwargs):
    """Нажатие клавиши в тесте.

    Событие с кириллическим keysym («Ctrl+ы») Tk собирает не везде: на Windows такой клавиши
    в раскладке раннера нет, и event_generate падает с «no keycode for keysym». Разбор
    русской раскладки там идёт по коду клавиши, и он проверяется без окна
    (test_theme.clipboard_action, shortcut_matches), поэтому оконную проверку пропускаем.
    """
    import pytest

    try:
        widget.event_generate(sequence, when="now", **kwargs)
    except tk.TclError as exc:
        pytest.skip("Tk здесь не собирает такое событие: %s" % exc)
