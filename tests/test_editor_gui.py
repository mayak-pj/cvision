"""Редактор эталона: выделение → категория → переименование на диске, отмена, список имён."""

import os

import pytest

tk = pytest.importorskip("tkinter")

from photo_renamer import config as cfg  # noqa: E402
from photo_renamer.gui import editor as editor_mod  # noqa: E402
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
    win.withdraw()
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
