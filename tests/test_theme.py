"""Светлая тема и размещение окон."""

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

from photo_renamer.gui import theme  # noqa: E402


@pytest.fixture
def keyboard_window(root):
    """Окно для проверки сочетаний клавиш, которое не видно и не мешает работать за машиной.

    Клавиатурные события Tk доставляет только окну, у которого есть фокус, а фокус бывает
    только у показанного окна. Поэтому окно на миг открывается прозрачным размером 1×1
    (на экране не появляется ничего), забирает фокус и сразу прячется: события после этого
    доходят, а окна на экране нет.
    """
    window = tk.Toplevel(root)
    window.geometry("1x1+0+0")
    window.attributes("-alpha", 0.0)
    window.update()
    window.focus_force()
    window.withdraw()
    window.update()
    yield window
    window.destroy()


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("нет графической среды: %s" % exc)
    window.withdraw()
    yield window
    window.destroy()


def test_light_theme_applied(root):
    theme.apply(root, "light")
    palette = theme.colors()
    assert ttk.Style(root).theme_use() == "clam"
    assert root.cget("background") == palette["background"]
    listbox = tk.Listbox(root)
    assert listbox.cget("background") == palette["surface"]
    assert listbox.cget("foreground") == palette["text"]
    assert listbox.cget("selectbackground") == palette["select_bg"]
    text = tk.Text(root)
    assert text.cget("background") == palette["surface"]


def test_system_theme_changes_nothing(root):
    before = root.cget("background")
    theme.apply(root, "system")
    assert root.cget("background") == before


def test_fit_to_screen_keeps_top_margin(root):
    window = tk.Toplevel(root)
    window.withdraw()
    theme.fit_to_screen(window, top_margin=60)
    window.update_idletasks()
    width, rest = window.geometry().split("x")
    height, x, y = rest.replace("+", " ").split()
    assert int(y) == 60                                    # не под заголовком окна
    assert int(height) + 60 <= window.winfo_screenheight()
    assert int(width) <= window.winfo_screenwidth()
    window.destroy()


def test_dark_theme_and_lamp(root):
    theme.apply(root, "light")
    assert theme.current_mode() == "light"
    lamp = theme.LampButton(root)
    assert lamp.on and lamp.find_all()          # лампочка нарисована

    toggled = []
    lamp.command = toggled.append
    lamp.toggle()
    assert lamp.on is False and toggled == [False]

    theme.apply(root, "dark")
    dark = theme.colors()
    assert theme.current_mode() == "dark"
    assert dark["background"] == theme.DARK["background"]
    assert root.cget("background") == dark["background"]


def test_apply_to_existing_recolors_open_windows(root):
    theme.apply(root, "light")
    window = tk.Toplevel(root)
    window.withdraw()
    listbox = tk.Listbox(window)
    listbox.pack()
    assert listbox.cget("background") == theme.LIGHT["surface"]

    theme.apply(root, "dark")
    theme.apply_to_existing(root)
    assert listbox.cget("background") == theme.DARK["surface"]
    assert window.cget("background") == theme.DARK["background"]
    window.destroy()


def test_table_heading_stays_readable_under_mouse(root):
    """Заголовки столбцов — кнопки сортировки: под мышью clam подсвечивал их системным
    светлым цветом, и на тёмном оформлении текст пропадал."""
    from tkinter import ttk

    for mode in ("dark", "light"):
        theme.apply(root, mode)
        style = ttk.Style(root)
        palette = theme.colors(mode)
        assert style.lookup("Treeview.Heading", "background", ["active"]) == palette["button_active"]
        assert style.lookup("Treeview.Heading", "foreground", ["active"]) == palette["text"]
        assert style.lookup("Treeview.Heading", "foreground", ["pressed"]) == palette["select_fg"]


def test_clipboard_keys_work_in_any_layout(root):
    """Ctrl+V при русской раскладке: Tk видит «Ctrl+М», поэтому разбираем код клавиши.

    Замечание с рабочей машины: «Нельзя вставить ctrl+v в окно категории и в окно добавить».
    """
    assert theme.clipboard_action(86, 0x0004) == "<<Paste>>"       # V
    assert theme.clipboard_action(67, 0x0004) == "<<Copy>>"        # C
    assert theme.clipboard_action(88, 0x0004) == "<<Cut>>"         # X
    assert theme.clipboard_action(90, 0x0004) == "<<Undo>>"        # Z
    assert theme.clipboard_action(65, 0x0004) == "select-all"      # A
    assert theme.clipboard_action(86, 0) == ""                     # без Ctrl — обычный ввод
    assert theme.clipboard_action(86, 0x0004 | 0x20000) == ""      # Ctrl+Alt (AltGr) — не сочетание
    assert theme.clipboard_action(70, 0x0004) == ""                # Ctrl+F к буферу не относится


def test_letter_wins_over_the_key_code():
    """Если признаки разошлись, вернее буква: код нужен там, где буквы нет."""
    assert theme.clipboard_action(86, 0x0004, "c", "c") == "<<Copy>>"   # код V, буква C
    assert theme.clipboard_action(0, 0x0004, "v", "v") == "<<Paste>>"   # кода нет — только буква
    assert theme.clipboard_action(86, 0x0004, "", "") == "<<Paste>>"    # буквы нет — только код


def test_clipboard_keys_by_letter_of_the_russian_layout():
    """На macOS кода клавиши, не зависящего от раскладки, нет — узнаём букву-двойник."""
    assert theme.clipboard_action(0, 0x0004, "Cyrillic_em", "м") == "<<Paste>>"     # V
    assert theme.clipboard_action(0, 0x0004, "Cyrillic_es", "с") == "<<Copy>>"      # C
    assert theme.clipboard_action(0, 0x0004, "Cyrillic_che", "ч") == "<<Cut>>"      # X
    assert theme.clipboard_action(0, 0x0004, "Cyrillic_ya", "я") == "<<Undo>>"      # Z
    assert theme.clipboard_action(0, 0x0004, "Cyrillic_ef", "ф") == "select-all"    # A
    assert theme.clipboard_action(0, 0x0004, "Cyrillic_be", "б") == ""              # чужая клавиша


def test_command_is_a_shortcut_only_on_mac(monkeypatch):
    """На Windows «Command» — это Mod1, который выставляет NumLock: сочетанием он не считается.

    Иначе при включённом NumLock обычная буква «v» сработала бы как вставка.
    """
    monkeypatch.setattr(theme, "COMMAND_IS_A_SHORTCUT", True)          # macOS
    assert theme.clipboard_action(0, 0x0008, "v", "v") == "<<Paste>>"
    assert theme.clipboard_action(0, 0x0008, "Cyrillic_em", "м") == "<<Paste>>"
    assert theme.shortcut_pressed(0x0008)

    monkeypatch.setattr(theme, "COMMAND_IS_A_SHORTCUT", False)         # Windows
    assert theme.clipboard_action(86, 0x0008, "v", "v") == ""
    assert not theme.shortcut_pressed(0x0008)
    assert theme.clipboard_action(86, 0x0004, "v", "v") == "<<Paste>>"  # Ctrl работает везде


def test_window_shortcuts_do_not_use_command_on_windows(root, monkeypatch):
    """Там же: сочетания окна на Windows привязываются только к Ctrl."""
    monkeypatch.setattr(theme, "COMMAND_IS_A_SHORTCUT", False)
    window = tk.Toplevel(root)
    theme.bind_shortcut(window, "s", lambda: None)
    bound = set(window.bind())
    assert "<Control-Key-s>" in bound
    assert not [sequence for sequence in bound if "Mod1" in sequence or "Command" in sequence]
    window.destroy()


def test_clipboard_keys_are_installed_for_entry_classes(root):
    """Привязка ставится на классы полей ввода, а не на каждое поле по отдельности."""
    theme.install_clipboard_keys(root)
    for name in ("Entry", "TEntry", "Text"):
        assert root.bind_class(name, "<Control-KeyPress>")
        if theme.COMMAND_IS_A_SHORTCUT:
            assert root.bind_class(name, "<Command-KeyPress>")


@pytest.mark.parametrize("letter, keysym, char, keycode, expected", [
    ("s", "s", "s", 83, True),                      # латинская раскладка
    ("s", "Cyrillic_yeru", "ы", 0, True),           # русская: буква-двойник на той же клавише
    ("s", "Cyrillic_yeru", "", 83, True),           # Windows: код клавиши от раскладки не зависит
    ("s", "Cyrillic_che", "ч", 0, False),           # другая клавиша
    ("z", "Cyrillic_ya", "я", 90, True),
    ("a", "Cyrillic_ef", "ф", 65, True),
    ("z", "s", "s", 83, False),                     # чужая буква и чужой код
])
def test_shortcut_key_is_recognised_in_any_layout(letter, keysym, char, keycode, expected):
    assert theme.shortcut_matches(letter, keysym, char, keycode) is expected


def test_command_counts_as_a_shortcut_but_alt_does_not():
    assert theme.shortcut_pressed(0x0004)                  # Ctrl
    assert theme.shortcut_pressed(0x0008) is theme.COMMAND_IS_A_SHORTCUT   # Command — только macOS
    assert not theme.shortcut_pressed(0)
    assert not theme.shortcut_pressed(0x0004 | 0x20000)    # Ctrl+Alt (AltGr) — не сочетание


def test_several_shortcuts_on_one_window_do_not_erase_each_other(keyboard_window):
    """Привязка следующей буквы не должна затирать разбор предыдущей."""
    window = keyboard_window
    fired = []
    for letter in ("z", "a", "s"):
        theme.bind_shortcut(window, letter, lambda l=letter: fired.append(l))
    window.event_generate("<Command-KeyPress>", keysym="Cyrillic_ya", state=0x0008, when="now")
    window.event_generate("<Control-KeyPress>", keysym="Cyrillic_yeru", state=0x0004, when="now")
    assert fired == ["z", "s"]


def test_shortcut_works_in_any_layout(keyboard_window):
    """Ctrl+S и Cmd+S в окне: и по букве, и в русской раскладке."""
    fired = []
    window = keyboard_window
    theme.bind_shortcut(window, "s", lambda: fired.append(1))

    window.event_generate("<Control-s>", when="now")
    assert len(fired) == 1
    window.event_generate("<Command-s>", when="now")        # macOS
    assert len(fired) == 2

    # русская раскладка: буквы «s» в событии нет — есть «ы» на той же клавише
    window.event_generate("<Control-KeyPress>", keysym="Cyrillic_yeru", state=0x0004, when="now")
    assert len(fired) == 3
    window.event_generate("<Command-KeyPress>", keysym="Cyrillic_yeru", state=0x0008, when="now")
    assert len(fired) == 4

    window.event_generate("<Control-KeyPress>", keysym="Cyrillic_che", state=0x0004, when="now")
    assert len(fired) == 4                      # другая клавиша — ничего не делаем
    window.event_generate("<KeyPress>", keysym="Cyrillic_yeru", state=0, when="now")
    assert len(fired) == 4                      # без Ctrl — обычный ввод
    window.event_generate("<Control-KeyPress>", keysym="Cyrillic_yeru",
                          state=0x0004 | 0x20000, when="now")
    assert len(fired) == 4                      # Ctrl+Alt (AltGr) — не сочетание


def test_shortcut_does_not_fire_twice_on_one_press(keyboard_window):
    """Обе привязки стоят на одном виджете, но Tk выбирает самую точную — действие одно."""
    fired = []
    theme.bind_shortcut(keyboard_window, "z", lambda: fired.append(1))
    keyboard_window.event_generate("<Control-z>", when="now")
    assert fired == [1]


def test_drawn_icons_are_repainted_with_the_theme(root):
    """Значок нарисован цветами темы: после переключения он не должен остаться прежним."""
    theme.apply(root, "light")
    button = theme.IconButton(root, "add")
    button.pack()
    light = button.itemcget(button.find_all()[0], "fill")
    theme.apply(root, "dark")
    theme.apply_to_existing(root, "dark")
    assert button.itemcget(button.find_all()[0], "fill") != light
    assert button.cget("background") == theme.colors("dark")["surface"]
