"""Экран пакетного переименования: предпросмотр, ручная правка, переименование, отмена."""

import os

import pytest

tk = pytest.importorskip("tkinter")

from photo_renamer import config as cfg, grouping  # noqa: E402
from photo_renamer.gui import review as review_mod  # noqa: E402
from photo_renamer.scanner import list_photos  # noqa: E402
from test_grouping import shoot  # noqa: E402

REF_NAMES = ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "Кабель HDMI.JPG",
             "Радиатор 1.JPG", "Радиатор 2.JPG"]
PARTS = [1, 2, 3, 4, 5, 6]


@pytest.fixture
def batch(tmp_path, monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(review_mod.messagebox, name, lambda *a, **k: None)
    monkeypatch.setattr(review_mod.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(review_mod, "ReportDialog", lambda *a, **k: None)

    root_dir = tmp_path / "Проект"
    shoot(root_dir / "эталон", PARTS, names=REF_NAMES)
    shoot(root_dir / "SN2", PARTS)
    infos = [grouping.folder_info(list_photos(str(root_dir / name))) for name in ("эталон", "SN2")]
    group = grouping.FolderGroup(infos, infos[0], True)

    config = cfg.AppConfig()
    config.cache_dir = str(tmp_path / "cache")
    config.logs_dir = str(tmp_path / "logs")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("нет графической среды: %s" % exc)
    root.withdraw()
    win = review_mod.BatchWindow(root, group, config)
    win.withdraw()
    win.start()
    yield win, root_dir
    win.log.close()
    win.cache.close()
    win.destroy()
    root.destroy()


def rows(win):
    return [(win.tree.item(iid, "text").strip(), win.tree.item(iid, "values")[0]) for iid in win.tree.get_children()]


def row_of(win, filename):
    """Строка таблицы по имени файла (порядок строк зависит от сортировки «сомнительные первыми»)."""
    for iid in win.tree.get_children():
        if win.tree.item(iid, "text").strip() == filename:
            return win.tree.item(iid, "values")
    raise AssertionError(filename)


def test_preview_is_built(batch):
    win, root_dir = batch
    assert win.model is not None and len(win.categories) == 4
    assert "Папка 1 из 1" in win.header_var.get()
    assert len(rows(win)) == 6
    assert "Фото: 6" in win.status_var.get()
    assert sorted(os.listdir(str(root_dir / "SN2")))[0].startswith("IMG_")  # ничего не переименовано


def select_row(win, position):
    iid = win.tree.get_children()[position]
    win.tree.selection_set(iid)
    win._on_row_select()
    return iid


def test_manual_category_and_exclude(batch):
    win, _ = batch
    first = win.tree.item(win.tree.get_children()[0], "text").strip()
    select_row(win, 0)
    win.apply_category("Кабель HDMI")
    assert row_of(win, first)[0] == "Кабель HDMI.JPG"
    assert row_of(win, first)[2] == "выбрано вручную"

    second = win.tree.item(win.tree.get_children()[0], "text").strip()
    select_row(win, 0)
    win.exclude_selected()
    assert row_of(win, second)[0] == "— не переименовывать —"


def test_candidate_buttons_shown(batch):
    win, _ = batch
    select_row(win, 0)
    texts = [w.cget("text") for w in win.candidates_frame.winfo_children()]
    assert any("%" in t for t in texts)


def test_apply_and_undo(batch):
    win, root_dir = batch
    folder = root_dir / "SN2"
    before = sorted(os.listdir(str(folder)))
    win.apply_folder()
    after = sorted(os.listdir(str(folder)))
    assert after != before
    assert any(name.startswith("Кулер") or name.startswith("Процессор") for name in after)
    assert "Переименовано:" in win.status_var.get()
    assert str(win.undo_btn["state"]) == "normal"

    win.undo_folder()
    assert sorted(os.listdir(str(folder))) == before
    assert str(win.undo_btn["state"]) == "disabled"


def test_log_written(batch):
    win, root_dir = batch
    win.apply_folder()
    from photo_renamer import oplog

    rows_log = oplog.read_log(win.log.csv_path)
    assert rows_log and all(r["Папка"] == str(root_dir / "SN2") for r in rows_log)
    assert any(r["Действие"] == oplog.ACTION_RENAME and r["Результат"] == oplog.RESULT_OK for r in rows_log)
    with open(win.log.txt_path, encoding="utf-8-sig") as f:
        assert "Распознано" in f.read()


def test_skip_folder_finishes(batch):
    win, _ = batch
    win.skip_folder()
    assert win.header_var.get() == "Готово"
    assert str(win.apply_btn["state"]) == "disabled"


def test_only_doubtful_filter(batch):
    win, _ = batch
    from photo_renamer import matcher

    total = len(win.tree.get_children())
    win.match.photos[2].status = matcher.STATUS_CHECK      # программа не уверена в имени
    win.render_rows()
    assert len(win.tree.get_children()) == total           # фильтр выключен — видно всё
    assert "проверить: 1" in win.status_var.get()

    win.only_doubtful.set(True)
    win.render_rows()
    rows_left = win.tree.get_children()
    assert len(rows_left) == 1
    assert win.tree.item(rows_left[0], "values")[2] == "ПРОВЕРЬТЕ"

    win.only_doubtful.set(False)
    win.render_rows()
    assert len(win.tree.get_children()) == total


def test_doubtful_rows_come_first(batch):
    win, _ = batch
    from photo_renamer import matcher

    doubtful = win.match.photos[3]
    doubtful.status = matcher.STATUS_CHECK
    win.render_rows()
    assert win.tree.item(win.tree.get_children()[0], "text").strip() == doubtful.filename

    win.sort_doubtful.set(False)          # выключаем — возвращается порядок съёмки
    win.render_rows()
    order = [win.tree.item(iid, "text").strip() for iid in win.tree.get_children()]
    assert order == [p.filename for p in win.match.photos]


def test_candidate_hotkeys(batch):
    win, _ = batch
    iid = win.tree.get_children()[0]
    win.tree.selection_set(iid)
    win._on_row_select()
    filename = win.tree.item(iid, "text").strip()
    photo = next(p for p in win.match.photos if p.filename == filename)
    second = photo.candidates[1].category

    next_filename = win.tree.item(win.tree.get_children()[1], "text").strip()
    win.apply_candidate(2)                 # клавиша «2» — второй кандидат
    assert row_of(win, filename)[0].startswith(second)
    assert row_of(win, filename)[2] == "выбрано вручную"
    # выделение перешло к следующей по таблице строке (той, что была под исправленной)
    assert win.tree.selection()
    assert win.tree.item(win.tree.selection()[0], "text").strip() == next_filename


def test_hotkey_without_such_candidate_does_nothing(batch):
    win, _ = batch
    select_row(win, 0)
    before = rows(win)
    win.apply_candidate(3)
    photo = win.match.photos[0]
    if len(photo.candidates) < 3:
        assert rows(win) == before


def all_labels(widget):
    """Тексты всех подписей внутри виджета, на любой глубине."""
    texts = []
    for child in widget.winfo_children():
        try:
            texts.append(child.cget("text"))
        except Exception:
            pass
        texts.extend(all_labels(child))
    return texts


def test_neighbours_are_shown_for_selected_photo(batch):
    win, _ = batch
    win.sort_doubtful.set(False)          # порядок съёмки
    win.render_rows()
    select_row(win, 2)
    selected = win.match.photos[2]
    assert (selected.new_name or selected.filename) in win.preview_caption.cget("text")
    texts = all_labels(win.neighbours_frame)
    assert any(t.startswith("до: ") for t in texts)
    assert any(t.startswith("после: ") for t in texts)
    previous = win.match.photos[1]
    assert any((previous.new_name or previous.filename) in t for t in texts)


def test_photo_size_is_adjustable_and_remembered(batch):
    """Крупные фото: размер меняется кнопками и Ctrl+колесом, не выходит за границы и запоминается."""
    win, _ = batch
    start = win.thumb_size
    win.set_thumb_size(start + review_mod.THUMB_STEP)
    assert win.thumb_size == start + review_mod.THUMB_STEP
    assert win.app_config.review_thumb == win.thumb_size       # сохранится в настройках при закрытии
    assert int(win.style.lookup("Review.Treeview", "rowheight")) == win.thumb_size + 8

    win.set_thumb_size(10_000)
    assert win.thumb_size == review_mod.MAX_THUMB
    win.set_thumb_size(1)
    assert win.thumb_size == review_mod.MIN_THUMB


def test_preview_is_empty_without_single_selection(batch):
    win, _ = batch
    win.render_rows()
    win.tree.selection_set(win.tree.get_children()[:2])       # выбрано два фото сразу
    win._on_row_select()
    assert win.preview_caption.cget("text") == ""


def test_warning_before_renaming_with_unresolved_photos(batch, monkeypatch):
    win, root_dir = batch
    from photo_renamer import matcher

    matcher.set_category(win.match, 0, None)      # два фото оставлены без имени
    matcher.set_category(win.match, 1, None)
    win.render_rows()
    assert len(win.match.unidentified()) >= 2

    questions = []
    monkeypatch.setattr(review_mod.messagebox, "askyesno",
                        lambda title, text, **k: questions.append(text) or False)
    win.apply_folder()
    assert questions and "Не опознано фото" in questions[0]
    assert sorted(os.listdir(str(root_dir / "SN2")))[0].startswith("IMG_")   # отменили — ничего не менялось
