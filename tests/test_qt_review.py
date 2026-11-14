"""Окно переименования на PyQt5: те же действия и клавиши, что у окна на tkinter (логика у них общая)."""

import os
import sys

import pytest

pytest.importorskip("PyQt5.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QKeyEvent, QWheelEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from photo_renamer import config as cfg, grouping, review_session  # noqa: E402
from photo_renamer.gui_qt import photoview, review as qt_review, theme as qt_theme  # noqa: E402
from photo_renamer.scanner import list_photos  # noqa: E402
from test_grouping import shoot  # noqa: E402

REF_NAMES = ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "Кабель HDMI.JPG",
             "Радиатор 1.JPG", "Радиатор 2.JPG"]
PARTS = [1, 2, 3, 4, 5, 6]

_app = QApplication.instance() or QApplication([sys.argv[0]])


def make_config(tmp_path):
    config = cfg.AppConfig()
    config.cache_dir = str(tmp_path / "cache")
    config.logs_dir = str(tmp_path / "logs")
    config.names_file = str(tmp_path / "names.txt")
    return config


def make_group(root, targets=("SN2",)):
    shoot(root / "эталон", PARTS, names=REF_NAMES)
    for name in targets:
        shoot(root / name, PARTS)
    infos = [grouping.folder_info(list_photos(str(root / name))) for name in ("эталон",) + tuple(targets)]
    return grouping.FolderGroup(infos, infos[0], True)


@pytest.fixture
def dialogs(monkeypatch):
    said = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda parent, title, text: said.append(text))
    monkeypatch.setattr(cfg, "save_config", lambda *a, **k: None)
    return said


def open_window(tmp_path, group):
    qt_theme_used = qt_theme.apply(_app, "light", str(tmp_path / "theme"))
    win = qt_review.BatchWindow(None, group, make_config(tmp_path), qt_theme_used)
    win.setAttribute(Qt.WA_DeleteOnClose, False)    # окно нужно тестам и после закрытия
    return win


@pytest.fixture
def batch(tmp_path, dialogs):
    root = tmp_path / "Проект"
    win = open_window(tmp_path, make_group(root))
    win.said = dialogs
    win.asked = []
    win._ask = lambda question: win.asked.append(question) or True
    win.start()
    yield win, root
    win.close()
    win.deleteLater()


def rows(win):
    return [win.tree.topLevelItem(k) for k in range(win.tree.topLevelItemCount())]


def row_of(win, filename):
    return next(item for item in rows(win) if item.text(0) == filename)


def select(win, *positions):
    win.tree.clearSelection()
    for position in positions:
        win.tree.topLevelItem(position).setSelected(True)
    win._on_row_select()


def candidate_texts(win):
    return [win.candidates.itemAt(k).widget().text() for k in range(win.candidates.count())]


def candidate_buttons(win):
    widgets = [win.candidates.itemAt(k).widget() for k in range(win.candidates.count())]
    return [w for w in widgets if isinstance(w, QPushButton)]


def category_row(win, name):
    return next(k for k in range(win.categories_list.count()) if win.categories_list.item(k).text() == name)


def choose_category(win, name):
    win.categories_list.setCurrentRow(category_row(win, name))


def load_all_thumbnails(win):
    while win._thumb_queue:
        win._load_next_thumbnail()


def test_preview_is_built(batch):
    win, _ = batch
    assert win.session.model is not None and win.categories_list.count() == 4
    assert win.tree.topLevelItemCount() == 6
    assert win.session.header_text() == win.header_label.full_text
    assert win.status_label.text().startswith("Фото: 6")
    assert win.tree.selectedItems() == [win.tree.topLevelItem(0)]   # новая папка — с первой строки


def test_rows_show_what_the_session_says(batch):
    win, _ = batch
    for item, photo in zip(rows(win), win.session.display_photos()):
        assert [item.text(k) for k in range(4)] == [photo.filename] + list(review_session.row_values(photo))


def test_candidates_are_numbered_and_last_is_refusal(batch):
    win, _ = batch
    select(win, 0)
    texts = candidate_texts(win)
    assert texts[0] in ("Похоже ещё на:", "Других вариантов нет")
    assert texts[-1] == "%d — Не переименовывать" % (review_session.ALTERNATIVES + 1)
    select(win, 0, 1)
    assert candidate_texts(win) == ["Выбрано фото: 2"]


def test_candidate_buttons_do_not_take_the_focus(batch):
    win, _ = batch
    select(win, 0)
    assert all(button.focusPolicy() == Qt.NoFocus for button in candidate_buttons(win))


def test_digit_applies_candidate_and_moves_down(batch):
    win, _ = batch
    select(win, 0)
    filename = rows(win)[0].text(0)
    wanted = win.session.alternatives(win.session.match.photos[win._selected_indexes()[0]])[0].category
    QTest.keyClick(win.tree, Qt.Key_1)
    photo = next(p for p in win.session.match.photos if p.filename == filename)
    assert photo.category.name == wanted
    assert win.tree.selectedItems() == [win.tree.topLevelItem(1)]


@pytest.mark.parametrize("key", [Qt.Key_4, Qt.Key_0, Qt.Key_Delete, Qt.Key_Backspace])
def test_keys_exclude_a_photo(batch, key):
    win, _ = batch
    select(win, 0)
    filename = rows(win)[0].text(0)
    QTest.keyClick(win.tree, key)
    assert row_of(win, filename).text(1) == "— не переименовывать —"


def test_enter_confirms_and_moves_down(batch):
    win, _ = batch
    select(win, 0)
    filename = rows(win)[0].text(0)
    QTest.keyClick(win.tree, Qt.Key_Return)
    assert row_of(win, filename).text(3) == "выбрано вручную"
    assert win.tree.selectedItems() == [win.tree.topLevelItem(1)]


def test_last_row_stays_selected_after_edit(batch):
    win, _ = batch
    select(win, 5)
    QTest.keyClick(win.tree, Qt.Key_Return)
    assert win.tree.selectedItems() == [win.tree.topLevelItem(5)]
    assert candidate_texts(win)


def test_digit_with_modifier_is_not_a_choice(batch):
    win, _ = batch
    select(win, 0)
    before = dict(win.session.match.manual)
    QTest.keyClick(win.tree, Qt.Key_1, Qt.AltModifier)
    assert win.session.match.manual == before


def test_undo_returns_the_edit_and_selects_the_photo(batch):
    win, _ = batch
    select(win, 0)
    filename = rows(win)[0].text(0)
    was = row_of(win, filename).text(1)
    QTest.keyClick(win.tree, Qt.Key_0)
    win.undo_edit_btn.click()
    assert row_of(win, filename).text(1) == was
    assert win.tree.selectedItems() == [row_of(win, filename)]


def test_undo_without_edits_says_so(batch):
    win, _ = batch
    win.undo_edit()
    assert win.status_label.text() == "Отменять нечего"


def test_shortcuts_work_from_the_category_field(batch):
    """Ctrl+Z в поле ввода отменяет правку фото, а не набранный текст; Ctrl+S — переименовывает."""
    win, root = batch
    win.show()
    win.activateWindow()
    QTest.qWaitForWindowActive(win)
    if QApplication.activeWindow() is not win:
        pytest.skip("окно не стало активным")
    select(win, 0)
    filename = rows(win)[0].text(0)
    was = row_of(win, filename).text(1)
    QTest.keyClick(win.tree, Qt.Key_0)
    win.search_edit.setFocus()
    QTest.keyClick(win.search_edit, Qt.Key_Z, Qt.ControlModifier)
    assert row_of(win, filename).text(1) == was
    before = sorted(os.listdir(str(root / "SN2")))
    QTest.keyClick(win.search_edit, Qt.Key_S, Qt.ControlModifier)
    assert sorted(os.listdir(str(root / "SN2"))) != before


def test_sorting_by_column_headers(batch):
    win, _ = batch
    win.tree.header().sectionClicked.emit(0)
    names = [item.text(0) for item in rows(win)]
    assert names == sorted(names)
    assert win.tree.headerItem().text(0) == "Фото ↑" and win.tree.headerItem().text(1) == "Новое имя"
    win.tree.header().sectionClicked.emit(0)
    assert [item.text(0) for item in rows(win)] == names[::-1]
    assert win.tree.headerItem().text(0) == "Фото ↓"


def test_row_order_does_not_change_after_edits(batch):
    win, _ = batch
    names = [item.text(0) for item in rows(win)]
    select(win, 0)
    QTest.keyClick(win.tree, Qt.Key_0)
    assert [item.text(0) for item in rows(win)] == names


def test_rows_are_colored_by_kind(batch):
    win, _ = batch
    select(win, 0)
    filename = rows(win)[0].text(0)
    QTest.keyClick(win.tree, Qt.Key_0)
    assert row_of(win, filename).foreground(1).color().name() == qt_theme.LIGHT["text_muted"]


def test_set_theme_repaints_rows(batch, tmp_path):
    win, _ = batch
    select(win, 0)
    filename = rows(win)[0].text(0)
    QTest.keyClick(win.tree, Qt.Key_0)
    win.set_theme(qt_theme.apply(_app, "dark", str(tmp_path / "theme")))
    assert row_of(win, filename).foreground(1).color().name() == qt_theme.DARK["text_muted"]
    qt_theme.apply(_app, "light", str(tmp_path / "theme"))


def test_thumbnails_are_loaded(batch):
    win, _ = batch
    load_all_thumbnails(win)
    assert all(not item.icon(0).isNull() for item in rows(win))
    assert not win._thumb_queue


def test_photo_size_changes_and_is_clamped(batch):
    win, _ = batch
    start = win.thumb_size
    win.larger_btn.click()
    assert win.thumb_size == start + review_session.THUMB_STEP
    assert win.app_config.review_thumb == win.thumb_size
    assert win.tree.iconSize().width() == win.thumb_size
    assert rows(win)[0].sizeHint(0).height() == win.thumb_size + qt_theme.SPACE["sm"]
    win.set_thumb_size(10_000)
    assert win.thumb_size == review_session.MAX_THUMB
    win.set_thumb_size(1)
    assert win.thumb_size == review_session.MIN_THUMB


def wheel(modifiers):
    return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, modifiers,
                       Qt.NoScrollPhase, False)


def test_ctrl_wheel_changes_photo_size(batch):
    win, _ = batch
    start = win.thumb_size
    win.tree.wheelEvent(wheel(Qt.NoModifier))
    assert win.thumb_size == start
    win.tree.wheelEvent(wheel(Qt.ControlModifier))
    assert win.thumb_size == start + review_session.THUMB_STEP


def test_apply_and_undo_folder(batch):
    win, root = batch
    folder = root / "SN2"
    before = sorted(os.listdir(str(folder)))
    win.apply_btn.click()
    after = sorted(os.listdir(str(folder)))
    assert after != before and any(name.startswith(("Кулер", "Процессор")) for name in after)
    assert "Переименовано папок: 1" in win.status_label.text() and win.header_label.full_text == "Готово"
    assert win.undo_btn.isEnabled() and not win.apply_btn.isEnabled() and not win.skip_btn.isEnabled()
    assert win.tree.topLevelItemCount() == 0
    win.undo_btn.click()
    assert sorted(os.listdir(str(folder))) == before
    assert not win.undo_btn.isEnabled() and "отменено" in win.status_label.text()


def test_excluded_photo_keeps_its_name(batch):
    win, root = batch
    select(win, 0)
    filename = rows(win)[0].text(0)
    QTest.keyClick(win.tree, Qt.Key_0)
    win.apply_folder()
    assert filename in os.listdir(str(root / "SN2"))


def test_edits_are_ignored_after_the_last_folder(batch):
    win, _ = batch
    win.skip_folder()
    win.undo_edit()
    win.apply_folder()
    assert win.tree.topLevelItemCount() == 0 and win.header_label.full_text == "Готово"


def test_unrecognised_photos_ask_and_no_stops_renaming(batch):
    win, root = batch
    select(win, 0)
    win.session.match.manual.clear()
    win.session.unresolved_count = lambda: 2
    win._ask = lambda question: win.asked.append(question) or False
    before = sorted(os.listdir(str(root / "SN2")))
    win.apply_folder()
    assert "Не опознано фото: 2" in win.asked[0]
    assert sorted(os.listdir(str(root / "SN2"))) == before and win.header_label.full_text != "Готово"


def test_nothing_is_asked_when_everything_is_sorted_out(batch):
    win, _ = batch
    win.session.unresolved_count = lambda: 0
    win.apply_folder()
    assert win.asked == []


def test_add_and_remove_category(batch):
    win, _ = batch
    win.search_edit.setText("Вентилятор")
    assert win.categories_list.count() == 0
    win.add_btn.click()
    assert win.search_edit.text() == "" and win._current_category() == "Вентилятор"
    assert "Вентилятор" in win.session.names.names()
    win.remove_btn.click()
    assert "Вентилятор" not in win.session.names.names()
    assert win.categories_list.count() == 4


def test_add_category_with_empty_field_only_hints(batch):
    win, _ = batch
    win.add_btn.click()
    assert "Наберите название" in win.status_label.text() and win.said == []


def test_reference_category_is_not_removed(batch):
    win, _ = batch
    choose_category(win, "Кулер")
    win.remove_btn.click()
    assert "категория эталона" in win.said[0] and win.categories_list.count() == 4


def test_search_filters_categories(batch):
    win, _ = batch
    win.search_edit.setText("кул")
    assert win._category_names() == ["Кулер"]


def test_apply_category_from_the_list(batch):
    win, _ = batch
    select(win, 0)
    filename = rows(win)[0].text(0)
    choose_category(win, "Кулер")
    win.apply_category_btn.click()
    assert row_of(win, filename).text(3) == "выбрано вручную"
    assert row_of(win, filename).text(1).startswith("Кулер")


def test_apply_without_choice_explains(batch):
    win, _ = batch
    win.apply_selected_category()
    assert "Выберите категорию" in win.said[0]
    win.tree.clearSelection()
    choose_category(win, "Кулер")
    win.apply_selected_category()
    assert "Выберите фото" in win.said[1]


def test_sides_field_follows_the_category(batch):
    win, _ = batch
    select(win, 0, 1)
    choose_category(win, "Процессор")
    assert win.sides_spin.value() == 2 and not win.sides_spin.isEnabled()
    win.search_edit.setText("Стойка")
    win.add_btn.click()
    assert win.sides_spin.isEnabled() and win.sides_spin.value() == 2


def test_sides_field_is_not_cut_at_ten(batch):
    """Подсказка равна числу выделенных фото: если их больше десяти, обрезать её нельзя."""
    win, _ = batch
    win.sides_spin.setValue(12)
    assert win.sides_spin.value() == 12


def test_sides_set_by_hand_win(batch):
    win, _ = batch
    select(win, 0, 1)
    win.search_edit.setText("Стойка")
    win.add_btn.click()
    win.sides_spin.setValue(5)
    win.apply_selected_category()
    assert win.session.names.sides_for("Стойка") == 5


def test_side_panel_fits_the_longest_name(batch):
    win, _ = batch
    assert win.side.width() == qt_review.MIN_SIDE_WIDTH
    win.search_edit.setText("Панель USB, VGA, кнопок и индикации, очень длинное название детали")
    win.add_btn.click()
    assert qt_review.MIN_SIDE_WIDTH < win.side.width() <= qt_review.MAX_SIDE_WIDTH


def test_double_click_opens_the_photo_large(batch, monkeypatch):
    win, _ = batch
    opened = []
    monkeypatch.setattr(qt_review, "LargeView", lambda *a: opened.append(a))
    item = win.tree.topLevelItem(2)
    win.tree.itemDoubleClicked.emit(item, 1)
    _, folder, names, index = opened[0]
    assert folder == win.session.folder.path and names[index] == item.text(0)


def test_long_path_does_not_widen_the_window(batch):
    win, _ = batch
    win.header_label.set_full_text("Папка: " + "/очень длинный путь" * 40)
    win.status_label.setText("Лог: " + "/очень_длинный_путь" * 40)
    assert win.minimumSizeHint().width() <= win.minimumWidth()


def test_next_folder_shows_new_thumbnails_not_old_ones(tmp_path, dialogs):
    """Имена файлов в папках группы совпадают: миниатюра прошлой папки здесь была бы ошибкой."""
    win = open_window(tmp_path, make_group(tmp_path / "Проект", ("SN2", "SN3")))
    win.start()
    load_all_thumbnails(win)
    assert win._pixmaps
    win.skip_folder()
    assert not win._pixmaps and len(win._thumb_queue) == 6
    assert win.session.folder.path.endswith("SN3")
    assert win.tree.selectedItems() == [win.tree.topLevelItem(0)]
    win.close()
    win.deleteLater()


def test_close_while_reading_cancels_and_closes(tmp_path, dialogs):
    win = open_window(tmp_path, make_group(tmp_path / "Проект"))
    win.show()
    answers = []
    original = win.session.load_model

    def load(progress):
        def close_on_progress(done, total):
            answers.append(win.close())
            progress(done, total)
        original(close_on_progress)

    win.session.load_model = load
    win.start()
    assert answers[0] is False and win._closed and not win.isVisible()
    win.deleteLater()


def test_controls_are_off_while_reading(tmp_path, dialogs):
    win = open_window(tmp_path, make_group(tmp_path / "Проект"))
    seen = []
    original = win.session.load_model

    def load(progress):
        seen.append((win.tree.isEnabled(), win.apply_btn.isEnabled(), win.close_btn.isEnabled()))
        original(progress)

    win.session.load_model = load
    win.start()
    assert seen == [(False, False, True)]
    assert win.apply_btn.isEnabled() and win.tree.isEnabled()
    win.close()
    win.deleteLater()


def test_unreadable_reference_closes_with_a_message(tmp_path, dialogs):
    win = open_window(tmp_path, make_group(tmp_path / "Проект"))

    def fail(progress):
        raise OSError("диск отключён")

    win.session.load_model = fail
    win.start()
    assert "диск отключён" in dialogs[0] and win._closed
    win.deleteLater()


def test_unavailable_folder_is_skipped(tmp_path, dialogs):
    win = open_window(tmp_path, make_group(tmp_path / "Проект", ("SN2", "SN3")))
    original = win.session.match_current
    calls = []

    def flaky(progress):
        calls.append(win.session.folder.path)
        if len(calls) == 1:
            raise OSError("нет доступа")
        original(progress)

    win.session.match_current = flaky
    win.start()
    assert "нет доступа" in dialogs[0] and win.session.folder.path.endswith("SN3")
    assert win.tree.topLevelItemCount() == 6
    win.close()
    win.deleteLater()


def test_to_pixmap_keeps_screen_size_on_dense_screens():
    image = Image.new("L", (640, 320))
    assert photoview.to_pixmap(image).width() == 640
    assert photoview.to_pixmap(image, 96).width() == 96
    dense = photoview.to_pixmap(image, 96, 2.0)
    assert dense.width() == 192 and dense.devicePixelRatio() == 2.0
    # пикселей на двойную плотность не хватает: на экране фото всё равно должно быть своего размера
    assert photoview.to_pixmap(image, 480, 2.0).width() / photoview.to_pixmap(image, 480, 2.0).devicePixelRatio() == 480


def test_large_view_steps_and_closes(tmp_path):
    folder = tmp_path / "фото"
    shoot(folder, [1, 2], names=["А.JPG", "Б.JPG"])
    view = photoview.LargeView(None, str(folder), ["А.JPG", "Б.JPG"], 0)
    view.setAttribute(Qt.WA_DeleteOnClose, False)
    view.show_photo()
    assert view.caption.text() == "А.JPG   (1 из 2)" and view.label.pixmap() is not None
    QTest.keyClick(view, Qt.Key_Right)
    assert view.caption.text() == "Б.JPG   (2 из 2)" and view.windowTitle() == "Б.JPG"
    QTest.keyClick(view, Qt.Key_Right)
    assert view.index == 0                 # листается по кругу
    QTest.keyClick(view, Qt.Key_Left)
    assert view.index == 1
    QTest.keyClick(view, Qt.Key_Escape)
    assert not view.isVisible()
    view.deleteLater()


def test_large_view_reports_unreadable_file(tmp_path):
    view = photoview.LargeView(None, str(tmp_path), ["нет.JPG"], 0)
    view.setAttribute(Qt.WA_DeleteOnClose, False)
    view.show_photo()
    assert view.caption.text().startswith("Не удалось открыть")
    view.close()
    view.deleteLater()


def test_undo_stack_does_not_cross_folders(tmp_path, dialogs):
    """Правка прошлой папки не должна отменяться в следующей: номера фото там значат другое."""
    win = open_window(tmp_path, make_group(tmp_path / "Проект", ("SN2", "SN3")))
    win.start()
    select(win, 0)
    QTest.keyClick(win.tree, Qt.Key_Return)
    win.skip_folder()
    win.undo_edit()
    assert win.status_label.text() == "Отменять нечего"
    win.close()
    win.deleteLater()


def test_reading_next_folder_stops_loading_thumbnails(tmp_path, dialogs):
    """На сетевом диске миниатюры прошлой папки ещё грузятся, когда её уже переименовали:
    их имена искались бы в новой папке, а чтение шло бы вперемешку с чтением фото."""
    win = open_window(tmp_path, make_group(tmp_path / "Проект", targets=("SN2", "SN3")))
    win.start()
    win.load_thumbnails()
    assert win._thumb_timer.isActive()
    during_read = []
    original = win.session.match_current

    def match_current(progress):
        during_read.append((win._thumb_timer.isActive(), list(win._thumb_queue), win.larger_btn.isEnabled()))
        original(progress)

    win.session.match_current = match_current
    win.skip_folder()
    assert during_read == [(False, [], False)]
    assert win.larger_btn.isEnabled()
    assert win._thumb_timer.isActive()        # у новой папки миниатюры грузятся заново
    win.close()
    win.deleteLater()


def test_buttons_leave_focus_in_the_table(batch):
    """На Windows кнопка забирает фокус щелчком, и цифры с Enter переставали бы работать в таблице."""
    win, _ = batch
    for button in win.findChildren(QPushButton):
        assert not button.focusPolicy() & Qt.ClickFocus, button.text()


def test_large_view_key_during_reading_does_not_mix_up_photos(tmp_path, monkeypatch):
    """Стрелка, нажатая, пока читается прошлое фото: подпись и картинка не должны разойтись."""
    folder = tmp_path / "папка"
    shoot(folder, [1, 2, 3], names=["А.JPG", "Б.JPG", "В.JPG"])
    view = photoview.LargeView(None, str(folder), ["А.JPG", "Б.JPG", "В.JPG"], 0)
    QTest.qWait(20)
    read = []
    original = photoview.feat.load_image

    def load_image(path, size):
        read.append(os.path.basename(path))
        return original(path, size)

    monkeypatch.setattr(photoview.feat, "load_image", load_image)
    QApplication.postEvent(view, QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
    view.step(1)                              # читает «Б», очередная стрелка ждёт в очереди
    assert read == ["Б.JPG"]
    QApplication.processEvents()              # стрелка обрабатывается после чтения: следующий кадр
    assert read == ["Б.JPG", "В.JPG"]
    assert view.caption.text().startswith("В.JPG") and view._loaded == view.index == 2
    view.close()
