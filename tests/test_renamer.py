"""Переименование, лог и откат. Всё происходит во временных папках pytest."""

import hashlib
import os

import pytest

from photo_renamer import oplog, renamer
from photo_renamer.renamer import RenameItem, RenamePlan, apply_plan, build_plan, rollback, validate_filename
from test_matcher import run as run_match


def fill(folder, names):
    os.makedirs(str(folder), exist_ok=True)
    for name in names:
        (folder / name).write_bytes(("содержимое " + name).encode("utf-8"))


def snapshot(folder):
    """{содержимое → имя}: позволяет проверить, что ни один файл не потерян и не изменён."""
    return {hashlib.sha256((folder / n).read_bytes()).hexdigest(): n for n in sorted(os.listdir(str(folder)))}


def plan_for(folder, pairs):
    return RenamePlan(str(folder), [RenameItem(a, b, "Кат", 90.0) for a, b in pairs])


@pytest.fixture
def log(tmp_path):
    with oplog.RunLog(str(tmp_path / "logs"), "проверка") as run_log:
        yield run_log


def content_names(folder):
    """Сопоставление «исходное имя (по содержимому) → текущее имя»."""
    out = {}
    for name in os.listdir(str(folder)):
        text = (folder / name).read_bytes().decode("utf-8")
        out[text.replace("содержимое ", "")] = name
    return out


# --- имена ---------------------------------------------------------------------

@pytest.mark.parametrize("name", ["Кулер.JPG", "Оперативная память 2 фото 1.jpg", "Кабель DP-miniDP.jpg"])
def test_valid_names(name):
    assert validate_filename(name) is None


@pytest.mark.parametrize("name", ["", "a/b.jpg", "a\\b.jpg", "a:b.jpg", "a?.jpg", "имя.", "имя ", "CON.jpg",
                                  "com1.JPG", "x" * 256])
def test_invalid_names(name):
    assert validate_filename(name) is not None


def test_sanitize_and_label():
    assert oplog.sanitize('a<b>c:"d"/e\\f|g?h*') == "a_b_c_d_e_f_g_h_"
    assert oplog.sanitize("  ..  ") == "папка"
    assert oplog.folder_label(r"\\server\share\Проект 7\SN 001") == "Проект 7_SN 001"
    assert oplog.folder_label("/mnt/data/Проект/SN:1/") == "Проект_SN_1"


# --- переименование ------------------------------------------------------------

def test_simple_renames_and_log(tmp_path, log):
    folder = tmp_path / "SN1"
    fill(folder, ["DSC_0001.JPG", "DSC_0002.JPG", "scan.jpeg"])
    before = snapshot(folder)
    plan = apply_plan(plan_for(folder, [("DSC_0001.JPG", "Кулер.JPG"), ("DSC_0002.JPG", "Кабель HDMI.JPG")]), log)
    assert [i.result for i in plan.items] == [oplog.RESULT_OK] * 2
    assert sorted(os.listdir(str(folder))) == sorted(["Кулер.JPG", "Кабель HDMI.JPG", "scan.jpeg"])
    assert set(snapshot(folder)) == set(before)  # содержимое то же, файлов столько же

    rows = oplog.read_log(log.csv_path)
    renames = [r for r in rows if r["Действие"] == oplog.ACTION_RENAME]
    assert [(r["Было"], r["Стало"]) for r in renames] == [("DSC_0001.JPG", "Кулер.JPG"),
                                                          ("DSC_0002.JPG", "Кабель HDMI.JPG")]
    assert all(r["Папка"] == str(folder) for r in rows)
    assert renames[0]["Уверенность, %"] == "90,0"
    assert rows[0]["Дата и время"]
    with open(log.csv_path, "rb") as f:
        assert f.read(3) == b"\xef\xbb\xbf"  # BOM для Excel


def test_log_is_next_to_program_not_in_target(tmp_path, log):
    folder = tmp_path / "SN1"
    fill(folder, ["a.jpg"])
    apply_plan(plan_for(folder, [("a.jpg", "Кулер.jpg")]), log)
    assert os.listdir(str(folder)) == ["Кулер.jpg"]
    assert os.path.dirname(log.csv_path) == str(tmp_path / "logs")


def test_swap_and_chain_via_temp_names(tmp_path, log):
    folder = tmp_path / "SN1"
    fill(folder, ["A.jpg", "B.jpg", "C.jpg", "D.jpg"])
    # обмен A↔B и цепочка C→D→E
    plan = apply_plan(plan_for(folder, [("A.jpg", "B.jpg"), ("B.jpg", "A.jpg"), ("C.jpg", "D.jpg"),
                                        ("D.jpg", "E.jpg")]), log)
    assert all(i.result == oplog.RESULT_OK for i in plan.items)
    assert content_names(folder) == {"A.jpg": "B.jpg", "B.jpg": "A.jpg", "C.jpg": "D.jpg", "D.jpg": "E.jpg"}
    actions = [r["Действие"] for r in oplog.read_log(log.csv_path)]
    assert actions.count(oplog.ACTION_TEMP) == 3  # A, B, C: их новые имена заняты файлами пачки


def test_case_only_rename(tmp_path, log):
    folder = tmp_path / "SN1"
    fill(folder, ["кулер.jpg"])
    plan = apply_plan(plan_for(folder, [("кулер.jpg", "Кулер.jpg")]), log)
    assert plan.items[0].result == oplog.RESULT_OK
    assert os.listdir(str(folder)) == ["Кулер.jpg"]


def test_occupied_name_is_never_overwritten(tmp_path, log):
    folder = tmp_path / "SN1"
    fill(folder, ["DSC_0001.JPG", "Кулер.JPG", "DSC_0002.JPG"])
    before = snapshot(folder)
    plan = apply_plan(plan_for(folder, [("DSC_0001.JPG", "кулер.jpg"), ("DSC_0002.JPG", "Кабель HDMI.JPG")]), log)
    assert plan.items[0].result == oplog.RESULT_SKIPPED
    assert "занято" in plan.items[0].message
    assert plan.items[1].result == oplog.RESULT_OK
    assert set(snapshot(folder)) == set(before)
    assert "DSC_0001.JPG" in os.listdir(str(folder))


def test_duplicate_new_names_are_skipped(tmp_path, log):
    folder = tmp_path / "SN1"
    fill(folder, ["a.jpg", "b.jpg"])
    plan = apply_plan(plan_for(folder, [("a.jpg", "Кулер.jpg"), ("b.jpg", "КУЛЕР.jpg")]), log)
    assert [i.result for i in plan.items] == [oplog.RESULT_SKIPPED] * 2
    assert sorted(os.listdir(str(folder))) == ["a.jpg", "b.jpg"]


def test_folder_changed_after_preview(tmp_path, log):
    folder = tmp_path / "SN1"
    fill(folder, ["a.jpg", "b.jpg", "c.jpg"])
    plan = plan_for(folder, [("a.jpg", "Кулер.jpg"), ("b.jpg", "Кабель HDMI.jpg"), ("c.jpg", "Кабель DP.jpg")])
    renamer.check_plan(plan)
    assert plan.pending == plan.items
    os.remove(str(folder / "a.jpg"))                    # файл удалили после предпросмотра
    (folder / "Кабель HDMI.jpg").write_bytes(b"new")    # имя заняли после предпросмотра
    apply_plan(plan, log)
    assert [i.result for i in plan.items] == [oplog.RESULT_SKIPPED, oplog.RESULT_SKIPPED, oplog.RESULT_OK]
    assert (folder / "Кабель HDMI.jpg").read_bytes() == b"new"


def test_error_on_one_file_does_not_stop_others(tmp_path, log, monkeypatch):
    folder = tmp_path / "SN1"
    fill(folder, ["a.jpg", "b.jpg"])
    real_rename = os.rename

    def flaky(src, dst):
        if src.endswith("a.jpg"):
            raise PermissionError(13, "Permission denied", src)
        real_rename(src, dst)

    monkeypatch.setattr(renamer.os, "rename", flaky)
    plan = apply_plan(plan_for(folder, [("a.jpg", "Кулер.jpg"), ("b.jpg", "Кабель HDMI.jpg")]), log)
    assert plan.items[0].result == oplog.RESULT_ERROR
    assert "нет доступа" in plan.items[0].message
    assert plan.items[1].result == oplog.RESULT_OK


def test_failed_final_step_restores_original_name(tmp_path, log, monkeypatch):
    folder = tmp_path / "SN1"
    fill(folder, ["A.jpg", "B.jpg"])
    real_rename = os.rename

    def fail_final(src, dst):
        if renamer.TEMP_MARK in src and dst.endswith("B.jpg"):
            raise PermissionError(13, "Permission denied", src)
        real_rename(src, dst)

    monkeypatch.setattr(renamer.os, "rename", fail_final)
    plan = apply_plan(plan_for(folder, [("A.jpg", "B.jpg"), ("B.jpg", "A.jpg")]), log)
    # A не смог стать B, и ему вернули исходное имя. Тогда B не может стать A (имя занято),
    # а вернуть ему B.jpg тоже не удалось (сбой подстроен), и он остался под временным именем.
    # Ни один файл не потерян, всё видно в логе, и откат вернёт всё как было.
    assert [i.result for i in plan.items] == [oplog.RESULT_ERROR, oplog.RESULT_ERROR]
    assert "исходное имя восстановлено" in plan.items[0].message
    assert "выполните откат" in plan.items[1].message
    names = os.listdir(str(folder))
    assert len(names) == 2 and "A.jpg" in names and any(renamer.TEMP_MARK in n for n in names)
    monkeypatch.setattr(renamer.os, "rename", real_rename)
    with oplog.RunLog(str(tmp_path / "logs"), "откат") as rb_log:
        results = rollback(log.csv_path, rb_log)
    assert all(r["ok"] for r in results)
    assert content_names(folder) == {"A.jpg": "A.jpg", "B.jpg": "B.jpg"}


# --- откат ---------------------------------------------------------------------

def test_rollback_restores_everything(tmp_path, log):
    folder = tmp_path / "SN1"
    fill(folder, ["A.jpg", "B.jpg", "C.jpg", "кулер.jpg"])
    original = content_names(folder)
    apply_plan(plan_for(folder, [("A.jpg", "B.jpg"), ("B.jpg", "A.jpg"), ("C.jpg", "Кабель DP.jpg"),
                                 ("кулер.jpg", "Кулер.jpg")]), log)
    assert content_names(folder) != original
    with oplog.RunLog(str(tmp_path / "logs"), "откат") as rb_log:
        results = rollback(log.csv_path, rb_log)
    assert all(r["ok"] for r in results)
    assert content_names(folder) == original
    rb_rows = oplog.read_log(rb_log.csv_path)
    assert all(r["Действие"] == oplog.ACTION_ROLLBACK and r["Папка"] == str(folder) for r in rb_rows)


def test_rollback_skips_changed_files(tmp_path, log):
    folder = tmp_path / "SN1"
    fill(folder, ["a.jpg", "b.jpg"])
    apply_plan(plan_for(folder, [("a.jpg", "Кулер.jpg"), ("b.jpg", "Кабель HDMI.jpg")]), log)
    os.rename(str(folder / "Кулер.jpg"), str(folder / "вручную.jpg"))  # пользователь переименовал сам
    (folder / "b.jpg").write_bytes(b"new")                               # исходное имя занято
    with oplog.RunLog(str(tmp_path / "logs"), "откат") as rb_log:
        results = rollback(log.csv_path, rb_log)
    assert [r["ok"] for r in results] == [False, False]
    assert sorted(os.listdir(str(folder))) == ["b.jpg", "Кабель HDMI.jpg", "вручную.jpg"]


def test_rollback_only_one_folder(tmp_path, log):
    f1, f2 = tmp_path / "SN1", tmp_path / "SN2"
    fill(f1, ["a.jpg"])
    fill(f2, ["a.jpg"])
    apply_plan(plan_for(f1, [("a.jpg", "Кулер.jpg")]), log)
    apply_plan(plan_for(f2, [("a.jpg", "Кулер.jpg")]), log)
    with oplog.RunLog(str(tmp_path / "logs"), "откат") as rb_log:
        rollback(log.csv_path, rb_log, folder=str(f2))
    assert os.listdir(str(f1)) == ["Кулер.jpg"] and os.listdir(str(f2)) == ["a.jpg"]


# --- план из результата сопоставления -------------------------------------------

def test_build_plan_from_match(tmp_path):
    folder = tmp_path / "SN1"
    names = ["DSC_0001.JPG", "DSC_0002.JPG", "DSC_0003.JPG"]
    fill(folder, names)
    match = run_match([{"Кулер": 0.08}, {}, {"Кабель HDMI": 0.07}], names=names)
    match.folder = str(folder)
    plan = build_plan(match)
    assert [(i.old, i.new) for i in plan.items] == [("DSC_0001.JPG", "Кулер.JPG"), ("DSC_0003.JPG", "Кабель HDMI.JPG")]
    assert [k[0] for k in plan.kept] == ["DSC_0002.JPG"]
    assert plan.pending == plan.items


def test_log_file_names_are_unique(tmp_path):
    logs = str(tmp_path / "logs")
    with oplog.RunLog(logs, 'Проект/SN:1') as a, oplog.RunLog(logs, 'Проект/SN:1') as b:
        assert a.csv_path != b.csv_path
        assert "Проект_SN_1" in os.path.basename(a.csv_path)
    with oplog.RunLog(logs, "x", dry_run=True) as c:
        assert c.csv_path.endswith("_проверка.csv")


def test_plan_notes_and_kept_candidates(tmp_path):
    folder = tmp_path / "SN1"
    names = ["DSC_0001.JPG", "DSC_0002.JPG", "DSC_0003.JPG"]
    fill(folder, names)
    match = run_match([{"Оперативная память": 0.10}, {"Оперативная память": 0.38}, {}], names=names)
    match.folder = str(folder)
    plan = build_plan(match)
    assert "по правилу сторон" in plan.items[1].note
    name, category, confidence, reason = plan.kept[0]
    assert name == "DSC_0003.JPG" and category == "" and confidence is not None
    assert "кандидаты:" in reason
