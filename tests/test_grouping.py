"""Группировка папок проекта и поиск готовых эталонов."""

import os
import zlib

import numpy as np

from photo_renamer import grouping
from photo_renamer.features import FG_HIST, HOG, LBP, FeatureExtractor
from synthetic import make_part, perturb, save_jpeg

W = {FG_HIST: 2, LBP: 1, HOG: 1}


def shoot(folder, seeds, names=None, tint=None):
    """Снимает «папку»: те же детали, но каждый раз с другими искажениями.
    Seed устойчив между запусками (встроенная hash() меняется из-за рандомизации)."""
    os.makedirs(str(folder), exist_ok=True)
    for k, s in enumerate(seeds):
        img = perturb(make_part(s), seed=zlib.crc32(("%s|%d" % (os.path.basename(str(folder)), k)).encode()) % 1000)
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


def test_service_names_are_not_a_reference():
    """Случай с рабочей машины: папка из файлов «file (1)»…«file (17)» не переименована.

    Раньше каждое такое имя считалось названным по шаблону, причём с собственной категорией,
    поэтому папка получала статус «уже названа по шаблону» и выпадала из списка на переименование
    («В этой группе нет папок для переименования»).
    """
    from photo_renamer.scanner import FolderScan

    info = grouping.folder_info(FolderScan("/x", ["file (%d).JPG" % i for i in range(1, 18)]))
    assert info.named_share == 0.0 and info.categories == 0 and not info.is_reference


def test_folder_with_service_names_stays_a_target(tmp_path):
    """И такая папка остаётся целью переименования рядом с готовым эталоном."""
    shoot(tmp_path / "эталон", [1, 2, 3], names=["Кулер.JPG", "Процессор 1 фото 1.JPG", "Кабель HDMI.JPG"])
    shoot(tmp_path / "SN2", [1, 2, 3], names=["file (1).JPG", "file (2).JPG", "file (3).JPG"])
    analysis = grouping.analyze_project([str(tmp_path)], FeatureExtractor(), W)
    group = analysis.groups[0]
    assert group.reference_is_ready and os.path.basename(group.reference.path) == "эталон"
    assert [os.path.basename(f.path) for f in group.targets] == ["SN2"]


def _fake_analysis(tmp_path):
    """Анализ без фото: три папки, две группы, заданная матрица несходства."""
    from photo_renamer.scanner import FolderScan

    infos = []
    for name, photos in [("A", ["IMG_%d.JPG" % i for i in range(5)]),
                         ("B", ["IMG_%d.JPG" % i for i in range(7)]),
                         ("C", ["IMG_%d.JPG" % i for i in range(3)])]:
        infos.append(grouping.folder_info(FolderScan(str(tmp_path / name), photos)))
    d = np.array([[0, .2, .5], [.2, 0, .5], [.5, .5, 0]])
    groups = grouping.make_groups(infos, grouping.cluster(d, 0.3))
    return grouping.ProjectAnalysis(str(tmp_path), infos, groups, d), infos


def test_move_folder_and_references(tmp_path):
    analysis, (a, b, c) = _fake_analysis(tmp_path)
    assert [len(g.folders) for g in analysis.groups] == [2, 1]
    assert analysis.groups[0].reference is b                     # больше всего фото
    assert grouping.similarity_label(grouping.similarity_to_reference(analysis, a)) == "высокое"
    assert grouping.similarity_to_reference(analysis, b) is None

    grouping.set_reference(analysis, a)
    assert analysis.groups[0].reference is a and analysis.groups[0].reference_manual

    k = grouping.move_folder(analysis, c, 0)                     # C в первую группу
    assert k == 0 and len(analysis.groups) == 1                  # вторая группа опустела и удалена
    assert analysis.groups[0].reference is a                     # ручной выбор сохранился
    assert grouping.similarity_label(grouping.similarity_to_reference(analysis, c)) == "низкое"

    k = grouping.move_folder(analysis, a, None)                  # эталон ушёл в новую группу
    assert k == 1 and analysis.groups[1].folders == [a]
    assert analysis.groups[0].reference is b and not analysis.groups[0].reference_manual


def test_refresh_names_after_reference_created(tmp_path):
    shoot(tmp_path / "SN1", [1, 2, 3, 4])
    shoot(tmp_path / "SN2", [1, 2, 3, 4, 5])
    analysis = grouping.analyze_project([str(tmp_path)], FeatureExtractor(), W)
    group = analysis.groups[0]
    assert len(analysis.groups) == 1 and not group.reference_is_ready
    folder = group.reference
    new_names = ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "Кабель HDMI.JPG", "Радиатор.JPG"]
    for old, new in zip(sorted(os.listdir(folder.path)), new_names):
        os.rename(os.path.join(folder.path, old), os.path.join(folder.path, new))
    grouping.refresh_names(analysis)
    assert group.reference is folder and group.reference_is_ready
