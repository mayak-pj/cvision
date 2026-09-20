"""Сквозная проверка на синтетических JPEG: эталон на диске → признаки → сопоставление папки."""

import os

import pytest

from photo_renamer.features import FeatureExtractor
from photo_renamer.matcher import STATUS_OK, STATUS_SIDE_RULE, MatchConfig, ReferenceModel, match_folder
from photo_renamer.reference import load_reference
from photo_renamer.scanner import list_photos
from synthetic import make_part, perturb, save_jpeg

# категория/сторона → «деталь» (seed генератора). Стороны одной детали выглядят по-разному.
PARTS = {
    "Процессор 1 фото 1": 1,
    "Процессор 1 фото 2": 2,
    "Материнская плата фото 1": 3,
    "Материнская плата фото 2": 4,
    "Оперативная память 1 фото 1": 5,
    "Оперативная память 1 фото 2": 6,
    "Оперативная память 2 фото 1": 5,   # вторая планка той же модели
    "Оперативная память 2 фото 2": 6,
    "Кулер": 7,
    "Кабель HDMI": 8,
    "Кабель DP": 9,
    "Блок питания": 10,
}


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    ref_dir = tmp_path_factory.mktemp("Эталон")
    for k, (name, seed) in enumerate(PARTS.items()):
        save_jpeg(perturb(make_part(seed), seed=500 + k), ref_dir / (name + ".jpg"))
    (ref_dir / "Рентгенограмма.jpg").write_bytes(b"x")
    reference = load_reference(str(ref_dir))
    assert reference.warnings == []
    m = ReferenceModel.build(reference, FeatureExtractor())
    assert m.warnings == []
    return m


def make_target(folder, shots):
    """shots — список seed (None — посторонний снимок). Файлы DSC_0001.JPG, ... в порядке съёмки."""
    os.makedirs(str(folder), exist_ok=True)
    for k, (seed, angle) in enumerate(shots):
        img = make_part(99) if seed is None else make_part(seed)
        save_jpeg(perturb(img, seed=1000 + k, rotate=angle), folder / ("DSC_%04d.JPG" % (k + 1)))
    return list_photos(str(folder)).photos


def test_complete_folder(model, tmp_path):
    shots = [(7, 0), (5, 0), (6, 0), (1, 0), (2, 0), (8, 0), (5, 0), (6, 0), (3, 0), (4, 0), (9, 0), (10, 0)]
    names = make_target(tmp_path / "SN001", shots)
    result = match_folder(model, str(tmp_path / "SN001"), names, MatchConfig(strict_counts=False))
    assert [p.new_name for p in result.photos] == [
        "Кулер.JPG",
        "Оперативная память 1 фото 1.JPG",
        "Оперативная память 1 фото 2.JPG",
        "Процессор 1 фото 1.JPG",
        "Процессор 1 фото 2.JPG",
        "Кабель HDMI.JPG",
        "Оперативная память 2 фото 1.JPG",
        "Оперативная память 2 фото 2.JPG",
        "Материнская плата фото 1.JPG",
        "Материнская плата фото 2.JPG",
        "Кабель DP.JPG",
        "Блок питания.JPG",
    ]
    assert all(p.status in (STATUS_OK, STATUS_SIDE_RULE) for p in result.photos)
    assert result.warnings == []


def test_folder_with_duplicate_missing_and_foreign(model, tmp_path):
    # кулер снят дважды, HDMI нет, есть посторонний снимок
    shots = [(7, 0), (7, 0), (1, 0), (2, 0), (None, 0), (3, 0), (4, 0), (9, 0), (10, 0)]
    names = make_target(tmp_path / "SN002", shots)
    result = match_folder(model, str(tmp_path / "SN002"), names, MatchConfig(strict_counts=False))
    got = [p.new_name for p in result.photos]
    # дубль кулера: либо «Кулер 2», либо не переименован, но «Кулер» — первый кандидат
    assert got[1] == "Кулер 2.JPG" or (got[1] is None and result.photos[1].candidates[0].category == "Кулер")
    assert got[:1] + got[2:] == [
        "Кулер.JPG",
        "Процессор 1 фото 1.JPG",
        "Процессор 1 фото 2.JPG",
        None,
        "Материнская плата фото 1.JPG",
        "Материнская плата фото 2.JPG",
        "Кабель DP.JPG",
        "Блок питания.JPG",
    ]
    assert not result.photos[4].identified          # посторонний снимок не переименован
    assert len(result.photos[4].candidates) == 3
    assert "Кабель HDMI" not in result.category_counts()


def test_never_wrong_name_under_heavy_duplication(model, tmp_path):
    """Состав сильно отличается от эталона (по 25 одинаковых деталей). Неопознанные допустимы,
    неверная категория — нет."""
    seeds = {7: "Кулер", 8: "Кабель HDMI", 9: "Кабель DP", 10: "Блок питания"}
    shots = [(s, 0) for s in seeds] * 25  # 100 фото
    names = make_target(tmp_path / "SN003", shots)
    progress = []
    result = match_folder(model, str(tmp_path / "SN003"), names, MatchConfig(strict_counts=False),
                          on_progress=lambda d, t: progress.append(d))
    assert progress[-1] == 100
    identified = [p for p in result.photos if p.identified]
    wrong = [(p.filename, p.category.name) for p in identified if p.category.name != seeds[shots[p.index][0]]]
    assert wrong == []
    assert len(identified) >= 80
