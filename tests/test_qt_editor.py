"""Редактор эталона на PyQt5: те же действия и клавиши, что у окна на tkinter (логика у них общая)."""

import os
import sys
import time

import pytest

pytest.importorskip("PyQt5.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QFont, QFontMetrics, QMouseEvent, QWheelEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from photo_renamer import config as cfg  # noqa: E402
from photo_renamer.gui_qt import editor as qt_editor, theme as qt_theme, thumbgrid  # noqa: E402
from photo_renamer.names import NamesList  # noqa: E402
from photo_renamer.reference import is_template_name  # noqa: E402
from test_grouping import shoot  # noqa: E402

_app = QApplication.instance() or QApplication([sys.argv[0]])


@pytest.fixture
def dialogs(monkeypatch):
    said = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda parent, title, text: said.append(text))
    return said


def open_editor(tmp_path, folder, names=None):
    config = cfg.AppConfig()
    # без CLIP (его в тестах нет) искусственные снимки одной комплектации отстоят на ~0.45
    config.group_threshold = 0.6
    config.cache_dir = str(tmp_path / "cache")
    config.logs_dir = str(tmp_path / "logs")
    if names is None:
        names = NamesList(str(tmp_path / "names.txt"))
        names.add("Плата оперативной памяти", 2)
        names.add("Кулер", 1)
        names.add("Вентилятор", 1)
    theme = qt_theme.apply(_app, "light", str(tmp_path / "theme"))
    win = qt_editor.ReferenceEditor(None, str(folder), config, theme, names)
    win.setAttribute(Qt.WA_DeleteOnClose, False)     # окно нужно тестам и после закрытия
    win.asked = []
    win._ask = lambda question: win.asked.append(question) or True
    return win, names


@pytest.fixture
def editor(tmp_path, dialogs):
    folder = tmp_path / "SN1"
    shoot(folder, [1, 2, 3, 4, 5, 6])
    win, names = open_editor(tmp_path, folder)
    win.said = dialogs
    win.start()
    yield win, folder, names
    win.close()
    win.deleteLater()


def pick_name(win, name):
    items = win.names_list.findItems(name, Qt.MatchExactly)
    assert items, name
    win.names_list.setCurrentItem(items[0])


def wait_for_thumbnails(win):
    win.worker.wait(30)
    deadline = time.time() + 5
    while win.grid.loaded_count() < len(win.grid.cells) and time.time() < deadline:
        win.worker.wait(1)
        QApplication.processEvents()


def wheel(modifiers):
    return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, modifiers,
                       Qt.NoScrollPhase, False)


def shortcuts_ready(win):
    win.show()
    win.activateWindow()
    QTest.qWaitForWindowActive(win)
    if QApplication.activeWindow() is not win:
        pytest.skip("окно не стало активным")


def test_loads_folder_in_shooting_order(editor):
    win, folder, _ = editor
    assert win.session.photos == sorted(os.listdir(str(folder)))
    assert "Фото: 6" in win.status_label.text() and "осталось: 6" in win.status_label.text()
    assert win.windowTitle() == "Эталон: %s" % folder


def test_rename_two_sided_instances(editor):
    win, folder, _ = editor
    win.grid.set_selection([0, 1, 2, 3])
    pick_name(win, "Плата оперативной памяти")
    assert win.sides_spin.value() == 2       # число ракурсов подставилось из списка
    win.rename_btn.click()
    renamed = ["Плата оперативной памяти 1 фото 1.JPG", "Плата оперативной памяти 1 фото 2.JPG",
               "Плата оперативной памяти 2 фото 1.JPG", "Плата оперативной памяти 2 фото 2.JPG"]
    assert set(renamed) <= set(os.listdir(str(folder)))
    assert win.session.photos[:4] == renamed
    assert "осталось: 2" in win.status_label.text()
    assert win.grid.selection == set() and win.selection_label.text() == "Выделено: 0"


def test_second_instance_renumbers_first(editor):
    win, folder, _ = editor
    pick_name(win, "Кулер")
    win.grid.set_selection([4])
    win.rename_selected()
    assert "Кулер.JPG" in os.listdir(str(folder))
    win.grid.set_selection([5])
    win.rename_selected()
    on_disk = os.listdir(str(folder))
    assert "Кулер 1.JPG" in on_disk and "Кулер 2.JPG" in on_disk and "Кулер.JPG" not in on_disk


def test_undo_restores_names(editor):
    win, folder, _ = editor
    before = sorted(os.listdir(str(folder)))
    assert not win.undo_btn.isEnabled()
    pick_name(win, "Вентилятор")
    win.grid.set_selection([0, 1])
    win.rename_selected()
    assert sorted(os.listdir(str(folder))) != before and win.undo_btn.isEnabled()
    win.undo_btn.click()
    assert sorted(os.listdir(str(folder))) == before
    assert win.session.photos == before and not win.undo_btn.isEnabled()
    assert [cell.name for cell in win.grid.cells] == before


def test_sides_are_remembered(editor):
    win, _, names = editor
    pick_name(win, "Вентилятор")
    win.sides_spin.setValue(3)
    win.grid.set_selection([0, 1, 2])
    win.rename_selected()
    assert names.sides_for("Вентилятор") == 3
    assert NamesList(names.path).sides_for("Вентилятор") == 3


def test_sides_field_is_not_cut_at_ten(editor):
    """Окно переименования подставляет и больше десяти, а список категорий у окон общий."""
    win, _, names = editor
    names.remember_sides("Кулер", 12)
    pick_name(win, "Кулер")
    assert win.sides_spin.value() == 12


def test_search_filters_names(editor):
    win, _, _ = editor
    win.search_edit.setText("пам")
    assert [win.names_list.item(k).text() for k in range(win.names_list.count())] == ["Плата оперативной памяти"]
    win.search_edit.clear()
    assert win.names_list.count() == 3


def test_search_keeps_the_chosen_name(editor):
    win, _, _ = editor
    pick_name(win, "Кулер")
    win.search_edit.setText("к")
    assert win.current_name() == "Кулер"


def test_rename_without_category_or_selection_explains(editor):
    win, folder, _ = editor
    before = sorted(os.listdir(str(folder)))
    win.grid.set_selection([0])
    win.rename_selected()
    assert "Выберите категорию" in win.said[0]
    pick_name(win, "Кулер")
    win.grid.clear_selection()
    win.rename_selected()
    assert "Выделите фото" in win.said[1]
    assert sorted(os.listdir(str(folder))) == before


def test_warning_can_stop_the_rename(editor):
    win, folder, _ = editor
    before = sorted(os.listdir(str(folder)))
    win._ask = lambda question: win.asked.append(question) or False
    pick_name(win, "Плата оперативной памяти")     # два ракурса, а выделено три фото
    win.grid.set_selection([0, 1, 2])
    win.rename_selected()
    assert win.asked and "Продолжить" in win.asked[0]
    assert sorted(os.listdir(str(folder))) == before


def test_add_category_without_a_dialog(editor):
    win, _, names = editor
    win.search_edit.setText("Планка индикации")
    win.sides_spin.setValue(2)
    win.add_btn.click()
    assert names.sides_for("Планка индикации") == 2
    assert win.search_edit.text() == "" and win.current_name() == "Планка индикации"
    assert "Планка индикации" in NamesList(names.path).names()


def test_add_category_with_empty_search_only_hints(editor):
    win, _, names = editor
    before = list(names.names())
    win.search_edit.setText("  ")
    win.add_btn.click()
    assert names.names() == before and "Наберите название" in win.status_label.text()


def test_remove_category_asks_first(editor):
    win, _, names = editor
    win.remove_btn.click()
    assert "Выберите категорию" in win.said[0]
    pick_name(win, "Кулер")
    win.remove_btn.click()
    assert "Кулер" in win.asked[0] and "Кулер" not in names.names()
    assert win.names_list.count() == 2


def test_enter_renames_from_search_and_list(editor):
    win, folder, _ = editor
    pick_name(win, "Кулер")
    win.grid.set_selection([0])
    QTest.keyClick(win.search_edit, Qt.Key_Return)
    assert "Кулер.JPG" in os.listdir(str(folder))
    win.grid.set_selection([1])
    QTest.keyClick(win.names_list, Qt.Key_Return)
    assert "Кулер 2.JPG" in os.listdir(str(folder))


def test_double_click_on_a_name_renames(editor):
    win, folder, _ = editor
    pick_name(win, "Кулер")
    win.grid.set_selection([0])
    win.names_list.itemDoubleClicked.emit(win.names_list.currentItem())
    assert "Кулер.JPG" in os.listdir(str(folder))


def test_down_from_search_goes_to_first_name(editor):
    win, _, _ = editor
    win.search_edit.setText("в")
    QTest.keyClick(win.search_edit, Qt.Key_Down)
    assert win.current_name() == win.names_list.item(0).text()


def test_ctrl_s_and_ctrl_z_work(editor):
    """Ctrl+S работает и из поля поиска; Ctrl+Z в поле остаётся за полем, из сетки — отменяет."""
    win, folder, _ = editor
    shortcuts_ready(win)
    before = sorted(os.listdir(str(folder)))
    pick_name(win, "Кулер")
    win.grid.set_selection([0])
    win.search_edit.setFocus()
    QTest.keyClick(win.search_edit, Qt.Key_S, Qt.ControlModifier)
    assert "Кулер.JPG" in os.listdir(str(folder))
    win.search_edit.setText("кул")
    QTest.keyClick(win.search_edit, Qt.Key_Z, Qt.ControlModifier)
    assert "Кулер.JPG" in os.listdir(str(folder))
    win.grid.setFocus()
    QTest.keyClick(win.grid, Qt.Key_Z, Qt.ControlModifier)
    assert sorted(os.listdir(str(folder))) == before


def test_ctrl_a_selects_photos_but_stays_with_the_field(editor):
    win, _, _ = editor
    shortcuts_ready(win)
    win.names_list.setFocus()
    QTest.keyClick(win.names_list, Qt.Key_A, Qt.ControlModifier)
    assert len(win.grid.selection) == 6
    win.grid.clear_selection()
    win.search_edit.setText("кул")
    win.search_edit.setFocus()
    QTest.keyClick(win.search_edit, Qt.Key_A, Qt.ControlModifier)
    assert win.grid.selection == set() and win.search_edit.selectedText() == "кул"


def test_selection_buttons_and_counter(editor):
    win, _, _ = editor
    win.select_all_btn.click()
    assert win.selection_label.text() == "Выделено: 6"
    win.clear_btn.click()
    assert win.selection_label.text() == "Выделено: 0"
    win.grid.set_selection([1, 3])
    assert win.selection_label.text() == "Выделено: 2"
    QTest.keyClick(win.grid, Qt.Key_Escape)
    assert win.selection_label.text() == "Выделено: 0"


def click_cell(grid, row, modifier=Qt.NoModifier):
    """Щелчок как рукой: ячейка сначала прокручивается в окно (при крупном шрифте сетка не помещается)."""
    index = grid.model_.index(row)
    grid.scrollTo(index)
    QTest.mouseClick(grid.viewport(), Qt.LeftButton, modifier, grid.visualRect(index).center())


def test_clicks_select_like_in_explorer(editor):
    win, _, _ = editor
    win.show()
    grid = win.grid
    QApplication.processEvents()
    click_cell(grid, 1)
    assert grid.selection == {1}
    click_cell(grid, 3, Qt.ControlModifier)
    assert grid.selection == {1, 3}
    click_cell(grid, 3, Qt.ControlModifier)
    assert grid.selection == {1}
    click_cell(grid, 3, Qt.ShiftModifier)
    assert grid.selection == {3}                # диапазон заменяет выделенное, как в Проводнике
    click_cell(grid, 1)
    click_cell(grid, 4, Qt.ShiftModifier)
    assert grid.selection == {1, 2, 3, 4}
    click_cell(grid, 2, Qt.ShiftModifier)
    assert grid.selection == {1, 2}             # начало диапазона остаётся на месте
    click_cell(grid, 5, Qt.ControlModifier)
    click_cell(grid, 3, Qt.ControlModifier | Qt.ShiftModifier)
    assert grid.selection == {1, 2, 3, 4, 5}    # Ctrl+Shift добавляет диапазон
    click_cell(grid, 0)
    assert grid.selection == {0}


def test_frame_drawn_with_the_mouse_selects(editor):
    win, _, _ = editor
    win.resize(1200, 700)
    win.show()
    grid = win.grid
    QApplication.processEvents()
    wait_for_thumbnails(win)
    end = grid.visualRect(grid.model_.index(1)).center()
    empty = QPoint(grid.viewport().width() - 3, grid.viewport().height() - 3)
    assert not grid.indexAt(empty).isValid()
    QTest.mousePress(grid.viewport(), Qt.LeftButton, Qt.NoModifier, empty)
    # QTest.mouseMove не сообщает о нажатой кнопке, а рамка без неё не рисуется
    QApplication.sendEvent(grid.viewport(), QMouseEvent(QEvent.MouseMove, QPointF(end), Qt.NoButton, Qt.LeftButton,
                                                        Qt.NoModifier))
    QTest.mouseRelease(grid.viewport(), Qt.LeftButton, Qt.NoModifier, end)
    assert 1 in grid.selection and 0 not in grid.selection


def test_zoom_limits_and_ctrl_wheel(editor):
    win, _, _ = editor
    grid = win.grid
    start = grid.cell_size
    grid.wheelEvent(wheel(Qt.NoModifier))
    assert grid.cell_size == start
    grid.wheelEvent(wheel(Qt.ControlModifier))
    assert grid.cell_size == start + thumbgrid.WHEEL_STEP
    win.larger_btn.click()
    assert grid.cell_size == start + thumbgrid.WHEEL_STEP + qt_editor.ZOOM_STEP
    assert grid.gridSize().width() > grid.cell_size
    grid.set_cell_size(10_000)
    assert grid.cell_size == thumbgrid.MAX_SIZE
    grid.set_cell_size(1)
    assert grid.cell_size == thumbgrid.MIN_SIZE


def test_thumbnails_load_in_background(editor):
    win, _, _ = editor
    wait_for_thumbnails(win)
    assert all(cell.image for cell in win.grid.cells)
    assert "загрузка миниатюр" not in win.status_label.text() and win.progress.value() == 0


def test_cells_are_painted_with_photo_and_name(editor):
    win, _, _ = editor
    wait_for_thumbnails(win)
    win.resize(1200, 700)
    win.show()
    QApplication.processEvents()
    assert not win.grab().isNull()
    assert all(cell.pixmap is not None for cell in win.grid.cells[:3])


def test_unreadable_photo_does_not_break_the_grid(tmp_path, dialogs):
    folder = tmp_path / "SN1"
    shoot(folder, [1, 2])
    (folder / "битое.JPG").write_bytes(b"not a jpeg")
    win, _ = open_editor(tmp_path, folder)
    win.start()
    wait_for_thumbnails(win)
    broken = next(cell for cell in win.grid.cells if cell.name == "битое.JPG")
    assert broken.image is False and sum(1 for cell in win.grid.cells if cell.image) == 2
    win.show()
    QApplication.processEvents()
    win.close()
    win.deleteLater()


def slow_loading(win):
    """Запускает загрузку заново с медленным чтением, как по сети."""
    wait_for_thumbnails(win)
    real = win.grid.open_cache

    class Slow:
        def __init__(self):
            self.cache = real()

        def thumbnail(self, path):
            time.sleep(0.1)
            return self.cache.thumbnail(path)

        def close(self):
            self.cache.close()

    win.grid.open_cache = Slow
    win.grid.set_items(win.session.items(), win.session.template_flags())
    assert win.worker.busy()


def test_window_answers_while_thumbnails_load(editor):
    win, _, _ = editor
    slow_loading(win)
    started = time.perf_counter()
    win.select_all_btn.click()
    assert time.perf_counter() - started < 0.1 and win.selection_label.text() == "Выделено: 6"
    assert win.worker.busy() and win.grid.loaded_count() < 6
    win.worker.cancel()
    win.worker.wait(5)


def test_rename_during_loading_still_shows_every_thumbnail(editor):
    """Задача читает по старым именам: после переименования её надо запустить заново, иначе «нет превью»."""
    win, _, _ = editor
    slow_loading(win)
    pick_name(win, "Кулер")
    win.grid.set_selection([0, 1])
    win.rename_selected()
    assert win.worker.busy()          # загрузка встала на время переименования и пошла по новым именам
    wait_for_thumbnails(win)
    assert not win.grid._restart
    assert all(cell.image for cell in win.grid.cells)
    assert "загрузка миниатюр" not in win.status_label.text()


def test_renamed_photos_are_grouped_but_numbered_by_shooting_order(editor):
    win, _, _ = editor
    shot_order = list(win.session.photos)
    pick_name(win, "Кулер")
    win.grid.set_selection([0, 4])
    win.rename_selected()
    assert win.session.photos[0] == "Кулер 1.JPG" and win.session.photos[4] == "Кулер 2.JPG"
    assert win.session.shown[:2] == ["Кулер 1.JPG", "Кулер 2.JPG"]
    assert win.session.shown[2:] == [shot_order[i] for i in (1, 2, 3, 5)]
    assert [cell.name for cell in win.grid.cells] == win.session.shown
    assert [cell.template for cell in win.grid.cells] == [is_template_name(n) for n in win.session.shown]


def test_second_category_keeps_its_own_instances_together(editor):
    win, _, _ = editor
    pick_name(win, "Вентилятор")
    win.grid.set_selection([1, 5])
    win.rename_selected()
    pick_name(win, "Кулер")
    win.grid.set_selection([2])
    win.rename_selected()
    assert win.session.shown[:3] == ["Вентилятор 1.JPG", "Вентилятор 2.JPG", "Кулер.JPG"]
    assert not any(is_template_name(name) for name in win.session.shown[3:])


def test_reorder_keeps_thumbnails_and_paths(editor):
    win, folder, _ = editor
    wait_for_thumbnails(win)
    images = {cell.path: cell.image for cell in win.grid.cells}
    moved = [images[os.path.join(str(folder), win.session.photos[k])] for k in (0, 4)]
    pick_name(win, "Кулер")
    win.grid.set_selection([0, 4])
    win.rename_selected()
    wait_for_thumbnails(win)
    assert [win.grid.cells[0].image, win.grid.cells[1].image] == moved
    assert all(cell.image for cell in win.grid.cells)
    assert all(os.path.exists(cell.path) for cell in win.grid.cells)


def test_reorder_moves_the_selection_with_the_photos(editor):
    win, _, _ = editor
    win.grid.set_selection([4])
    name = win.grid.selected_names()[0]
    order = [4, 0, 1, 2, 3, 5]
    items = [win.session.items()[k] for k in order]
    win.grid.reorder(order, items)
    assert win.grid.selected_names() == [name] and win.grid.selection == {0}


def test_double_click_opens_the_photo_large(editor, monkeypatch):
    win, folder, _ = editor
    opened = []
    monkeypatch.setattr(qt_editor, "LargeView", lambda *a: opened.append(a))
    win.grid.doubleClicked.emit(win.grid.model_.index(2))
    _, path, names, index = opened[0]
    assert path == str(folder) and names[index] == win.session.shown[2]


def test_named_folder_teaches_categories(tmp_path, dialogs):
    folder = tmp_path / "SN1"
    shoot(folder, [1, 2, 3], names=["Стойка 1 фото 1.JPG", "Стойка 1 фото 2.JPG", "Кабель.JPG"])
    win, names = open_editor(tmp_path, folder)
    win.start()
    assert names.sides_for("Стойка") == 2
    assert win.names_list.findItems("Стойка", Qt.MatchExactly)
    win.close()
    win.deleteLater()


def test_unreadable_folder_is_reported(tmp_path, dialogs):
    win, _ = open_editor(tmp_path, tmp_path / "нет такой папки")
    win.start()
    assert "Не удалось прочитать папку" in dialogs[0] and win.grid.cells == []
    win.close()
    win.deleteLater()


def test_start_is_not_repeated(editor):
    win, _, _ = editor
    win.grid.set_selection([0])
    win.start()
    assert win.grid.selection == {0}


def test_close_stops_loading_and_closes_session(tmp_path, dialogs):
    folder = tmp_path / "SN1"
    shoot(folder, [1, 2, 3])
    win, _ = open_editor(tmp_path, folder)
    done = []
    win.on_done = lambda: done.append(True)
    win.start()
    win.close()
    win.close()
    assert done == [True] and not win.worker.busy()
    win.deleteLater()


def test_long_status_does_not_widen_the_window(editor):
    win, _, _ = editor
    win.status_label.setText("очень длинная надпись " * 60)
    assert win.minimumSizeHint().width() <= win.minimumWidth()


def test_label_keeps_the_end_of_a_long_name():
    font = QFont()
    metrics = QFontMetrics(font)
    assert thumbgrid.label_lines("Кулер.JPG", metrics, font, 160) == "Кулер.JPG"
    width = metrics.horizontalAdvance("Плата оперативной памяти")    # ширина ячейки — по шрифту, а не в точках
    lines = thumbgrid.label_lines("Плата оперативной памяти и ещё много других слов 12 фото 3.JPG", metrics, font, width)
    assert len(lines.split("\n")) == 2 and lines.endswith("3.JPG")


def test_buttons_do_not_take_focus_by_click(editor):
    """Иначе после «Выделить всё» Esc и стрелки дошли бы до кнопки, а не до сетки."""
    win, _, _ = editor
    for button in win.findChildren(QPushButton):
        assert not button.focusPolicy() & Qt.ClickFocus, button.text()


def test_grid_has_focus_when_the_window_opens(tmp_path, dialogs):
    """Пробел и стрелки сразу работают с сеткой, а не с первой кнопкой окна."""
    folder = tmp_path / "эталон"
    shoot(folder, [1, 2])
    win, _ = open_editor(tmp_path, folder)
    assert win.focusWidget() is win.grid
    win.close()
    win.deleteLater()


def test_rename_waits_for_the_photo_being_read(tmp_path, dialogs, monkeypatch):
    """На Windows фото, которое в этот миг читает загрузка миниатюр, переименовать нельзя («файл открыт
    в другой программе»): так в Actions не переименовывалось первое фото. Загрузка встаёт на время
    переименования и потом дочитывает остальное."""
    from photo_renamer.cache import PhotoCache

    folder = tmp_path / "SN1"
    shoot(folder, [1, 2, 3, 4, 5, 6])
    reading = []
    original = PhotoCache.thumbnail

    def slow(self, path):
        reading.append(path)
        time.sleep(0.15)                      # фото с сетевого диска читается долго
        try:
            return original(self, path)
        finally:
            reading.remove(path)

    monkeypatch.setattr(PhotoCache, "thumbnail", slow)
    win, _ = open_editor(tmp_path, folder)
    win.start()
    assert win.worker.busy()
    seen = []
    apply = win.session.apply_rename
    monkeypatch.setattr(win.session, "apply_rename", lambda *a: (seen.append(list(reading)), apply(*a))[1])
    undo = win.session.undo
    monkeypatch.setattr(win.session, "undo", lambda: (seen.append(list(reading)), undo())[1])
    win.grid.set_selection([0])
    pick_name(win, "Кулер")
    win.rename_selected()
    assert "Кулер.JPG" in os.listdir(str(folder))
    win.undo()
    assert "Кулер.JPG" not in os.listdir(str(folder))
    assert seen == [[], []]                   # в миг переименования и отмены ничего не читалось
    wait_for_thumbnails(win)
    assert win.grid.loaded_count() == 6       # остальное дочитано
    win.close()
