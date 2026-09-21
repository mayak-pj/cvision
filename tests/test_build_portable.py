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
    for name, _ in build_portable.TK_FILES:
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
    out = tmp_path / "Переименование фото"
    build_portable.build(embed, wheels, tk_from, out, ROOT)

    python_dir = out / "python"
    assert (python_dir / "python.exe").exists()
    pth = (python_dir / "python38._pth").read_text(encoding="ascii")
    assert "import site" in pth and "..\\src" in pth and not pth.strip().startswith("#import")

    site_packages = python_dir / "Lib" / "site-packages"
    assert (site_packages / "numpy" / "__init__.py").exists()
    assert (site_packages / "PIL" / "__init__.py").exists()
    assert (site_packages / "scipy" / "__init__.py").exists()

    assert (python_dir / "_tkinter.pyd").exists() and (python_dir / "tcl" / "init.tcl").exists()
    assert (python_dir / "Lib" / "tkinter" / "init.tcl").exists()

    assert (out / "run.py").exists() and (out / "src" / "photo_renamer" / "matcher.py").exists()
    assert not list((out / "src").rglob("__pycache__"))
    assert (out / "config.example.json").exists() and (out / "names.example.txt").exists()
    assert (out / "Переименование фото.bat").exists()
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
