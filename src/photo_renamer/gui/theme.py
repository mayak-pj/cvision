"""Светлая тема и размещение окон.

На рабочих машинах Windows 7 стоит светлое оформление, поэтому программа всегда рисует себя
светлой, независимо от системной темы (на macOS с тёмным оформлением окна иначе получаются
тёмными и плохо читаются). Тему можно переключить настройкой theme: "light" или "system".
"""

import tkinter as tk
from tkinter import ttk

LIGHT = {
    "background": "#F4F5F7",     # фон окон и панелей
    "surface": "#FFFFFF",        # поля ввода, списки, таблицы, холст миниатюр
    "text": "#111827",
    "muted": "#555555",
    "border": "#C9CDD4",
    "select_bg": "#CCE4FF",      # выделение
    "select_fg": "#111827",
    "heading": "#E8EAED",        # заголовки таблиц
    "accent": "#3B82F6",
    "warning": "#B45309",
    "error": "#B91C1C",
    "ok": "#166534",
}


def colors() -> dict:
    return dict(LIGHT)


def apply(root: tk.Misc, mode: str = "light") -> None:
    """Применяет тему ко всему приложению. Вызывается один раз для главного окна:
    настройки наследуются всеми окнами, созданными позже."""
    if mode != "light":
        return
    palette = LIGHT
    style = ttk.Style(root)
    # clam есть на всех платформах и, в отличие от aqua/vista, поддаётся раскраске
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure(".", background=palette["background"], foreground=palette["text"],
                    fieldbackground=palette["surface"], bordercolor=palette["border"],
                    lightcolor=palette["background"], darkcolor=palette["border"])
    style.configure("TFrame", background=palette["background"])
    style.configure("TLabel", background=palette["background"], foreground=palette["text"])
    style.configure("TLabelframe", background=palette["background"])
    style.configure("TLabelframe.Label", background=palette["background"], foreground=palette["text"])
    style.configure("TButton", background="#E9ECEF", foreground=palette["text"], focuscolor=palette["accent"])
    style.map("TButton",
              background=[("pressed", "#D6DAE0"), ("active", "#DDE1E6"), ("disabled", palette["background"])],
              foreground=[("disabled", "#9AA0A6")])
    style.configure("TCheckbutton", background=palette["background"], foreground=palette["text"])
    style.map("TCheckbutton", background=[("active", palette["background"])])
    style.configure("TEntry", fieldbackground=palette["surface"], foreground=palette["text"],
                    insertcolor=palette["text"])
    style.configure("TSpinbox", fieldbackground=palette["surface"], foreground=palette["text"])
    style.configure("TMenubutton", background="#E9ECEF", foreground=palette["text"])
    style.configure("TProgressbar", background=palette["accent"], troughcolor=palette["heading"])
    style.configure("Treeview", background=palette["surface"], fieldbackground=palette["surface"],
                    foreground=palette["text"])
    style.map("Treeview", background=[("selected", palette["select_bg"])],
              foreground=[("selected", palette["select_fg"])])
    style.configure("Treeview.Heading", background=palette["heading"], foreground=palette["text"])
    style.configure("TScrollbar", background="#E9ECEF", troughcolor=palette["background"],
                    arrowcolor=palette["text"])

    # классические виджеты (список, холст, текст, меню) цвета из ttk не берут
    for pattern, value in (
        ("*background", palette["background"]),
        ("*foreground", palette["text"]),
        ("*Listbox.background", palette["surface"]),
        ("*Listbox.foreground", palette["text"]),
        ("*Listbox.selectBackground", palette["select_bg"]),
        ("*Listbox.selectForeground", palette["select_fg"]),
        ("*Text.background", palette["surface"]),
        ("*Text.foreground", palette["text"]),
        ("*Canvas.background", palette["surface"]),
        ("*Entry.background", palette["surface"]),
        ("*Entry.foreground", palette["text"]),
        ("*Menu.background", palette["surface"]),
        ("*Menu.foreground", palette["text"]),
        ("*Menu.activeBackground", palette["select_bg"]),
        ("*Menu.activeForeground", palette["select_fg"]),
    ):
        root.option_add(pattern, value)
    try:
        root.configure(background=palette["background"])
    except tk.TclError:
        pass


def fit_to_screen(window: tk.Misc, width_fraction: float = 0.92, height_fraction: float = 0.88,
                  top_margin: int = 60) -> None:
    """Размещает окно внутри экрана с отступом сверху.

    Отступ нужен, чтобы верхняя панель окна не оказалась под заголовком окна и строкой меню:
    если родительское окно развёрнуто во весь экран, дочернее открывается в той же области,
    и без отступа его кнопки перекрываются.
    """
    window.update_idletasks()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    width = max(int(screen_width * width_fraction), 900)
    height = max(int((screen_height - top_margin) * height_fraction), 560)
    width = min(width, screen_width - 20)
    height = min(height, screen_height - top_margin - 20)
    x = max((screen_width - width) // 2, 10)
    window.geometry("%dx%d+%d+%d" % (width, height, x, top_margin))
