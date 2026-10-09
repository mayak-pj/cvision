"""Сборщик портативного дистрибутива: проверки входных данных и состав готовой папки.

Настоящий Python для Windows здесь не используется — вместо него подделки такой же структуры,
поэтому тест работает на любой системе.
"""

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_portable", ROOT / "dist_offline" / "build_portable.py")
build_portable = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_portable)


@pytest.fixture
def parts(tmp_path):
    """Поддельные: архив embeddable Python, папка колёс, установка Python с tkinter."""
    embed = tmp_path / "python-3.8.10-embed-amd64.zip"
    with zipfile.ZipFile(embed, "w") as archive:
        archive.writestr("python.exe", "")
        archive.writestr("pythonw.exe", "")
        archive.writestr("python38.zip", "")
        archive.writestr("python38._pth", "python38.zip\n.\n#import site\n")

    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for name, payload in (("numpy-1.24.4-cp38-cp38-win_amd64.whl", "numpy/__init__.py"),
                          ("pillow-10.4.0-cp38-cp38-win_amd64.whl", "PIL/__init__.py"),
                          ("scipy-1.10.1-cp38-cp38-win_amd64.whl", "scipy/__init__.py")):
        with zipfile.ZipFile(wheels / name, "w") as archive:
            archive.writestr(payload, "# заглушка\n")

    tk_from = tmp_path / "Python38"
    for name, _, _required in build_portable.TK_FILES:
        path = tk_from / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
    for name, _ in build_portable.TK_DIRS:
        (tk_from / name).mkdir(parents=True, exist_ok=True)
        (tk_from / name / "init.tcl").write_text("", encoding="utf-8")
    return embed, wheels, tk_from


def test_check_inputs(tmp_path, parts):
    embed, wheels, tk_from = parts
    build_portable.check_inputs(embed, wheels, tk_from)

    with pytest.raises(build_portable.BuildError, match="embeddable"):
        build_portable.check_inputs(tmp_path / "нет.zip", wheels, tk_from)
    empty = tmp_path / "пусто"
    empty.mkdir()
    with pytest.raises(build_portable.BuildError, match="whl"):
        build_portable.check_inputs(embed, empty, tk_from)
    (tk_from / "DLLs" / "_tkinter.pyd").unlink()
    with pytest.raises(build_portable.BuildError, match="_tkinter"):
        build_portable.check_inputs(embed, wheels, tk_from)


def test_wrong_platform_wheels_detected(tmp_path, parts):
    embed, wheels, tk_from = parts
    with zipfile.ZipFile(wheels / "numpy-1.24.4-cp311-cp311-macosx_11_0_arm64.whl", "w") as archive:
        archive.writestr("numpy/__init__.py", "")
    with zipfile.ZipFile(wheels / "six-1.16.0-py2.py3-none-any.whl", "w") as archive:
        archive.writestr("six.py", "")          # не зависит от платформы — это нормально
    bad = build_portable.wrong_platform_wheels(wheels)
    assert bad == ["numpy-1.24.4-cp311-cp311-macosx_11_0_arm64.whl"]
    assert "ВНИМАНИЕ" in build_portable.plan(embed, wheels, tk_from, tmp_path / "out")


def test_build_creates_distribution(tmp_path, parts):
    embed, wheels, tk_from = parts
    out = tmp_path / "CV"
    build_portable.build(embed, wheels, tk_from, out, ROOT)

    python_dir = out / "python"
    assert (python_dir / "python.exe").exists()
    pth = (python_dir / "python38._pth").read_text(encoding="ascii")
    lines = pth.split()
    assert "import" in pth and "..\\src" in lines and not pth.strip().startswith("#import")
    # без строки «Lib» не находится tkinter: окно не запускается
    assert "Lib" in lines and "Lib\\site-packages" in lines

    site_packages = python_dir / "Lib" / "site-packages"
    assert (site_packages / "numpy" / "__init__.py").exists()
    assert (site_packages / "PIL" / "__init__.py").exists()
    assert (site_packages / "scipy" / "__init__.py").exists()

    # проверку запускают ярлыком: иначе её запускают чужим Python и получают «No module named numpy»
    check = (out / "Проверка сборки.bat").read_text(encoding="cp866")
    assert "python\\python.exe" in check and "check_install.py" in check

    # раскладка как в обычной установке Python: DLLs\ рядом с tcl\ — тогда Tcl находит файлы сам
    assert (python_dir / "DLLs" / "_tkinter.pyd").exists()
    assert (python_dir / "DLLs" / "tcl86t.dll").exists()
    assert (python_dir / "tcl" / "init.tcl").exists()
    assert (python_dir / "Lib" / "tkinter" / "init.tcl").exists()
    assert "DLLs" in pth.split()

    assert (out / "run.py").exists() and (out / "src" / "photo_renamer" / "matcher.py").exists()
    assert not list((out / "src").rglob("__pycache__"))
    assert (out / "config.example.json").exists() and (out / "names.example.txt").exists()
    assert (out / "CV.bat").exists()
    assert (out / "Запуск с консолью (для ошибок).bat").exists()
    assert (out / "Инструкция.txt").exists() and (out / "check_install.py").exists()
    assert (out / "logs").is_dir() and (out / "cache").is_dir()


def test_build_without_tkinter_warns(tmp_path, parts):
    embed, wheels, _ = parts
    text = build_portable.plan(embed, wheels, None, tmp_path / "out")
    assert "НЕ КОПИРУЮТСЯ" in text
    build_portable.build(embed, wheels, None, tmp_path / "out", ROOT)
    assert not (tmp_path / "out" / "python" / "_tkinter.pyd").exists()


def test_dry_run_writes_nothing(tmp_path, parts, capsys):
    embed, wheels, tk_from = parts
    out = tmp_path / "out"
    code = build_portable.main(["--embed", str(embed), "--wheels", str(wheels), "--tk-from", str(tk_from),
                                "--out", str(out), "--project", str(ROOT), "--dry-run"])
    assert code == 0 and not out.exists()
    assert "dry-run" in capsys.readouterr().out


def test_download_command_targets_windows_python38(tmp_path):
    spec2 = importlib.util.spec_from_file_location("download_wheels", ROOT / "dist_offline" / "download_wheels.py")
    module = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(module)
    line = " ".join(module.command(ROOT / "requirements.txt", tmp_path))
    assert "--only-binary=:all:" in line
    assert "--python-version 38" in line and "--platform win_amd64" in line


def test_console_output_survives_limited_encoding(tmp_path):
    """Русский текст не должен ронять скрипты в консоли без кириллицы (cp1252 в CI, cp866 на Windows)."""
    import io
    import sys

    from photo_renamer import console

    class Cp1252Stream(io.TextIOWrapper):
        pass

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", newline="")
    original = sys.stdout
    sys.stdout = stream
    try:
        console.setup()
        print("Скачано файлов: 3")          # без настройки здесь было бы UnicodeEncodeError
        stream.flush()
    finally:
        sys.stdout = original
    assert b"?" in buffer.getvalue()        # кириллица заменена, программа не упала


def test_optional_zlib_is_not_required(tmp_path, parts):
    """zlib1.dll есть не во всех сборках Python (например, его нет у раннеров GitHub),
    и tkinter там работает без него."""
    embed, wheels, tk_from = parts
    (tk_from / "DLLs" / "zlib1.dll").unlink()
    build_portable.check_inputs(embed, wheels, tk_from)          # не ошибка
    assert "необязательных файлов нет" in build_portable.plan(embed, wheels, tk_from, tmp_path / "out")

    out = tmp_path / "out"
    build_portable.build(embed, wheels, tk_from, out, ROOT)
    assert (out / "python" / "DLLs" / "_tkinter.pyd").exists()
    assert not (out / "python" / "DLLs" / "zlib1.dll").exists()


def test_missing_required_tk_file_is_an_error(tmp_path, parts):
    embed, wheels, tk_from = parts
    (tk_from / "DLLs" / "tk86t.dll").unlink()
    with pytest.raises(build_portable.BuildError, match="tk86t"):
        build_portable.check_inputs(embed, wheels, tk_from)


def test_wheel_data_is_moved_into_site_packages(tmp_path):
    """Колесо onnxruntime кладёт пакет в «*.data/purelib», и pip переносит это в site-packages.

    Распаковка обязана делать то же самое: иначе «import onnxruntime» не работает, а сборка
    при этом выглядит исправной (так и случилось на первой сборке с CLIP).
    """
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    with zipfile.ZipFile(wheels / "onnxruntime-1.11.1-cp38-cp38-win_amd64.whl", "w") as archive:
        archive.writestr("onnxruntime-1.11.1.data/purelib/onnxruntime/__init__.py", "# заглушка\n")
        archive.writestr("onnxruntime-1.11.1.data/purelib/onnxruntime/capi/state.pyd", "\x00")
        archive.writestr("onnxruntime-1.11.1.dist-info/METADATA", "Name: onnxruntime\n")
    with zipfile.ZipFile(wheels / "numpy-1.24.4-cp38-cp38-win_amd64.whl", "w") as archive:
        archive.writestr("numpy/__init__.py", "# заглушка\n")

    site_packages = tmp_path / "site-packages"
    assert build_portable.extract_wheels(wheels, site_packages) == 2
    assert (site_packages / "onnxruntime" / "__init__.py").is_file()
    assert (site_packages / "onnxruntime" / "capi" / "state.pyd").is_file()
    assert (site_packages / "numpy" / "__init__.py").is_file()
    assert not (site_packages / "onnxruntime-1.11.1.data" / "purelib").exists()


def test_vcruntime_taken_from_wheel_and_from_python(tmp_path):
    """msvcp140.dll берётся из колеса msvc-runtime, vcruntime140.dll уже есть во встраиваемом Python."""
    python_dir = tmp_path / "python"
    python_dir.mkdir()
    (python_dir / "vcruntime140.dll").write_bytes(b"\x00")
    (python_dir / "vcruntime140_1.dll").write_bytes(b"\x00")
    data = python_dir / "Lib" / "site-packages" / "msvc_runtime-14.29.30133.data" / "data"
    data.mkdir(parents=True)
    for name in ("msvcp140.dll", "concrt140.dll"):
        (data / name).write_bytes(b"\x00")

    copied, missing = build_portable.install_vcruntime(python_dir, python_dir / "Lib" / "site-packages")
    assert missing == []
    assert copied["vcruntime140.dll"] == "уже в Python"
    assert copied["msvcp140.dll"] == "из колеса msvc-runtime"
    assert (python_dir / "msvcp140.dll").is_file()


def test_missing_vcruntime_is_reported(tmp_path):
    """Без msvcp140.dll onnxruntime не загрузится, и сборщик обязан об этом сказать."""
    python_dir = tmp_path / "python"
    (python_dir / "Lib" / "site-packages").mkdir(parents=True)
    copied, missing = build_portable.install_vcruntime(python_dir, python_dir / "Lib" / "site-packages")
    assert copied == {}
    assert "msvcp140.dll" in missing
