"""Сборка портативного дистрибутива: раскладка колёс и библиотек Visual C++."""

import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist_offline"))

import build_portable as build  # noqa: E402


def make_wheel(folder, name, entries):
    path = folder / name
    with zipfile.ZipFile(path, "w") as archive:
        for entry, text in entries.items():
            archive.writestr(entry, text)
    return path


def test_wheel_data_is_moved_into_site_packages(tmp_path):
    """Колесо onnxruntime кладёт пакет в «*.data/purelib»: pip переносит это в site-packages,
    и распаковка обязана делать то же самое, иначе «import onnxruntime» не работает."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    make_wheel(wheels, "onnxruntime-1.11.1-cp38-cp38-win_amd64.whl", {
        "onnxruntime-1.11.1.data/purelib/onnxruntime/__init__.py": "версия",
        "onnxruntime-1.11.1.data/purelib/onnxruntime/capi/state.pyd": "двоичное",
        "onnxruntime-1.11.1.dist-info/METADATA": "имя",
    })
    make_wheel(wheels, "numpy-1.24.4-cp38-cp38-win_amd64.whl", {"numpy/__init__.py": "версия"})

    site_packages = tmp_path / "site-packages"
    assert build.extract_wheels(wheels, site_packages) == 2
    assert (site_packages / "onnxruntime" / "__init__.py").is_file()
    assert (site_packages / "onnxruntime" / "capi" / "state.pyd").is_file()
    assert (site_packages / "numpy" / "__init__.py").is_file()
    assert not (site_packages / "onnxruntime-1.11.1.data" / "purelib").exists()


def test_vcruntime_taken_from_wheel_and_from_python(tmp_path):
    """msvcp140.dll берётся из колеса msvc-runtime, vcruntime140.dll уже есть во встраиваемом Python."""
    python_dir = tmp_path / "python"
    (python_dir).mkdir()
    (python_dir / "vcruntime140.dll").write_text("уже есть", encoding="utf-8")
    site_packages = python_dir / "Lib" / "site-packages" / "msvc_runtime-14.29.30133.data" / "data"
    site_packages.mkdir(parents=True)
    for name in ("msvcp140.dll", "vcruntime140_1.dll", "concrt140.dll"):
        (site_packages / name).write_text("из колеса", encoding="utf-8")

    copied, missing = build.install_vcruntime(python_dir, python_dir / "Lib" / "site-packages")
    assert missing == []
    assert copied["vcruntime140.dll"] == "уже в Python"
    assert copied["msvcp140.dll"] == "из колеса msvc-runtime"
    assert (python_dir / "msvcp140.dll").is_file()


def test_missing_vcruntime_is_reported(tmp_path):
    """Если библиотек нет, сборщик обязан сказать об этом: иначе onnxruntime молча не загрузится."""
    python_dir = tmp_path / "python"
    python_dir.mkdir()
    (python_dir / "Lib" / "site-packages").mkdir(parents=True)
    copied, missing = build.install_vcruntime(python_dir, python_dir / "Lib" / "site-packages")
    assert copied == {}
    assert "msvcp140.dll" in missing
