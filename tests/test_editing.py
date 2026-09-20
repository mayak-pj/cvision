"""Логика редактора эталона: какие имена получат выделенные фото."""

import pytest

from photo_renamer import oplog
from photo_renamer.editing import Assignment, assign, existing_instances, undo_plan

NAMES = ["IMG_001.JPG", "IMG_002.JPG", "IMG_003.JPG", "IMG_004.JPG", "IMG_005.jpg", "IMG_006.JPG"]


def pairs(result: Assignment):
    return [(i.old, i.new) for i in result.plan.items]


def test_single_photo_single_side():
    r = assign("/f", NAMES, ["IMG_003.JPG"], "Микросхема", 1)
    assert pairs(r) == [("IMG_003.JPG", "Микросхема.JPG")]
    assert r.instances == [1] and r.warnings == []


def test_two_sides_two_instances_order_follows_folder():
    r = assign("/f", NAMES, ["IMG_004.JPG", "IMG_001.JPG", "IMG_002.JPG", "IMG_003.JPG"],
               "Плата оперативной памяти", 2)
    assert pairs(r) == [
        ("IMG_001.JPG", "Плата оперативной памяти 1 фото 1.JPG"),
        ("IMG_002.JPG", "Плата оперативной памяти 1 фото 2.JPG"),
        ("IMG_003.JPG", "Плата оперативной памяти 2 фото 1.JPG"),
        ("IMG_004.JPG", "Плата оперативной памяти 2 фото 2.JPG"),
    ]
    assert r.instances == [1, 2]


def test_extension_case_is_kept():
    r = assign("/f", NAMES, ["IMG_005.jpg"], "Кулер", 1)
    assert pairs(r) == [("IMG_005.jpg", "Кулер.jpg")]


def test_numbering_continues_and_first_gets_number():
    names = ["Кулер.JPG", "IMG_002.JPG", "IMG_003.JPG"]
    r = assign("/f", names, ["IMG_002.JPG"], "Кулер", 1)
    assert pairs(r) == [("Кулер.JPG", "Кулер 1.JPG"), ("IMG_002.JPG", "Кулер 2.JPG")]
    assert r.renumbered == 1 and r.instances == [2]

    names = ["Кулер 1.JPG", "Кулер 2.JPG", "IMG_003.JPG"]
    r = assign("/f", names, ["IMG_003.JPG"], "Кулер", 1)
    assert pairs(r) == [("IMG_003.JPG", "Кулер 3.JPG")] and r.renumbered == 0


def test_numbering_continues_for_multi_side_category():
    names = ["Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "IMG_003.JPG", "IMG_004.JPG"]
    r = assign("/f", names, ["IMG_003.JPG", "IMG_004.JPG"], "Процессор", 2)
    assert pairs(r) == [("IMG_003.JPG", "Процессор 2 фото 1.JPG"), ("IMG_004.JPG", "Процессор 2 фото 2.JPG")]


def test_incomplete_instance_warns():
    r = assign("/f", NAMES, NAMES[:3], "Трансивер", 2)
    assert r.incomplete == 1
    assert "1 фото вместо 2" in r.warnings[0]
    assert pairs(r)[-1] == ("IMG_003.JPG", "Трансивер 2 фото 1.JPG")


def test_conflict_with_existing_name_is_skipped():
    names = ["Кулер 1.JPG", "Кулер 2.JPG", "IMG_003.JPG"]
    r = assign("/f", names, ["IMG_003.JPG"], "Кулер 2", 1)  # имя «Кулер 2.JPG» уже занято
    assert r.plan.items[0].result == oplog.RESULT_SKIPPED
    assert "занято" in r.plan.items[0].message and r.warnings


@pytest.mark.parametrize("category, sides, error", [("", 1, "категория"), ("Кулер", 0, "ракурс")])
def test_validation(category, sides, error):
    with pytest.raises(ValueError, match=error):
        assign("/f", NAMES, ["IMG_001.JPG"], category, sides)


def test_no_selection():
    with pytest.raises(ValueError, match="выделено"):
        assign("/f", NAMES, [], "Кулер", 1)


def test_existing_instances():
    names = ["Кулер.JPG", "Радиатор 3.JPG", "Радиатор 1 фото 1.JPG", "IMG_1.JPG"]
    assert existing_instances(names, "радиатор") == [1, 3]
    assert existing_instances(names, "Кулер") == [1]
    assert existing_instances(names, "Плата") == []


def test_undo_plan():
    r = assign("/f", NAMES, ["IMG_001.JPG", "IMG_002.JPG"], "Кулер", 1)
    undo = undo_plan("/f", r.plan.items)
    assert [(i.old, i.new) for i in undo.items] == [("Кулер 2.JPG", "IMG_002.JPG"), ("Кулер 1.JPG", "IMG_001.JPG")]
