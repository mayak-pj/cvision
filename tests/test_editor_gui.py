"""Редактор эталона: выделение → категория → переименование на диске, отмена, список имён."""

import os

import pytest

tk = pytest.importorskip("tkinter")

from photo_renamer import config as cfg  # noqa: E402
from photo_renamer.gui import editor as editor_mod, theme as theme_mod  # noqa: E402
from photo_renamer.names import NamesList  # noqa: E402
from test_grouping import shoot  # noqa: E402


@pytest.fixture
def editor(tmp_path, monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(editor_mod.messagebox, name, lambda *a, **k: None)
    monkeypatch.setattr(editor_mod.messagebox, "askyesno", lambda *a, **k: True)
    folder = tmp_path / "SN1"
    shoot(folder, [1, 2, 3, 4, 5, 6])
    config = cfg.AppConfig()
    config.cache_dir = str(tmp_path / "cache")
    config.logs_dir = str(tmp_path / "logs")
    names = NamesList(str(tmp_path / "names.txt"))
    names.add("Плата оперативной памяти", 2)
    names.add("Кулер", 1)
    names.add("Вентилятор", 1)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("нет графической среды: %s" % exc)
    root.withdraw()
    win = editor_mod.ReferenceEditor(root, str(folder), config, names)
    # окно остаётся «показанным» и с фокусом, иначе до него не дойдут нажатия клавиш.
    # На экране его всё равно нет: conftest делает окна прозрачными и запрещает процессу
    # выходить на передний план
    win.deiconify()
    win.update()
    win.focus_force()
    win.update()
    yield win, folder, names
    win.log.close()
    win.cache.close()
    win.destroy()
    root.destroy()


def pick_name(win, name):
    for i in range(win.names_list.size()):
        if win.names_list.get(i) == name:
            win.names_list.selection_clear(0, "end")
            win.names_list.selection_set(i)
            win._on_name_selected()
            return
    raise AssertionError(name)


def test_loads_folder_in_shooting_order(editor):
    win, folder, _ = editor
    assert win.photos == sorted(os.listdir(str(folder)))
    assert "Фото: 6" in win.status_var.get() and "осталось: 6" in win.status_var.get()


def test_rename_two_sided_instances(editor):
    win, folder, names = editor
    win.grid_widget.set_selection([0, 1, 2, 3])
    pick_name(win, "Плата оперативной памяти")
    assert win.sides_var.get() == 2          # число ракурсов подставилось из списка
    win.rename_selected()
    renamed = ["Плата оперативной памяти 1 фото 1.JPG", "Плата оперативной памяти 1 фото 2.JPG",
               "Плата оперативной памяти 2 фото 1.JPG", "Плата оперативной памяти 2 фото 2.JPG"]
    assert set(renamed) <= set(os.listdir(str(folder)))
    assert win.photos[:4] == renamed         # порядок съёмки сохранён, имена по порядку выделения
    assert "осталось: 2" in win.status_var.get()
    assert win.grid_widget.selection == set()


def test_second_instance_renumbers_first(editor):
    win, folder, _ = editor
    pick_name(win, "Кулер")
    win.grid_widget.set_selection([4])
    win.rename_selected()
    assert "Кулер.JPG" in os.listdir(str(folder))
    win.grid_widget.set_selection([5])
    win.rename_selected()
    on_disk = os.listdir(str(folder))
    assert "Кулер 1.JPG" in on_disk and "Кулер 2.JPG" in on_disk and "Кулер.JPG" not in on_disk


def test_undo_restores_names(editor):
    win, folder, _ = editor
    before = sorted(os.listdir(str(folder)))
    pick_name(win, "Вентилятор")
    win.grid_widget.set_selection([0, 1])
    win.rename_selected()
    assert sorted(os.listdir(str(folder))) != before
    win.undo()
    assert sorted(os.listdir(str(folder))) == before
    assert win.photos == before
    assert str(win.undo_btn["state"]) == "disabled"


def test_sides_are_remembered(editor):
    win, folder, names = editor
    pick_name(win, "Вентилятор")
    win.sides_var.set(3)
    win.grid_widget.set_selection([0, 1, 2])
    win.rename_selected()
    assert names.sides_for("Вентилятор") == 3
    assert NamesList(names.path).sides_for("Вентилятор") == 3   # сохранено в файл


def test_search_filters_names(editor):
    win, _, _ = editor
    win.search_var.set("пам")
    win.update()
    assert win.names_list.get(0) == "Плата оперативной памяти" and win.names_list.size() == 1
    win.search_var.set("")
    win.update()
    assert win.names_list.size() == 3


def test_rename_without_category_or_selection_does_nothing(editor):
    win, folder, _ = editor
    before = sorted(os.listdir(str(folder)))
    win.grid_widget.set_selection([0])
    win.names_list.selection_clear(0, "end")
    win.rename_selected()                      # категория не выбрана
    pick_name(win, "Кулер")
    win.grid_widget.clear_selection()
    win.rename_selected()                      # ничего не выделено
    assert sorted(os.listdir(str(folder))) == before


def test_zoom_limits(editor):
    win, _, _ = editor
    win.grid_widget.set_cell_size(10_000)
    assert win.grid_widget.cell_size == editor_mod.MAX_SIZE
    win.grid_widget.set_cell_size(1)
    assert win.grid_widget.cell_size == editor_mod.MIN_SIZE


def test_thumbnails_load_in_background(editor):
    """Миниатюры читаются фоновой задачей и появляются в сетке, окно при этом не блокируется."""
    win, folder, _ = editor
    grid = win.grid_widget
    assert grid.worker is not None
    win.worker.wait(30)
    win.update()
    assert grid._pending == len(win.photos)
    assert all(image for image in grid._images), "не все миниатюры загружены"
    assert "загрузка миниатюр" not in win.status_var.get()


def test_editor_answers_clicks_while_thumbnails_load(editor, monkeypatch):
    import time

    from photo_renamer.gui import editor as editor_module

    win, folder, _ = editor
    real = editor_module.PhotoCache

    class SlowCache(real):
        def thumbnail(self, path):
            time.sleep(0.2)                     # медленное чтение, как по сети
            return super().thumbnail(path)

    monkeypatch.setattr(editor_module, "PhotoCache", SlowCache)
    win.grid_widget.set_items(win._items(), win._template_flags())
    assert win.worker.busy()

    clicks = []
    win.rename_btn.configure(command=lambda: clicks.append(1))
    started = time.perf_counter()
    win.rename_btn.event_generate("<Button-1>")
    win.rename_btn.event_generate("<ButtonRelease-1>")
    while not clicks and time.perf_counter() - started < 5:
        win.update()
    assert clicks and time.perf_counter() - started < 0.2
    win.worker.cancel()
    win.worker.wait(5)


def test_double_click_opens_the_photo_large(editor, monkeypatch):
    """Двойной клик по миниатюре показывает фото крупно.

    Раньше LargeView в редакторе не был импортирован, и двойной клик падал с NameError.
    """
    win, folder, _ = editor
    opened = []
    monkeypatch.setattr(editor_mod, "LargeView",
                        lambda master, path, photos, index, cache: opened.append((path, photos[index])))
    x0, y0, x1, y1 = win.grid_widget._cell_box(0)
    win.grid_widget._on_double(type("Event", (), {"x": (x0 + x1) // 2, "y": (y0 + y1) // 2})())
    assert opened == [(str(folder), win.photos[0])]
    win._open_large(2)
    assert opened[-1] == (str(folder), win.photos[2])


def test_add_category_without_a_dialog(editor):
    """Набрано в поиске, нажат «+» — категория добавлена сразу, без окна с «ОК»."""
    win, _, names = editor
    win.search_var.set("Планка индикации")
    win.sides_var.set(2)
    win.add_name()
    assert "Планка индикации" in names.names()
    assert names.sides_for("Планка индикации") == 2
    assert win.search_var.get() == ""                    # поиск очищен, видно весь список
    assert win.current_name() == "Планка индикации"      # и новая категория уже выбрана
    assert "Планка индикации" in NamesList(names.path).names()   # записано в файл


def test_add_category_with_empty_search_only_hints(editor):
    win, _, names = editor
    before = list(names.names())
    win.search_var.set("  ")
    win.add_name()
    assert names.names() == before
    assert "Наберите название" in win.status_var.get()


def test_ctrl_s_renames_selected(editor):
    """Ctrl+S делает то же, что кнопка «Переименовать выделенные»."""
    from photo_renamer.gui import theme

    win, folder, _ = editor
    assert theme.shortcut_label(win, "s") in win.rename_btn.cget("text")
    pick_name(win, "Кулер")
    win.grid_widget.select_all()
    win.update()
    win.event_generate("<Control-s>", when="now")
    win.update()
    assert any(name.startswith("Кулер") for name in os.listdir(str(folder)))


def test_ctrl_s_works_in_russian_layout(editor):
    """В русской раскладке приходит «Ctrl+ы» — сочетание всё равно срабатывает."""
    win, folder, _ = editor
    pick_name(win, "Кулер")
    win.grid_widget.select_all()
    win.update()
    win.event_generate("<Control-KeyPress>", keysym="Cyrillic_yeru", state=0x0004, when="now")
    win.update()
    assert any(name.startswith("Кулер") for name in os.listdir(str(folder)))


def test_command_and_russian_layout_shortcuts(editor):
    """macOS: то же сочетание нажимают Command; в русской раскладке приходит «⌘ы»/«Ctrl+я»."""
    win, folder, _ = editor
    pick_name(win, "Кулер")
    win.grid_widget.select_all()
    win.update()
    win.event_generate("<Command-s>", when="now")
    win.update()
    assert any(name.startswith("Кулер") for name in os.listdir(str(folder)))

    win.event_generate("<Command-KeyPress>", keysym="Cyrillic_ya", state=0x0008, when="now")
    win.update()
    assert not any(name.startswith("Кулер") for name in os.listdir(str(folder))), "Cmd+я не отменил"


def test_shortcut_label_matches_the_system(editor):
    from photo_renamer.gui import theme

    win, _, _ = editor
    aqua = win.tk.call("tk", "windowingsystem") == "aqua"
    assert theme.shortcut_label(win, "s") == ("\u2318S" if aqua else "Ctrl+S")


def test_renamed_photos_are_grouped_but_numbered_by_shooting_order(editor):
    """Показ — по именам (экземпляры рядом), нумерация — по съёмке (порядок съёмки не меняется)."""
    win, folder, _ = editor
    shot_order = list(win.photos)
    pick_name(win, "Кулер")                       # один ракурс
    win.grid_widget.set_selection([0, 4])         # первое и пятое фото по съёмке
    win.rename_selected()

    assert win.photos[0] == "Кулер 1.JPG" and win.photos[4] == "Кулер 2.JPG"
    assert [name for name in win.photos if not name.startswith("Кулер")] == \
           [name for name in shot_order if name not in (shot_order[0], shot_order[4])]
    assert win.shown[:2] == ["Кулер 1.JPG", "Кулер 2.JPG"]          # в сетке стоят рядом
    assert win.shown[2:] == [shot_order[i] for i in (1, 2, 3, 5)]   # остальные — по съёмке
    assert [name for name, _ in win.grid_widget.items] == win.shown


def test_second_category_keeps_its_own_instances_together(editor):
    """После первого переименования сетка уже переставлена: неназванные — в конце."""
    win, folder, _ = editor
    pick_name(win, "Вентилятор")
    win.grid_widget.set_selection([1, 5])
    win.rename_selected()
    assert win.shown[:2] == ["Вентилятор 1.JPG", "Вентилятор 2.JPG"]

    pick_name(win, "Кулер")
    win.grid_widget.set_selection([2])            # первое из оставшихся неназванных
    win.rename_selected()
    assert win.shown[:3] == ["Вентилятор 1.JPG", "Вентилятор 2.JPG", "Кулер.JPG"]
    assert all(not is_named(name) for name in win.shown[3:])


def is_named(name):
    from photo_renamer.reference import is_template_name

    return is_template_name(name)


def test_reorder_keeps_thumbnails_and_selection_by_name(editor):
    """Перестановка не перечитывает миниатюры и не теряет выделение."""
    win, _, _ = editor
    win.worker.wait(30)
    win.update()
    assert all(image for image in win.grid_widget._images)
    pick_name(win, "Кулер")
    win.grid_widget.set_selection([0, 4])
    win.rename_selected()
    win.update()
    assert all(image for image in win.grid_widget._images), "миниатюры пропали после перестановки"
    assert win.grid_widget._pending == len(win.photos)


# Windows-путь (клавиша узнаётся по коду) здесь не проверить: на macOS Tk сам подставляет
# к заданному коду свой keysym, и событие получается противоречивым — код говорит «V»,
# а keysym «a». Этот путь проверяется без окна, в test_theme.clipboard_action.
PASTE_KEYS = [
    ("латиница", "<Control-v>", {}),
    ("русская раскладка", "<Control-KeyPress>", dict(keysym="Cyrillic_em", state=0x0004)),
]
if theme_mod.COMMAND_IS_A_SHORTCUT:
    PASTE_KEYS += [
        ("macOS ⌘V", "<Command-v>", {}),
        ("macOS ⌘м", "<Command-KeyPress>", dict(keysym="Cyrillic_em", state=0x0008)),
    ]


@pytest.mark.parametrize("label, sequence, event", PASTE_KEYS)
def test_paste_into_the_category_field(editor, label, sequence, event):
    """Замечание с рабочей машины и с мака: в поле категории не вставлялось из буфера."""
    win, _, _ = editor
    theme_mod.install_clipboard_keys(win)
    win.clipboard_clear()
    win.clipboard_append("Плата управления")
    win.search_var.set("")
    win.search_entry.focus_set()
    win.update()
    win.search_entry.event_generate(sequence, when="now", **event)
    win.update()
    assert win.search_var.get() == "Плата управления", label
