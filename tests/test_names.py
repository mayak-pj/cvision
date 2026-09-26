"""Список категорий для редактора эталона."""

import pytest

from photo_renamer.names import NamesList, filter_names, fold as names_fold

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
    assert names.search("") == sorted(names.names(), key=names_fold)   # без запроса — по алфавиту


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


def test_filter_names_function():
    names = ["Кабель питания", "Кабель соединительный", "Плата оперативной памяти", "Блок питания"]
    assert filter_names("пит", names) == ["Блок питания", "Кабель питания"]  # равный ранг — по алфавиту
    assert filter_names("каб пит", names) == ["Кабель питания"]
    assert filter_names("", names) == sorted(names)    # без запроса список идёт по алфавиту


def test_list_without_query_is_alphabetical():
    """Без запроса список идёт по алфавиту: добавленная категория встаёт на своё место."""
    names = ["Процессор", "Вентилятор", "Съемный накопитель HDD", "Аккумулятор"]
    assert filter_names("", names) == ["Аккумулятор", "Вентилятор", "Процессор",
                                       "Съемный накопитель HDD"]


def test_search_ranking_is_kept():
    """С запросом сначала идут имена, начинающиеся с него, и уже внутри — по алфавиту."""
    names = ["Плата расширения", "Кабель питания", "Плата оперативной памяти"]
    assert filter_names("плата", names) == ["Плата оперативной памяти", "Плата расширения"]


def test_learn_from_skips_service_names():
    """Категории из имён файлов берутся автоматически, поэтому мусор туда попадать не должен.

    На рабочей машине в список категорий попали «file (1)»…«file (17)» — имена неназванных
    кадров. Руками введённое имя не проверяется: там человек знает, что пишет.
    """
    from photo_renamer.names import NamesList

    names = NamesList()
    names.learn_from([("Кабель сетевой", 2), ("file (1)", 1), ("Снимок_3", 1), ("12", 1)])
    assert names.names() == ["Кабель сетевой"]
    names.add("Плата (вид сзади)")
    assert "Плата (вид сзади)" in names


def test_junk_names_are_dropped_from_the_file(tmp_path):
    """Мусор из старой версии программы уходит из names.txt сам.

    Случай с рабочей машины: в списке категорий висели «file (1)»…«file (17)» — их туда
    записала версия, которая считала такие имена названными по шаблону.
    """
    path = tmp_path / "names.txt"
    path.write_text("Кулер | 1\nFile | 1\nФото | 2\nfile (1) | 1\nfile (17) | 1\n"
                    "Фотоаппарат | 1\nПанель USB, VGA, кнопок и индикации | 2\n", encoding="utf-8")
    names = NamesList(str(path))
    assert names.names() == ["Кулер", "Фотоаппарат", "Панель USB, VGA, кнопок и индикации"]
    names.save()
    saved = path.read_text(encoding="utf-8")
    assert "file" not in saved and "Фото |" not in saved


def test_latin_lookalike_letters_are_fixed(tmp_path):
    """Случай с рабочей машины: «Cтойка многопозиционная» с латинской C ушла в начало списка."""
    latin = "C" + "тойка многопозиционная"
    names = NamesList(str(tmp_path / "names.txt"))
    for name in ("Вентилятор", "Радиатор", "Шлейф"):
        names.add(name)
    assert names.add(latin) == "Стойка многопозиционная"          # чинится при добавлении
    assert names.search("")[-2:] == ["Стойка многопозиционная", "Шлейф"]
    assert latin in names                                          # старое написание — то же имя
    assert names.search("стойка") == ["Стойка многопозиционная"]


def test_genuine_latin_names_are_kept(tmp_path):
    names = NamesList(str(tmp_path / "names.txt"))
    for name in ("Кабель miniSAS", "Плата расширения PCIE", "Съемный накопитель SSD",
                 "Панель USB, VGA, кнопок и индикации", "RAID контроллер"):
        assert names.add(name) == name
