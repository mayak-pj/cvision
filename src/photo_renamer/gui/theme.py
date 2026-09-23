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
    # заголовки столбцов — кнопки сортировки, и под мышью clam подсвечивает их системным
    # светлым цветом: на тёмном оформлении текст в них пропадал. Задаём цвета явно.
    style.map("Treeview.Heading",
              background=[("pressed", palette["select_bg"]), ("active", palette["button_active"])],
              foreground=[("pressed", palette["select_fg"]), ("active", palette["text"])],
              relief=[("pressed", "sunken"), ("active", "raised")])
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


class IconButton(tk.Canvas):
    """Маленькая кнопка с нарисованным значком: «плюс» — добавить, «урна» — удалить.

    Значок рисуется, а не берётся из шрифта: на Windows 7 символы вроде «✓» и «🗑»
    в tkinter отображаются не везде, а рисунок выглядит одинаково всюду.
    """

    def __init__(self, master, kind: str, command=None, size: int = 22, hint: str = "", **kwargs):
        super().__init__(master, width=size, height=size, highlightthickness=0, borderwidth=0,
                         cursor="hand2", **kwargs)
        self.kind = kind
        self.size = size
        self.command = command
        self.bind("<Button-1>", lambda event: self.command() if self.command else None)
        if hint:
            self.bind("<Enter>", lambda event: self._show_hint(hint))
            self.bind("<Leave>", lambda event: self._hide_hint())
        self._hint_window = None
        self.redraw()

    def _show_hint(self, text: str) -> None:
        self._hide_hint()
        palette = colors()
        self._hint_window = tk.Toplevel(self)
        self._hint_window.wm_overrideredirect(True)
        self._hint_window.wm_geometry("+%d+%d" % (self.winfo_rootx(), self.winfo_rooty() + self.size + 2))
        tk.Label(self._hint_window, text=text, background=palette["surface"], foreground=palette["text"],
                 borderwidth=1, relief="solid", padx=4).pack()

    def _hide_hint(self) -> None:
        if self._hint_window is not None:
            self._hint_window.destroy()
            self._hint_window = None

    def redraw(self) -> None:
        palette = colors()
        # фон как у полей ввода: apply_to_existing красит холсты именно так, и кнопка
        # не «выпадает» из оформления после переключения темы
        self.configure(background=palette["surface"])
        self.delete("all")
        size, color = self.size, palette["text"]
        if self.kind == "add":
            middle, arm = size / 2, size * 0.28
            self.create_line(middle - arm, middle, middle + arm, middle, fill=color, width=2)
            self.create_line(middle, middle - arm, middle, middle + arm, fill=color, width=2)
        else:                                   # урна
            left, right = size * 0.26, size * 0.74
            top, bottom = size * 0.34, size * 0.80
            self.create_rectangle(left, top, right, bottom, outline=color, width=2)
            self.create_line(size * 0.18, top, size * 0.82, top, fill=color, width=2)
            self.create_line(size * 0.40, size * 0.22, size * 0.60, size * 0.22, fill=color, width=2)
            for x in (size * 0.42, size * 0.58):
                self.create_line(x, top + size * 0.10, x, bottom - size * 0.08, fill=color, width=1)


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
