"""Оформление (светлое и тёмное), переключатель-лампочка и размещение окон.

На рабочих машинах Windows 7 стоит светлое оформление, поэтому по умолчанию программа рисует
себя светлой независимо от системной темы. Тёмную можно включить кнопкой-лампочкой в окне
или настройкой theme: "light" | "dark" | "system" (последнее — оформление как в системе).
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
    "button": "#E9ECEF",
    "button_active": "#DDE1E6",
    "button_pressed": "#D6DAE0",
    "disabled": "#9AA0A6",
}


DARK = {
    "background": "#23272E",
    "surface": "#1B1F24",
    "text": "#E6E8EB",
    "muted": "#A0A6AE",
    "border": "#3A4048",
    "select_bg": "#2F5D8C",
    "select_fg": "#FFFFFF",
    "heading": "#2C323A",
    "accent": "#5B9BF8",
    "warning": "#E0A458",
    "error": "#F87171",
    "ok": "#6EE7A8",
    "button": "#333A43",
    "button_active": "#3D4550",
    "button_pressed": "#2A3038",
    "disabled": "#6B7280",
}
PALETTES = {"light": LIGHT, "dark": DARK}
_current = "light"


def current_mode() -> str:
    return _current


def colors(mode: str = None) -> dict:
    return dict(PALETTES.get(mode or _current, LIGHT))


def apply(root: tk.Misc, mode: str = "light") -> None:
    """Применяет оформление ко всему приложению. Новые окна наследуют его автоматически,
    для уже открытых нужен apply_to_existing()."""
    global _current
    if mode not in PALETTES:
        return
    _current = mode
    palette = PALETTES[mode]
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
    style.configure("TButton", background=palette["button"], foreground=palette["text"],
                    focuscolor=palette["accent"])
    style.map("TButton",
              background=[("pressed", palette["button_pressed"]), ("active", palette["button_active"]),
                          ("disabled", palette["background"])],
              foreground=[("disabled", palette["disabled"])])
    style.configure("TCheckbutton", background=palette["background"], foreground=palette["text"])
    style.map("TCheckbutton", background=[("active", palette["background"])])
    style.configure("TEntry", fieldbackground=palette["surface"], foreground=palette["text"],
                    insertcolor=palette["text"])
    style.configure("TSpinbox", fieldbackground=palette["surface"], foreground=palette["text"])
    style.configure("TMenubutton", background=palette["button"], foreground=palette["text"])
    style.configure("TProgressbar", background=palette["accent"], troughcolor=palette["heading"])
    style.configure("Treeview", background=palette["surface"], fieldbackground=palette["surface"],
                    foreground=palette["text"])
    style.map("Treeview", background=[("selected", palette["select_bg"])],
              foreground=[("selected", palette["select_fg"])])
    style.configure("Treeview.Heading", background=palette["heading"], foreground=palette["text"])
    style.configure("TScrollbar", background=palette["button"], troughcolor=palette["background"],
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


def apply_to_existing(widget: tk.Misc, mode: str = None) -> None:
    """Перекрашивает уже созданные окна и обычные виджеты (ttk перекрашивается сам,
    а список, холст, текст и меню цвета темы не наследуют)."""
    palette = colors(mode)
    for child in widget.winfo_children():
        name = child.winfo_class()
        try:
            if name in ("Listbox", "Text", "Entry"):
                child.configure(background=palette["surface"], foreground=palette["text"],
                                selectbackground=palette["select_bg"], selectforeground=palette["select_fg"])
            elif name == "Canvas":
                child.configure(background=palette["surface"])
            elif name in ("Toplevel", "Tk", "Frame", "Labelframe", "Menu"):
                child.configure(background=palette["background"])
        except tk.TclError:
            pass
        apply_to_existing(child, mode)


class LampButton(tk.Canvas):
    """Переключатель оформления в виде лампочки: горит — светлая тема, погашена — тёмная."""

    def __init__(self, master, command=None, size: int = 30, **kwargs):
        super().__init__(master, width=size, height=size, highlightthickness=0, borderwidth=0,
                         cursor="hand2", **kwargs)
        self.command = command
        self.size = size
        self.on = True
        self.bind("<Button-1>", lambda event: self.toggle())
        self.redraw()

    def toggle(self) -> None:
        self.set(not self.on)
        if self.command:
            self.command(self.on)

    def set(self, on: bool) -> None:
        self.on = bool(on)
        self.redraw()

    def redraw(self) -> None:
        palette = colors()
        self.configure(background=palette["background"])
        self.delete("all")
        size = self.size
        glass = "#FFD866" if self.on else "#6B7280"
        outline = "#B08900" if self.on else "#4B5563"
        margin = size * 0.18
        # колба
        self.create_oval(margin, margin * 0.7, size - margin, size - margin * 1.6,
                         fill=glass, outline=outline, width=1)
        # цоколь
        base_left, base_right = size * 0.38, size * 0.62
        self.create_rectangle(base_left, size - margin * 1.7, base_right, size - margin * 0.9,
                              fill="#9CA3AF" if self.on else "#4B5563", outline=outline)
        self.create_line(base_left, size - margin * 0.7, base_right, size - margin * 0.7,
                         fill=outline, width=2)
        if self.on:  # лучи
            for x1, y1, x2, y2 in ((size * 0.08, size * 0.5, size * 0.2, size * 0.5),
                                   (size * 0.8, size * 0.5, size * 0.92, size * 0.5),
                                   (size * 0.5, size * 0.02, size * 0.5, size * 0.12)):
                self.create_line(x1, y1, x2, y2, fill="#E0A458", width=2)
        self.configure(state="normal")


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
