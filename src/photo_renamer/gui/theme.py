"""Оформление (светлое и тёмное), переключатель-лампочка и размещение окон.

На рабочих машинах Windows 7 стоит светлое оформление, поэтому по умолчанию программа рисует
себя светлой независимо от системной темы. Тёмную можно включить кнопкой-лампочкой в окне
или настройкой theme: "light" | "dark" | "system" (последнее — оформление как в системе).
"""

import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

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
    # В clam подпись выпадающей кнопки прижата к левому краю слоем стиля, и она выбивалась
    # из ряда обычных кнопок с подписью по центру. «expand» отдаёт подписи всё место кнопки,
    # а пустой «sticky» ставит её посередине. Стрелку рисует сам стиль, писать её в тексте
    # не нужно
    style.layout("Centered.TMenubutton", [
        ("Menubutton.border", {"sticky": "nswe", "children": [
            ("Menubutton.focus", {"sticky": "nswe", "children": [
                ("Menubutton.indicator", {"side": "right", "sticky": ""}),
                ("Menubutton.padding", {"sticky": "we", "children": [
                    ("Menubutton.label", {"side": "left", "expand": True, "sticky": ""})]})]})]})])
    style.configure("Centered.TMenubutton", background=palette["button"], foreground=palette["text"])
    style.map("Centered.TMenubutton",
              background=[("pressed", palette["button_pressed"]), ("active", palette["button_active"]),
                          ("disabled", palette["background"])],
              foreground=[("disabled", palette["disabled"])])
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


# Сочетания с Ctrl (на macOS — с Command) при любой раскладке клавиатуры.
#
# Tk узнаёт «<Control-v>» только в латинской раскладке: в русской приходит «Ctrl+м», и
# привязка не срабатывает. Поэтому событие разбирается по трём признакам:
#   * сама буква;
#   * её двойник по русской раскладке — «м» на клавише V, «ы» на S, «я» на Z;
#   * код клавиши — он не зависит от раскладки, но только на Windows: на macOS Tk собирает
#     код из самого символа (для «s» это 0xFF000073), и от раскладки он там как раз зависит.
_SHORTCUT_KEYCODES = {"a": 65, "c": 67, "s": 83, "v": 86, "x": 88, "z": 90}
_LAYOUT_TWINS = {
    "a": ("ф", "cyrillic_ef"), "c": ("с", "cyrillic_es"), "s": ("ы", "cyrillic_yeru"),
    "v": ("м", "cyrillic_em"), "x": ("ч", "cyrillic_che"), "z": ("я", "cyrillic_ya"),
}
# Что делают эти сочетания в полях ввода
_CLIPBOARD_ACTIONS = {"c": "<<Copy>>", "v": "<<Paste>>", "x": "<<Cut>>", "z": "<<Undo>>",
                      "a": "select-all"}
_CLIPBOARD_CLASSES = ("Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox")
CTRL_MASK, COMMAND_MASK, ALT_MASK = 0x0004, 0x0008, 0x20000
# В Tk «Command» — это то же самое, что «Mod1», и на macOS это клавиша ⌘. На других системах
# Mod1 означает что угодно (на Windows его выставляет NumLock), поэтому там сочетания
# с Command не привязываются вовсе: иначе при включённом NumLock обычная буква сработала бы
# как сочетание.
COMMAND_IS_A_SHORTCUT = sys.platform == "darwin"


def shortcut_pressed(state: int) -> bool:
    """Нажат Ctrl (0x0004) или, на macOS, Command (0x0008), и не нажат Alt.

    Alt (0x20000) отсекается: AltGr на некоторых раскладках выглядит как Ctrl+Alt.
    """
    if state & ALT_MASK:
        return False
    if state & CTRL_MASK:
        return True
    return COMMAND_IS_A_SHORTCUT and bool(state & COMMAND_MASK)


def clipboard_action(keycode: int = 0, state: int = 0, keysym: str = "", char: str = "") -> str:
    """Что делать по нажатию: имя виртуального события, «select-all» или пустая строка.

    Сначала клавиша ищется по букве (и по её двойнику в русской раскладке), и только потом
    по коду: если признаки разошлись, буква вернее — код нужен там, где буквы нет.
    """
    if not shortcut_pressed(state):
        return ""
    for by_code in (False, True):
        for letter, action in _CLIPBOARD_ACTIONS.items():
            if shortcut_matches(letter, keysym, char, keycode if by_code else 0):
                return action
    return ""


def shortcut_label(widget: tk.Misc, letter: str) -> str:
    """Как назвать сочетание в надписи на кнопке: «⌘S» на macOS, «Ctrl+S» в остальных системах."""
    try:
        aqua = widget.tk.call("tk", "windowingsystem") == "aqua"
    except tk.TclError:
        aqua = False
    return ("\u2318%s" if aqua else "Ctrl+%s") % letter.upper()


def shortcut_matches(letter: str, keysym: str, char: str = "", keycode: int = 0) -> bool:
    """Та ли это клавиша, с поправкой на раскладку.

    Совпасть может сама буква, её двойник по русской раскладке («ы» на клавише S) или код
    клавиши. Код помогает на Windows, где он от раскладки не зависит; на macOS Tk собирает
    код из самого символа, поэтому там работают первые два признака.
    """
    twin_char, twin_keysym = _LAYOUT_TWINS[letter]
    keysym, char = (keysym or "").lower(), (char or "").lower()
    return (keysym in (letter, twin_char, twin_keysym) or char in (letter, twin_char)
            or keycode == _SHORTCUT_KEYCODES[letter])


def bind_shortcut(widget: tk.Misc, letter: str, command, in_fields: bool = False) -> None:
    """Сочетание Ctrl+<буква> (на macOS — и Command+<буква>) при любой раскладке клавиатуры.

    Простой привязки мало сразу по двум причинам:

    * «<Control-s>» Tk узнаёт только в латинской раскладке — в русской приходит «Ctrl+ы»;
    * на macOS то же сочетание нажимают Command, а не Ctrl.

    Поэтому рядом с привязками по букве стоит разбор события: буква, её двойник по русской
    раскладке или код клавиши (последнее работает на Windows, где код от раскладки не зависит).
    Дважды одно нажатие не сработает: из подходящих привязок одного виджета Tk выбирает самую
    точную. in_fields=True — перехватывать сочетание и в полях ввода; по умолчанию там
    работают свои Ctrl+C/V/X/Z (см. install_clipboard_keys).
    """
    def run(_event=None):
        command()
        return "break"

    def by_event(event):
        if not shortcut_pressed(getattr(event, "state", 0)):
            return None
        if not shortcut_matches(letter, getattr(event, "keysym", ""), getattr(event, "char", ""),
                                getattr(event, "keycode", 0)):
            return None
        if not in_fields and event.widget.winfo_class() in _CLIPBOARD_CLASSES:
            return None
        return run()

    for modifier in _shortcut_modifiers():
        for key in (letter, letter.upper()):
            widget.bind("<%s-%s>" % (modifier, key), run)
        # add="+": разбор по коду и двойнику ставится для каждой буквы отдельно, и привязка
        # следующего сочетания не должна затирать предыдущую
        widget.bind("<%s-KeyPress>" % modifier, by_event, add="+")


def _clipboard_key(event):
    action = clipboard_action(getattr(event, "keycode", 0), getattr(event, "state", 0),
                              getattr(event, "keysym", ""), getattr(event, "char", ""))
    if not action:
        return None
    widget = event.widget
    if action == "select-all":
        try:
            widget.selection_range(0, "end")
            widget.icursor("end")
        except (tk.TclError, AttributeError):   # в многострочном поле выделения «от и до» нет
            return None
        return "break"
    widget.event_generate(action)
    return "break"


def install_clipboard_keys(root: tk.Misc) -> None:
    """Включает Ctrl+C/V/X/A/Z (на macOS — ⌘C/V/X/A/Z) в полях ввода при любой раскладке."""
    for name in _CLIPBOARD_CLASSES:
        for modifier in _shortcut_modifiers():
            root.bind_class(name, "<%s-KeyPress>" % modifier, _clipboard_key)


def _shortcut_modifiers():
    return ("Control", "Command") if COMMAND_IS_A_SHORTCUT else ("Control",)


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
        if hasattr(child, "redraw"):        # значок нарисован цветами прежней темы
            child.redraw()
        apply_to_existing(child, mode)


def tree_columns(tree: ttk.Treeview):
    """Все столбцы таблицы, включая первый («#0»)."""
    return ("#0",) + tuple(tree.cget("columns"))


def columns_width(tree: ttk.Treeview) -> int:
    return sum(tree.column(column, "width") for column in tree_columns(tree))


def fit_columns(tree: ttk.Treeview, samples: dict, extra: int = 24) -> None:
    """Ширина столбца — по самой длинной надписи: заголовку или образцу значения.

    Задавать ширины числом нельзя: на рабочей машине шрифт крупнее, и столбец то обрезает
    текст, то занимает место зря. «Статус» с образцом «100% переименована» получается вдвое
    у́же прежних 230 точек, и «Сходство» помещается в окно сразу при открытии.
    """
    text_font = tkfont.nametofont("TkDefaultFont")
    heading_font = tkfont.nametofont("TkHeadingFont")
    for column, sample in samples.items():
        needed = max(heading_font.measure(tree.heading(column, "text")),
                     text_font.measure(sample)) + extra
        tree.column(column, width=needed, minwidth=min(needed, tree.column(column, "minwidth")))


def fit_column_headers(tree: ttk.Treeview, extra: int = 28) -> None:
    """Столбец не бывает уже своего заголовка.

    На рабочей машине шрифт крупнее, и подписи обрезались («Сходство с э…»). extra — запас
    на отступы и значок сортировки.
    """
    heading_font = tkfont.nametofont("TkHeadingFont")
    for column in tree_columns(tree):
        needed = heading_font.measure(tree.heading(column, "text")) + extra
        tree.column(column,
                    width=max(tree.column(column, "width"), needed),
                    minwidth=max(tree.column(column, "minwidth"), needed))


def fixed_columns(tree: ttk.Treeview, hscroll: ttk.Scrollbar, on_change=None):
    """Столбцы меняют ширину поодиночке, а последний добирает свободное место.

    Tk, меняя ширину столбца, забирает разницу у растяжимого: потянешь границу одного —
    ужмётся другой, которого никто не трогал (замечание с рабочей машины «столбцы
    неестественно уезжают»). Поэтому не растягивается ни один.

    Но тогда справа остаётся ничья пустая полоса, а пустого места в таблице быть не должно.
    Её добирает последний столбец: у него короткие значения, и его ширина ни на что не влияет,
    зато пропадает и полоса, и вопрос «кто съел место». Если последний столбец потянули руками,
    его ширину запоминаем и меньше неё не делаем. Не помещаются столбцы — снизу появляется
    полоса прокрутки, как в Проводнике.
    """
    columns = tree_columns(tree)
    last = columns[-1]
    for column in columns:
        tree.column(column, stretch=False)
    tree.configure(xscrollcommand=hscroll.set)
    hscroll.configure(command=tree.xview)
    state = {"own": tree.column(last, "width"), "set": None}

    def update(_event=None):
        width = tree.column(last, "width")
        if state["set"] is not None and width != state["set"]:
            state["own"] = width                   # последний столбец потянули руками
        free = tree.winfo_width() - (columns_width(tree) - width)
        state["set"] = max(state["own"], tree.column(last, "minwidth"), free)
        if state["set"] != width:
            tree.column(last, width=state["set"])
        if columns_width(tree) > tree.winfo_width():
            hscroll.grid()
        else:
            hscroll.grid_remove()
        if on_change:
            on_change()

    tree.bind("<Configure>", lambda e: update(), add="+")
    # ширины становятся новыми, только когда границу отпустили
    tree.bind("<ButtonRelease-1>", lambda e: tree.after_idle(update), add="+")
    return update


class IconButton(ttk.Button):
    """Маленькая кнопка со значком: «плюс» — добавить, «урна» — удалить.

    Это обычная кнопка оформления — такая же, как «+»/«−» у размера миниатюр: с рамкой,
    подсветкой под мышью и нажатием (замечание с рабочей машины: голый рисунок выбивался
    из ряда кнопок). А сам значок рисуется, а не берётся из шрифта: на Windows 7 знаки
    вроде «✓» и «🗑» в tkinter отображаются не везде.
    """

    PADDING = 2

    def __init__(self, master, kind: str, command=None, size: int = None, hint: str = "", **kwargs):
        super().__init__(master, command=command, padding=self.PADDING, **kwargs)
        self.kind = kind
        # размер значка не задаётся числом, а измеряется: кнопка должна быть той же высоты,
        # что и обычная маленькая кнопка рядом («+»/«−» у размера миниатюр). На рабочей
        # машине шрифт крупнее, и любое число сделало бы ряд кнопок рваным
        self.size = size if size is not None else self._fit_size(master)
        self._image = None
        if hint:
            self.bind("<Enter>", lambda event: self._show_hint(hint))
            self.bind("<Leave>", lambda event: self._hide_hint())
        self._hint_window = None
        self.redraw()

    @classmethod
    def _fit_size(cls, master) -> int:
        """Какой высоты должен быть значок, чтобы кнопка сравнялась с обычной.

        Меряем две пробные кнопки: обычную с текстом и такую же с картинкой в одну точку.
        Разница высот — это рамка и отступы, остальное место и занимает значок.
        """
        try:
            text_button = ttk.Button(master, text="+", width=3)
            dot = tk.PhotoImage(master=master, width=1, height=1)
            image_button = ttk.Button(master, image=dot, padding=cls.PADDING)
            overhead = image_button.winfo_reqheight() - 1
            size = text_button.winfo_reqheight() - overhead
            text_button.destroy()
            image_button.destroy()
        except tk.TclError:
            return 16
        return max(8, min(size, 64))

    def _show_hint(self, text: str) -> None:
        self._hide_hint()
        palette = colors()
        self._hint_window = tk.Toplevel(self)
        self._hint_window.wm_overrideredirect(True)
        self._hint_window.wm_geometry("+%d+%d" % (self.winfo_rootx(), self.winfo_rooty() + self.size + 8))
        tk.Label(self._hint_window, text=text, background=palette["surface"], foreground=palette["text"],
                 borderwidth=1, relief="solid", padx=4).pack()

    def _hide_hint(self) -> None:
        if self._hint_window is not None:
            self._hint_window.destroy()
            self._hint_window = None

    def redraw(self) -> None:
        """Перерисовывает значок цветом текущего оформления."""
        size, color = self.size, colors()["text"]
        # новая картинка прозрачна целиком, закрашиваются только штрихи значка —
        # поэтому кнопка подсвечивается под мышью, а значок не «обрастает» фоном
        image = tk.PhotoImage(master=self, width=size, height=size)
        for box in (_PLUS if self.kind == "add" else _URN)(size):
            image.put(color, to=box)
        self._image = image            # ссылку надо держать, иначе картинку соберёт сборщик мусора
        self.configure(image=image)


def _PLUS(size: int):
    """Две полоски крест-накрест."""
    thick = max(2, size // 8)
    pad = max(2, size // 5)
    middle = (size - thick) // 2
    return [(pad, middle, size - pad, middle + thick),
            (middle, pad, middle + thick, size - pad)]


def _URN(size: int):
    """Корпус, крышка и ручка."""
    thick = max(1, size // 10)
    left, right = size // 4, size - size // 4
    top, bottom = size // 3, size - size // 8
    return [
        (left, top, left + thick, bottom),                 # левая стенка
        (right - thick, top, right, bottom),               # правая стенка
        (left, bottom - thick, right, bottom),             # дно
        (size // 8, top - thick, size - size // 8, top),   # крышка
        (size // 3, top - 2 * thick, size - size // 3, top - thick),   # ручка
    ]


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
