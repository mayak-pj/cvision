import os
import unicodedata

import pytest

from photo_renamer.reference import (
    ParsedName,
    build_reference,
    category_key,
    describe,
    load_reference,
    parse_name,
)


@pytest.mark.parametrize(
    "filename, category, instance, side",
    [
        ("Процессор 1 фото 1.jpg", "Процессор", 1, 1),
        ("Процессор 1 фото 2.JPG", "Процессор", 1, 2),
        ("Материнская плата фото 1.jpg", "Материнская плата", None, 1),
        ("Оперативная память 2 фото 2.jpg", "Оперативная память", 2, 2),
        ("Кулер.jpg", "Кулер", None, None),
        ("Кулер 2.jpg", "Кулер", 2, None),
        ("Кабель HDMI.jpg", "Кабель HDMI", None, None),
        ("Кабель DP-miniDP 3.jpg", "Кабель DP-miniDP", 3, None),
        ("Кабель USB 3.0.jpg", "Кабель USB 3.0", None, None),
        ("Оперативная память DDR4 1 фото 2.jpg", "Оперативная память DDR4", 1, 2),
        ("Блок питания  ФОТО1.jpg", "Блок питания", None, 1),     # регистр и пробелы
        ("  Жёсткий   диск 1   Фото 2 .jpg", "Жёсткий диск", 1, 2),
        ("фото 1.jpg", "фото", 1, None),                        # категория не может быть пустой
        ("12.jpg", "12", None, None),
    ],
)
def test_parse_name(filename, category, instance, side):
    parsed = parse_name(filename)
    assert (parsed.category, parsed.instance, parsed.side) == (category, instance, side)


def test_parse_nfd_name():
    parsed = parse_name(unicodedata.normalize("NFD", "Жёсткий диск 1 фото 1.jpg"))
    assert parsed.category == "Жёсткий диск"


def test_category_key():
    assert category_key("Кабель  HDMI") == category_key("кабель hdmi")


TYPICAL = [
    "Процессор 1 фото 1.jpg",
    "Процессор 1 фото 2.jpg",
    "Материнская плата фото 1.jpg",
    "Материнская плата фото 2.jpg",
    "Оперативная память 1 фото 1.jpg",
    "Оперативная память 1 фото 2.jpg",
    "Оперативная память 2 фото 1.jpg",
    "Оперативная память 2 фото 2.jpg",
    "Кулер.jpg",
    "Кабель HDMI.jpg",
    "Кабель DP.jpg",
    "Кабель DP 2.jpg",
    "Видеокарта 1 фото 1.jpg",
    "Видеокарта 1 фото 2.jpg",
    "Видеокарта 1 фото 3.jpg",
]


def test_build_typical_reference():
    ref = build_reference("/ref", TYPICAL)
    assert ref.warnings == []
    cats = ref.categories
    assert [c.name for c in cats.values()] == [
        "Процессор", "Материнская плата", "Оперативная память", "Кулер", "Кабель HDMI", "Кабель DP", "Видеокарта",
    ]

    def spec(name):
        c = cats[category_key(name)]
        return c.numbered, c.has_side_suffix, c.sides, c.instances, len(c.photos)

    assert spec("Процессор") == (True, True, 2, 1, 2)
    assert spec("Материнская плата") == (False, True, 2, 1, 2)
    assert spec("Оперативная память") == (True, True, 2, 2, 4)
    assert spec("Кулер") == (False, False, 1, 1, 1)
    assert spec("Кабель DP") == (False, False, 1, 2, 2)
    assert spec("Видеокарта") == (True, True, 3, 1, 3)
    assert ref.expected_counts()["Оперативная память"] == 4


def test_format_name_roundtrip():
    ref = build_reference("/ref", TYPICAL)
    for photo in ref.photos:
        cat = ref.category_of(photo)
        assert cat.format_name(photo.instance, photo.side) == photo.filename


def test_format_name_new_instances_and_extension():
    ref = build_reference("/ref", TYPICAL)
    cooler = ref.categories[category_key("Кулер")]
    ram = ref.categories[category_key("Оперативная память")]
    board = ref.categories[category_key("Материнская плата")]
    assert cooler.format_name(1, 1, ".JPG") == "Кулер.JPG"
    assert cooler.format_name(2, 1) == "Кулер 2.jpg"
    assert ram.format_name(3, 2) == "Оперативная память 3 фото 2.jpg"
    assert board.format_name(2, 1) == "Материнская плата 2 фото 1.jpg"
    # каждое сгенерированное имя разбирается обратно в то же самое
    for cat, inst, side in [(cooler, 2, 1), (ram, 3, 2), (board, 2, 1)]:
        parsed = parse_name(cat.format_name(inst, side))
        assert category_key(parsed.category) == cat.key
        assert (parsed.instance or 1, parsed.side or 1) == (inst, side)


def test_side_word_spelling_is_preserved():
    ref = build_reference("/ref", ["ЦП 1 Фото 1.jpg", "ЦП 1 Фото 2.jpg"])
    assert ref.categories[category_key("ЦП")].format_name(2, 1) == "ЦП 2 Фото 1.jpg"


@pytest.mark.parametrize(
    "names, fragment",
    [
        (["Процессор 1 фото 1.jpg", "Процессор 1 фото 3.jpg"], "стороны не идут подряд"),
        (["Память 1.jpg", "Память 3.jpg"], "номера экземпляров не идут подряд"),
        (["Кабель USB 3.jpg"], "номера экземпляров не идут подряд"),
        (["Плата фото 1.jpg", "Плата.jpg"], "у части фото нет"),
        (["Кабель HDMI.jpg", "кабель hdmi 2.jpg"], "записана по-разному"),
        (["ОЗУ 1 фото 1.jpg", "ОЗУ 1 фото 2.jpg", "ОЗУ 2 фото 1.jpg"], "разное число сторон"),
        (["Кулер.jpg", "Кулер 1.jpg"], "и с номером, и без"),
        (["Кулер.jpg", "кулер.JPG"], "повторяются имена"),
        (["Процессор 1 фото 1.jpg", "DSC_0042.JPG"], "не переименован"),
        (["IMG_20240501_101010.jpg"], "не переименован"),
        ([], "нет ни одного фото"),
    ],
)
def test_reference_warnings(names, fragment):
    ref = build_reference("/ref", names)
    assert any(fragment in w for w in ref.warnings), ref.warnings


def test_load_reference_from_disk(tmp_path):
    for name in TYPICAL + ["Рентгенограмма платы.jpg", "схема.png", "скан.jpeg"]:
        (tmp_path / name).write_bytes(b"x")
    ref = load_reference(str(tmp_path))
    assert len(ref.photos) == len(TYPICAL)
    assert ref.excluded == ["Рентгенограмма платы.jpg"]
    assert ref.photos[0].path == os.path.join(str(tmp_path), ref.photos[0].filename)
    text = describe(ref)
    assert "Оперативная память" in text and "Пропущено рентгенограмм: 1" in text


def test_ref_photo_defaults():
    ref = build_reference("/ref", ["Кулер.jpg"])
    photo = ref.photos[0]
    assert photo.parsed == ParsedName("Кулер", None, None)
    assert (photo.instance, photo.side) == (1, 1)


@pytest.mark.parametrize("name", ["DSC_0042.JPG", "IMG_8921.JPG", "IMG_BB_043972.JPG", "IMG_AA_000414.JPG",
                                  "IMG_20240501_101010.jpg", "P1010001.JPG", "dscn1234.jpg"])
def test_camera_names(name):
    from photo_renamer.reference import is_camera_name, is_template_name

    assert is_camera_name(name)
    assert not is_template_name(name)


@pytest.mark.parametrize("name, expected", [
    ("Процессор 1 фото 2.JPG", True),
    ("Кулер.jpg", True),
    ("Кулер 2.jpg", True),
    ("Материнская плата фото 1.jpg", True),
    ("Кабель miniSAS.JPG", True),
    ("Процессор  1 фото 2.jpg", False),   # двойной пробел — не каноническая запись
    ("12.jpg", False),
    ("IMG_BB_043972.JPG", False),
])
def test_template_names(name, expected):
    from photo_renamer.reference import is_template_name

    assert is_template_name(name) is expected
