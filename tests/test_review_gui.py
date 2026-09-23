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
    saved = []          # настройки пользователя тесты не трогают
    monkeypatch.setattr(review_mod.cfg, "save_config", lambda cfg_, path=None: saved.append(cfg_.review_thumb))

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


def test_bottom_row_stays_visible_on_small_screen(batch):
    """Нижняя строка с состоянием и кнопками не должна вытесняться панелью справа."""
    win, _ = batch
    win.geometry("1024x700")
    win.thumb_size = review_mod.MAX_THUMB
    win._apply_thumb_size()
    win.render_rows()
    win.update()
    height = win.winfo_height()
    for button in (win.undo_btn, win.skip_btn, win.apply_btn, win.close_btn):
        name = button.cget("text")
        # вытесненная кнопка сжимается в полоску высотой в пиксель и уезжает наверх
        assert button.winfo_height() > 10, "кнопка сжата: %s" % name
        bottom = button.winfo_rooty() - win.winfo_rooty() + button.winfo_height()
        assert height - 60 <= bottom <= height, "кнопка не внизу окна: %s" % name


def test_marked_photos_are_kept_and_the_rest_are_renamed(batch, monkeypatch):
    """«Не переименовывать» — осознанный выбор: спрашивать про такие фото не нужно,
    а остальные переименовываются, даже если программа в них не уверена."""
    win, root_dir = batch
    from photo_renamer import matcher

    kept = [win.match.photos[0].filename, win.match.photos[1].filename]
    matcher.set_category(win.match, 0, None)
    matcher.set_category(win.match, 1, None)
    win.render_rows()

    questions = []
    monkeypatch.setattr(review_mod.messagebox, "askyesno",
                        lambda title, text, **k: questions.append(text) or True)
    win.apply_folder()
    assert questions and "Не опознано фото" not in questions[0]
    after = set(os.listdir(str(root_dir / "SN2")))
    assert set(kept) <= after                       # помеченные остались с прежними именами
    assert any(not name.startswith("IMG_") for name in after)   # остальные переименованы


def test_sorting_by_column_headers(batch):
    """Сортировка щелчком по заголовку: первый щелчок — по возрастанию, второй — наоборот."""
    win, _ = batch
    order = [p.filename for p in win.match.photos]
    assert [r[0] for r in rows(win)] == order          # по умолчанию — порядок съёмки

    win.sort_by("#0")
    by_name = [r[0] for r in rows(win)]
    assert by_name == sorted(order)
    assert "↑" in win.tree.heading("#0", "text")

    win.sort_by("#0")                                   # повторный щелчок — обратный порядок
    assert [r[0] for r in rows(win)] == sorted(order, reverse=True)
    assert "↓" in win.tree.heading("#0", "text")

    win.sort_by("confidence")
    values = [float(r[1].rstrip("%") or 0) for r in
              [(win.tree.item(iid, "text").strip(), win.tree.item(iid, "values")[1]) for iid in win.tree.get_children()]]
    assert values == sorted(values)


def test_sorting_by_status_groups_doubtful_together(batch):
    """Столбец «Состояние» заменяет прежние галочки: сомнительные собираются рядом."""
    win, _ = batch
    from photo_renamer import matcher

    win.match.photos[3].status = matcher.STATUS_CHECK
    win.sort_by("status")
    states = [win.tree.item(iid, "values")[2] for iid in win.tree.get_children()]
    assert states == sorted(states)


def test_only_two_candidates_are_numbered(batch):
    """Клавиши 1 и 2 — кандидаты, 3 — отказ; третьей кнопки-кандидата быть не должно."""
    win, _ = batch
    select_row(win, 0)
    texts = [w.cget("text") for w in win.candidates_frame.winfo_children() if w.cget("text")]
    numbered = [t for t in texts if t.startswith(("1 —", "2 —", "3 —"))]
    assert numbered[-1] == "3 — не переименовывать"
    assert len([t for t in texts if "%" in t]) <= 2
