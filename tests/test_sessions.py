"""Логика окон без окон: всё это должно работать в любой библиотеке интерфейса."""

import os
import subprocess
import sys

import pytest

from photo_renamer import config as cfg, grouping
from photo_renamer.cache import PhotoCache
from photo_renamer.editor_session import EditorSession
from photo_renamer.names import NamesList
from photo_renamer.project_session import MARK_REFERENCE, ProjectSession, percent
from photo_renamer.review_session import SORT_PHOTO, ReviewSession, clamp_thumb_size, MAX_THUMB, MIN_THUMB
from photo_renamer.scanner import list_photos
from test_grouping import shoot

REF_NAMES = ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "Кабель HDMI.JPG",
             "Радиатор 1.JPG", "Радиатор 2.JPG"]
PARTS = [1, 2, 3, 4, 5, 6]


def app_config(tmp_path):
    config = cfg.AppConfig()
    config.cache_dir = str(tmp_path / "cache")
    config.logs_dir = str(tmp_path / "logs")
    config.names_file = str(tmp_path / "names.txt")
    return config


@pytest.fixture
def review(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "save_config", lambda *a, **k: None)   # настройки пользователя не трогаем
    root = tmp_path / "Проект"
    shoot(root / "эталон", PARTS, names=REF_NAMES)
    shoot(root / "SN2", PARTS)
    infos = [grouping.folder_info(list_photos(str(root / name))) for name in ("эталон", "SN2")]
    session = ReviewSession(grouping.FolderGroup(infos, infos[0], True), app_config(tmp_path))
    session.load_model(lambda done, total: None)
    assert session.advance()
    session.match_current(lambda done, total: None)
    session.resort()
    yield session, root / "SN2"
    session.close()


def test_sessions_do_not_need_tkinter():
    """Новому окну на другой библиотеке логика достаётся без tkinter."""
    code = ("import sys\n"
            "sys.modules['tkinter'] = None\n"      # любая попытка импорта упадёт
            "from photo_renamer import project_session, review_session, editor_session\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_percent_says_100_only_when_everything_is_named():
    assert percent(0.996) == 99 and percent(1) == 100


def test_thumb_size_stays_in_range():
    assert clamp_thumb_size(1) == MIN_THUMB and clamp_thumb_size(10 ** 6) == MAX_THUMB


def test_review_rename_and_undo_return_original_names(review):
    session, folder = review
    before = sorted(os.listdir(str(folder)))
    plan = session.build_plan()
    assert plan.pending
    assert "Переименовано: %d" % len(plan.pending) in session.apply_plan(plan)
    assert sorted(os.listdir(str(folder))) != before
    assert len(session.reports) == 1

    assert "отменено" in session.undo_folder()
    assert sorted(os.listdir(str(folder))) == before
    assert session.reports == [] and session.undo_folder() is None


def test_review_edit_can_be_undone_in_one_step(review):
    session, _ = review
    photo = session.match.photos[0]
    was = photo.new_name
    session.apply_category([0], None)
    assert not session.match.photos[0].new_name
    assert session.undo_edit() == [0]
    assert session.match.photos[0].new_name == was
    assert session.undo_edit() is None


def test_review_empty_edit_leaves_nothing_to_undo(review):
    session, _ = review
    assert session.apply_candidate([0], 99) is False
    assert session.undo_edit() is None


def test_review_sort_click_twice_reverses(review):
    session, _ = review
    session.sort_by(SORT_PHOTO)
    forward = [p.index for p in session.display_photos()]
    session.sort_by(SORT_PHOTO)
    assert [p.index for p in session.display_photos()] == forward[::-1]


def test_review_sides_typed_by_hand_are_kept(review):
    session, _ = review
    name = session.add_category("Стойка")
    suggested = session.suggest_sides(name, 2, None, force=True)
    assert suggested == 2
    assert session.suggest_sides(name, 3, 5) is None        # в поле уже своё число
    session.apply_category([0, 1, 2, 3, 4], name, 5)
    assert session.names.sides_for(name) == 5


def test_review_categories_add_and_remove(review):
    session, _ = review
    name = session.add_category("Стойка")
    assert name in session.categories and session.filter_categories("стой") == [name]
    session.remove_category(name)
    assert name not in session.categories


def test_editor_rename_and_undo(tmp_path):
    folder = tmp_path / "SN1"
    shoot(folder, [1, 2, 3])
    config = app_config(tmp_path)
    session = EditorSession(str(folder), config, NamesList(config.names_path()),
                            PhotoCache(config.cache_path(), config.cache_max_mb))
    session.load_folder()
    before = sorted(os.listdir(str(folder)))
    assignment = session.plan_rename(session.photos[:2], "Процессор", 2)
    assert sorted(os.listdir(str(folder))) == before           # план ничего не меняет на диске

    done, failed = session.apply_rename(assignment, "Процессор", 2)
    session.apply_renames(done)
    assert not failed and len(done) == 2
    assert session.shown[:2] == ["Процессор фото 1" + os.path.splitext(before[0])[1],
                                 "Процессор фото 2" + os.path.splitext(before[0])[1]]
    assert session.names.sides_for("Процессор") == 2
    assert "названо по шаблону: 2" in session.status_text()

    session.apply_renames(session.undo())
    assert sorted(os.listdir(str(folder))) == before
    assert session.undo() is None
    session.close()


def test_project_session_analysis_rows_and_actions(tmp_path):
    root = tmp_path / "Проект"
    shoot(root / "эталон", PARTS, names=REF_NAMES)
    shoot(root / "SN2", PARTS)
    session = ProjectSession(app_config(tmp_path))
    session.project_root = str(root)
    assert session.rows() == [] and session.available_actions(None, None)["rename_group"] is False

    messages = []
    session.analysis = session.analysis_job(str(root))(messages.append, lambda: False)
    assert any(m[0] == "folder" for m in messages)
    assert "групп: 1" in session.summary()

    rows = session.rows()
    assert len(rows) == 1 and len(rows[0].children) == 2
    reference = next(r for r in rows[0].children if r.values[0] == MARK_REFERENCE)
    other = next(r for r in rows[0].children if r.values[0] != MARK_REFERENCE)
    assert session.available_actions(reference.folder, 0)["make_reference"] is False
    assert session.available_actions(other.folder, 0)["make_reference"] is True

    assert session.toggle_rename(reference.folder) is False    # у эталона отметки нет
    was = other.folder.will_rename
    assert session.toggle_rename(other.folder) is True and other.folder.will_rename != was

    session.save_state(str(root))
    assert session.load_state() == {"project": str(root)}


def test_review_edits_of_the_last_folder_are_not_undone_in_the_next(review):
    session, _ = review
    session.confirm([0])
    session.queue.append(session.folder)
    assert session.advance() and session.undo_edit() is None
