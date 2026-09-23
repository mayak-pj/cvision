"""Светлая тема и размещение окон."""

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

from photo_renamer.gui import theme  # noqa: E402


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
