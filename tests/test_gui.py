"""Первое окно: анализ проекта, дерево групп, действия. Окно создаётся скрытым."""

import os

import pytest

tk = pytest.importorskip("tkinter")

from photo_renamer import config as cfg  # noqa: E402
from photo_renamer.gui import app as gui_app  # noqa: E402
from test_grouping import shoot  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(gui_app.messagebox, name, lambda *a, **k: None)
    config = cfg.AppConfig()
    config.cache_dir = str(tmp_path / "cache")
    config.logs_dir = str(tmp_path / "logs")
    try:
        win = gui_app.MainWindow(config)
    except tk.TclError as exc:
        pytest.skip("нет графической среды: %s" % exc)
    win.withdraw()
    yield win
    win.destroy()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Проект"
    ref_names = ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "Кабель HDMI.JPG",
                 "Радиатор 1.JPG", "Радиатор 2.JPG"]
    shoot(root / "A" / "эталон", [1, 2, 3, 4, 5, 6], names=ref_names)
    shoot(root / "A" / "SN2", [1, 2, 3, 4, 5, 6])
    shoot(root / "B" / "SN4", [11, 12, 13, 14, 15, 16, 17])
    shoot(root / "B" / "SN5", [11, 12, 13, 14, 15, 16, 17, 18])
    return root


def folder_rows(win):
    rows = []
    for gid in win.tree.get_children():
        for iid in win.tree.get_children(gid):
            rows.append((win._item_group[iid], win.tree.item(iid, "text"), tuple(win.tree.item(iid, "values"))))
    return rows


def test_analyze_and_render(window, project):
    window.root_var.set(str(project))
    window.analyze()
    assert window.analysis is not None and not window._busy
    groups = window.tree.get_children()
    assert len(groups) == 2
    assert "эталон готов" in window.tree.item(groups[0], "text")
    assert "нужно создать эталон" in window.tree.item(groups[1], "text")
    rows = folder_rows(window)
    assert (0, os.path.join("Проект", "A", "эталон"), ("6", "ЭТАЛОН — готов", "")) in rows
    assert any(r[1].endswith("SN5") and r[2][1] == "ЭТАЛОН — нужно переименовать вручную" for r in rows)
    assert "Нужно создать эталонов: 1" in window.status_var.get()
    # папка проекта запоминается
    assert window._load_state()["project"] == str(project)


def select(window, suffix):
    for iid, folder in window._item_folder.items():
        if folder.path.endswith(suffix):
            window.tree.selection_set(iid)
            window._update_buttons()
            return folder
    raise AssertionError(suffix)


def test_buttons_follow_selection(window, project):
    window.root_var.set(str(project))
    window.analyze()
    assert str(window.reference_btn["state"]) == "disabled"
    select(window, "SN2")
    assert str(window.reference_btn["state"]) == "normal"
    assert str(window.rename_group_btn["state"]) == "normal"     # в группе готовый эталон
    select(window, "SN4")
    assert str(window.rename_group_btn["state"]) == "disabled"   # эталон ещё не создан


def test_make_reference_and_move(window, project):
    window.root_var.set(str(project))
    window.analyze()
    folder = select(window, "SN4")
    window.make_reference()
    assert window.analysis.groups[1].reference is folder
    select(window, "SN2")
    window.move_selected(None)                                   # в новую группу
    assert len(window.tree.get_children()) == 3
    select(window, "SN2")
    window.move_selected(0)                                      # обратно
    assert len(window.tree.get_children()) == 2


def test_refresh_after_manual_rename(window, project):
    window.root_var.set(str(project))
    window.analyze()
    folder = select(window, "SN5")
    names = ["Кулер.JPG", "Вентилятор 1.JPG", "Вентилятор 2.JPG", "Кабель HDMI.JPG", "Радиатор 1.JPG",
             "Радиатор 2.JPG", "Плата фото 1.JPG", "Плата фото 2.JPG"]
    for old, new in zip(sorted(os.listdir(folder.path)), names):
        os.rename(os.path.join(folder.path, old), os.path.join(folder.path, new))
    window.refresh()
    assert "Все эталоны готовы" in window.status_var.get()


def test_missing_folder_and_cancel(window, tmp_path, project):
    window.root_var.set(str(tmp_path / "нет"))
    window.analyze()
    assert window.analysis is None
    window.root_var.set(str(project))
    original = window._on_progress

    def cancel_on_first(done, total):
        window.cancel()
        original(done, total)

    window._on_progress = cancel_on_first
    window.analyze()
    assert window.status_var.get() == "Анализ прерван." and not window._busy
