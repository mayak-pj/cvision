"""Список категорий для редактора эталона."""

import pytest

from photo_renamer.names import NamesList

SAMPLE = """# список категорий
Плата оперативной памяти | 2
Вентилятор | 1
Кулер
Кабель волоконно-оптический | 1
Плата расширения PCIE | 2
Процессор | 3
Съемный накопитель SSD | 3
"""


@pytest.fixture
def names(tmp_path):
    path = tmp_path / "names.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    return NamesList(str(path))


def test_load(names):
    assert len(names) == 7
    assert names.names()[0] == "Плата оперативной памяти"
    assert names.sides_for("плата оперативной памяти") == 2
    assert names.sides_for("Кулер") == 1            # без указания — один ракурс
    assert names.sides_for("Неизвестно") == 1
    assert "ВЕНТИЛЯТОР" in names


def test_search_by_word_start(names):
    assert names.search("пам") == ["Плата оперативной памяти"]
    assert names.search("оп пам") == ["Плата оперативной памяти"]
    assert names.search("плата") == ["Плата оперативной памяти", "Плата расширения PCIE"]
    assert names.search("pcie") == ["Плата расширения PCIE"]
    assert names.search("к") == ["Кабель волоконно-оптический", "Кулер"]
    assert names.search("оптический") == ["Кабель волоконно-оптический"]  # слово в середине имени
    assert names.search("") == names.names()


def test_search_is_case_and_yo_insensitive(tmp_path):
    n = NamesList()
    n.add("Крепёж")
    n.add("Съёмный накопитель")
    assert n.search("креп") == ["Крепёж"]
    assert n.search("крепеж") == ["Крепёж"]
    assert n.search("СЪЕМНЫЙ") == ["Съёмный накопитель"]


def test_search_falls_back_to_substring(names):
    assert names.search("тилят") == ["Вентилятор"]
    assert names.search("неттакого") == []


def test_add_remove_and_remember(names, tmp_path):
    names.add("Радиатор", 1)
    names.remember_sides("Радиатор", 2)
    assert names.sides_for("радиатор") == 2
    assert names.remove("Кулер") and not names.remove("Кулер")
    names.save()

    again = NamesList(str(tmp_path / "names.txt"))
    assert "Кулер" not in again and again.sides_for("Радиатор") == 2
    assert again.names()[0] == "Плата оперативной памяти"


def test_learn_from_reference(names):
    names.learn_from([("Процессор", 2), ("Микросхема", 1)])
    assert names.sides_for("Процессор") == 2   # в этом проекте у процессора 2 ракурса
    assert names.sides_for("Микросхема") == 1
    assert "Микросхема" in names


def test_warnings_and_bad_lines(tmp_path):
    path = tmp_path / "names.txt"
    path.write_text("Кулер | два\n\nКулер | 1\n  \n", encoding="utf-8")
    n = NamesList()
    n.path = str(path)
    warnings = n.load()
    assert len(warnings) == 2 and len(n) == 1 and n.sides_for("Кулер") == 1


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        NamesList().add("   ")
