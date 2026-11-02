"""Главное окно на PyQt5: те же действия, что у окна на tkinter (логика у них общая)."""

import os
import sys

import pytest

pytest.importorskip("PyQt5.QtWidgets")
# окно не выводится на экран: так же и не конфликтует с Tk в одном процессе
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from photo_renamer import config as cfg, launcher  # noqa: E402
from photo_renamer.gui_qt import app as qt_app, theme as qt_theme  # noqa: E402
from photo_renamer.project_session import MARK_NO, MARK_REFERENCE, MARK_YES  # noqa: E402
from test_grouping import shoot  # noqa: E402

REF_NAMES = ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "Кабель HDMI.JPG",
             "Радиатор 1.JPG", "Радиатор 2.JPG"]
PARTS = [1, 2, 3, 4, 5, 6]


_app = QApplication.instance() or QApplication([sys.argv[0]])   # должно жить, пока живут окна


@pytest.fixture
def window(tmp_path, monkeypatch):
    said = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda parent, title, text: said.append(text))
    monkeypatch.setattr(cfg, "save_config", lambda *a, **k: None)
    config = cfg.AppConfig()
    config.cache_dir = str(tmp_path / "cache")
    config.logs_dir = str(tmp_path / "logs")
    config.names_file = str(tmp_path / "names.txt")
    win = qt_app.MainWindow(config)
    win.said = said
    yield win
    win.wait_for_worker()
    win.close()
    win.deleteLater()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Проект"
    shoot(root / "эталон", PARTS, names=REF_NAMES)
    shoot(root / "SN2", PARTS)
    return root


def analyzed(window, project):
    window.root_edit.setText(str(project))
    window.analyze()
    window.wait_for_worker()
    assert window.analysis is not None and not window._busy
    return window


def folder_items(window):
    group = window.tree.topLevelItem(0)
    return [group.child(k) for k in range(group.childCount())]


def item_of(window, mark):
    return next(item for item in folder_items(window) if (item.text(1) == MARK_REFERENCE) == mark)


def test_analysis_fills_the_table_like_the_session(window, project):
    analyzed(window, project)
    rows = window.session.rows()
    assert window.tree.topLevelItemCount() == len(rows)
    assert window.tree.topLevelItem(0).text(0) == rows[0].text
    assert [item.text(0) for item in folder_items(window)] == [row.text for row in rows[0].children]
    assert "групп: 1" in window.status_label.text()
    assert not window.progress.isVisibleTo(window) and not window.cancel_btn.isVisibleTo(window)


def test_missing_folder_is_reported(window, tmp_path):
    window.root_edit.setText(str(tmp_path / "нет такой"))
    window.analyze()
    assert "Папка не найдена" in window.said[0] and window.analysis is None


def test_buttons_follow_the_selection(window, project):
    assert not window.reference_btn.isEnabled() and not window.refresh_btn.isEnabled()
    analyzed(window, project)
    assert window.refresh_btn.isEnabled() and not window.open_btn.isEnabled()
    item_of(window, True).setSelected(True)
    assert not window.reference_btn.isEnabled() and window.open_btn.isEnabled()
    window.tree.clearSelection()
    item_of(window, False).setSelected(True)
    assert window.reference_btn.isEnabled() and window.rename_group_btn.isEnabled()


def test_click_on_rename_column_toggles_the_mark(window, project):
    analyzed(window, project)
    item = item_of(window, False)
    item.setSelected(True)
    was = item.text(qt_app.COLUMN_RENAME)
    window._on_click(item, qt_app.COLUMN_RENAME)
    assert item_of(window, False).text(qt_app.COLUMN_RENAME) == (MARK_NO if was == MARK_YES else MARK_YES)
    assert window._selected_folder() is item_of(window, False).folder   # выделение на месте


def test_click_on_reference_column_moves_the_mark(window, project):
    analyzed(window, project)
    item = item_of(window, False)
    folder = item.folder
    item.setSelected(True)
    window._on_click(item, qt_app.COLUMN_REFERENCE)
    assert item_of(window, True).folder is folder


def test_move_menu_lists_groups_and_new_group(window, project):
    analyzed(window, project)
    item_of(window, False).setSelected(True)
    window._fill_move_menu()
    texts = [action.text() for action in window.move_menu.actions() if not action.isSeparator()]
    assert texts[-1] == "Новая группа" and texts[0].startswith("Группа 1")
    assert not window.move_menu.actions()[0].isEnabled()       # в свою группу не переносят
    window.move_selected(None)
    assert window.tree.topLevelItemCount() == 2


def test_analysis_can_be_cancelled(window, project):
    original = window._on_worker_message

    def cancel_on_first(message):
        window.cancel()
        original(message)

    window._on_worker_message = cancel_on_first
    window.root_edit.setText(str(project))
    window.analyze()
    window.wait_for_worker()
    assert window.status_label.text() == "Анализ прерван." and not window._busy


def test_close_during_analysis_waits_for_the_worker(window, project):
    window.show()
    window.root_edit.setText(str(project))
    window.analyze()
    assert window.close() is False and window.isVisible()
    window.wait_for_worker()
    assert not window.isVisible()


def test_project_is_remembered(window, project):
    analyzed(window, project)
    assert window.session.load_state()["project"] == str(project)


def test_lamp_switches_theme_and_remembers(window, monkeypatch):
    saved = []
    monkeypatch.setattr(cfg, "save_config", lambda config, path=None: saved.append(config.theme))
    window.lamp.click()
    assert window.theme.name == "dark" and saved == ["dark"]
    assert QApplication.instance().palette().window().color().name() == qt_theme.DARK["bg"]
    window.lamp.click()
    assert window.theme.name == "light" and saved == ["dark", "light"]


def test_windows_not_yet_ported_say_so(window):
    window.edit_reference()
    window.rename_group()
    assert len(window.said) == 2 and all("--tk" in text for text in window.said)


def test_themes_have_the_same_tokens():
    assert set(qt_theme.LIGHT) == set(qt_theme.DARK)


def test_every_icon_is_drawn(window):
    for name in ("folder-open", "search", "square", "lightbulb", "lightbulb-off", "refresh-cw"):
        assert not window.theme.icon(name).isNull()


def test_interface_setting_is_checked():
    assert cfg.from_dict({"interface": "qt"})[0].interface == "qt"
    config, warnings = cfg.from_dict({"interface": "gtk"})
    assert config.interface == "tk" and warnings


def test_launcher_falls_back_when_qt_is_missing(monkeypatch):
    """Нет PyQt5 — программа всё равно открывается, в прежнем окне."""
    import photo_renamer.gui.app as tk_app

    opened = []
    monkeypatch.setattr(launcher, "load_qt", lambda: None)
    monkeypatch.setattr(tk_app, "main", lambda: opened.append("tk"))
    launcher.main("qt")
    assert opened == ["tk"]


def test_launcher_opens_qt_when_asked(monkeypatch):
    class Qt:
        @staticmethod
        def main(config, warnings):
            opened.append("qt")

    opened = []
    monkeypatch.setattr(launcher, "load_qt", lambda: Qt)
    launcher.main("qt")
    assert opened == ["qt"]
