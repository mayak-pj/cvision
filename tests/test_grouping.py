"""Группировка папок проекта и поиск готовых эталонов."""

import os

import numpy as np

from photo_renamer import grouping
from photo_renamer.features import FG_HIST, HOG, LBP, FeatureExtractor
from synthetic import make_part, perturb, save_jpeg

W = {FG_HIST: 2, LBP: 1, HOG: 1}


def shoot(folder, seeds, names=None, tint=None):
    os.makedirs(str(folder), exist_ok=True)
    for k, s in enumerate(seeds):
        img = perturb(make_part(s), seed=hash((str(folder), k)) % 1000)
        if tint is not None:  # другое освещение
            arr = np.asarray(img, dtype=np.float32) * np.array(tint)
            from PIL import Image
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        name = names[k] if names else "IMG_%04d.JPG" % k
        save_jpeg(img, folder / name)


def test_cluster_and_make_groups():
    d = np.array([[0, .1, .5, .5], [.1, 0, .5, .5], [.5, .5, 0, .1], [.5, .5, .1, 0]], dtype=float)
    assert grouping.cluster(d, 0.3) == [0, 0, 1, 1]
    assert grouping.cluster(d, 0.6) == [0, 0, 0, 0]
    assert grouping.cluster(np.zeros((1, 1)), 0.3) == [0]


def test_analyze_project_groups_and_references(tmp_path):
    root = tmp_path / "Проект"
    parts_a = [1, 2, 3, 4, 5, 6]
    parts_b = [11, 12, 13, 14, 15, 16, 17]
    ref_names = ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "Кабель HDMI.JPG",
                 "Радиатор 1.JPG", "Радиатор 2.JPG"]
    shoot(root / "A" / "эталон", parts_a, names=ref_names)
    shoot(root / "A" / "SN2", parts_a)
    shoot(root / "A" / "SN3", parts_a[:5])
    shoot(root / "B" / "SN4", parts_b)
    shoot(root / "B" / "SN5", parts_b + [18])
    (root / "B" / "SN5" / "Рентгенограмма.jpg").write_bytes(b"x")
    (root / "B" / "SN5" / "scan.tif").write_bytes(b"x")

    analysis = grouping.analyze_project([str(root)], FeatureExtractor(), W, threshold=0.30)
    by_name = {os.path.basename(f.path): f for f in analysis.folders}
    assert set(by_name) == {"эталон", "SN2", "SN3", "SN4", "SN5"}
    assert by_name["SN5"].count == 8               # рентгенограмма и .tif не считаются
    assert by_name["эталон"].is_reference and not by_name["SN2"].is_reference

    groups = sorted((sorted(os.path.basename(f.path) for f in g.folders), g) for g in analysis.groups)
    assert [names for names, _ in groups] == [["SN2", "SN3", "эталон"], ["SN4", "SN5"]]
    g_a, g_b = groups[0][1], groups[1][1]
    assert os.path.basename(g_a.reference.path) == "эталон" and g_a.reference_is_ready
    assert os.path.basename(g_b.reference.path) == "SN5" and not g_b.reference_is_ready  # больше всего фото
    assert {os.path.basename(f.path) for f in g_a.targets} == {"SN2", "SN3"}
    assert "Группа" in grouping.describe(analysis)


def test_single_folder_and_errors(tmp_path):
    shoot(tmp_path / "SN1", [1, 2])
    analysis = grouping.analyze_project([str(tmp_path / "SN1"), str(tmp_path / "нет")], FeatureExtractor(), W)
    assert len(analysis.groups) == 1 and len(analysis.errors) == 1


def test_generic_numbering_is_not_a_reference():
    from photo_renamer.scanner import FolderScan

    generic = grouping.folder_info(FolderScan("/x", ["File %d.JPG" % i for i in range(1, 191)]))
    assert generic.named_share == 1.0 and generic.categories == 1 and not generic.is_reference
    real = grouping.folder_info(FolderScan("/y", ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG",
                                                  "Кабель HDMI.JPG"]))
    assert real.is_reference
