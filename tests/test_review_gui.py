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
    select_row(win, 0)
    win.apply_category("Кабель HDMI")
    assert rows(win)[0][1] == "Кабель HDMI.JPG"
    assert win.tree.item(win.tree.get_children()[0], "values")[2] == "выбрано вручную"

    select_row(win, 1)
    win.exclude_selected()
    assert rows(win)[1][1] == "— не переименовывать —"


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
