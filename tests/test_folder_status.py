"""Инструмент «почему у папки такой статус»: показывает числа, по которым решает программа."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import folder_status  # noqa: E402


def make(folder, names):
    os.makedirs(str(folder), exist_ok=True)
    for name in names:
        open(os.path.join(str(folder), name), "wb").close()
    return str(folder)


def run(folder, **kwargs):
    lines = []
    folder_status.report(folder, kwargs.pop("show_names", False), out=lines.append)
    return "\n".join(lines)


def test_named_folder_is_a_reference(tmp_path):
    folder = make(tmp_path / "готовая", ["Кулер.JPG", "Матрица фото 1.JPG", "Матрица фото 2.JPG",
                                         "Кабель HDMI.JPG", "Плата питания.JPG"])
    text = run(folder)
    assert "названо по шаблону: 5, не названо: 0, доля: 100%" in text
    assert "эталон" in text


def test_partly_renamed_folder_goes_to_renaming(tmp_path):
    """Случай с рабочей машины: пара десятков переименованных не делает папку готовой."""
    names = ["Шлейф %d фото %d.JPG" % (i, k) for i in range(1, 8) for k in range(1, 4)]
    names += ["IMG_%04d.JPG" % i for i in range(3)]
    text = run(make(tmp_path / "частичная", names))
    assert "названо по шаблону: 21, не названо: 3" in text
    assert "годится в эталоны группы: нет" in text
    assert "не хватило доли названных" in text
    assert "не хватило разных категорий" in text


def test_unnamed_photos_can_be_listed(tmp_path):
    folder = make(tmp_path / "папка", ["Кулер.JPG", "IMG_0001.JPG"])
    assert "IMG_0001.JPG" in run(folder, show_names=True)
    assert "IMG_0001.JPG" not in run(folder).split("ВЕРДИКТ")[0].replace("не названо: 1", "")


def test_unreadable_folder_is_reported(tmp_path):
    text = run(str(tmp_path / "нет такой"))
    assert "не прочитать" in text


def test_comparison_with_the_reference(tmp_path):
    """С эталоном видно главное: сколько его деталей в папке нашлось."""
    reference = make(tmp_path / "эталон", ["Кулер.JPG", "Матрица фото 1.JPG", "Матрица фото 2.JPG",
                                           "Кабель HDMI.JPG", "Плата питания.JPG"])
    partly = make(tmp_path / "частичная", ["Шлейф %d.JPG" % i for i in range(1, 20)] + ["IMG_1.JPG"])

    info = folder_status.read(reference, out=print)
    text = run_with_reference(partly, info)
    assert "деталей эталона найдено: 0%" in text
    assert "не нашлось: кабель hdmi, кулер, матрица, плата питания" in text
    assert "названа частично: 19 из 20 — будет переименована" in text

    assert "уже названа" in run_with_reference(reference, info)


def run_with_reference(folder, reference):
    lines = []
    folder_status.report(folder, False, reference, out=lines.append)
    return "\n".join(lines)


def test_without_a_reference_the_verdict_is_postponed(tmp_path):
    folder = make(tmp_path / "папка", ["Кулер.JPG", "Матрица фото 1.JPG"])
    assert "решается в сравнении с эталоном" in run(folder)
