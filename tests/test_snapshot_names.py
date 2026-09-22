"""Снимок имён и восстановление по нему (страховка на время испытаний)."""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("snapshot_names", ROOT / "tools" / "snapshot_names.py")
snapshot_names = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot_names)


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "Проект" / "SN1"
    folder.mkdir(parents=True)
    for name in ("IMG_001.JPG", "IMG_002.JPG", "IMG_003.JPG"):
        (folder / name).write_bytes(("содержимое " + name).encode("utf-8"))
    (tmp_path / "Проект" / ".DS_Store").write_bytes(b"x")
    return tmp_path / "Проект", folder


def test_save_check_restore(project, tmp_path, capsys):
    root, folder = project
    snapshot = tmp_path / "снимок.csv"
    assert snapshot_names.save(root, snapshot) == 0
    assert "файлов: 3" in capsys.readouterr().out          # служебный файл macOS не учтён

    os.rename(str(folder / "IMG_001.JPG"), str(folder / "Кулер.JPG"))
    os.rename(str(folder / "IMG_002.JPG"), str(folder / "Процессор 1 фото 1.JPG"))
    changed, _, _ = snapshot_names.differences(root, snapshot)
    assert len(changed) == 2

    snapshot_names.restore(root, snapshot, dry_run=True)
    assert (folder / "Кулер.JPG").exists()                 # при --dry-run ничего не менялось

    assert snapshot_names.restore(root, snapshot, dry_run=False) == 0
    assert sorted(os.listdir(str(folder))) == ["IMG_001.JPG", "IMG_002.JPG", "IMG_003.JPG"]
    assert snapshot_names.restore(root, snapshot, dry_run=False) == 0   # повторно — нечего делать


def test_restore_after_several_renames(project, tmp_path):
    """Файл узнаётся по содержимому, поэтому цепочка переименований не мешает."""
    root, folder = project
    snapshot = tmp_path / "снимок.csv"
    snapshot_names.save(root, snapshot)
    for old, new in (("IMG_003.JPG", "Радиатор 1.JPG"), ("Радиатор 1.JPG", "Радиатор 2.JPG")):
        os.rename(str(folder / old), str(folder / new))
    snapshot_names.restore(root, snapshot, dry_run=False)
    assert (folder / "IMG_003.JPG").exists()


def test_occupied_name_is_reported(project, tmp_path, capsys):
    root, folder = project
    snapshot = tmp_path / "снимок.csv"
    snapshot_names.save(root, snapshot)
    os.rename(str(folder / "IMG_001.JPG"), str(folder / "Кулер.JPG"))
    (folder / "IMG_001.JPG").write_text("кто-то занял имя", encoding="utf-8")
    assert snapshot_names.restore(root, snapshot, dry_run=False) == 1
    assert "имя занято" in capsys.readouterr().out
    assert (folder / "Кулер.JPG").exists()                 # файл не потерян
