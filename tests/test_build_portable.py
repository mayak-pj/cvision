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


def _qt_wheels(wheels: Path, skip: str = "") -> None:
    """Поддельные колёса PyQt5 с теми файлами, без которых новые окна не откроются."""
    files = {"PyQt5-5.15.10-cp37-abi3-win_amd64.whl": [n for n in build_portable.QT_FILES if n.endswith(".pyd")],
             "PyQt5_Qt5-5.15.2-py3-none-win_amd64.whl": [n for n in build_portable.QT_FILES if not n.endswith(".pyd")],
             "PyQt5_sip-12.13.0-cp38-cp38-win_amd64.whl": ["PyQt5/sip.cp38-win_amd64.pyd"]}
    for wheel, names in files.items():
        with zipfile.ZipFile(wheels / wheel, "w") as archive:
            for name in names:
                if name != skip:
                    archive.writestr(name, "\x00")


def test_build_with_qt_has_its_files_and_launcher(tmp_path, parts, capsys):
    embed, wheels, tk_from = parts
    _qt_wheels(wheels)
    assert build_portable.wrong_platform_wheels(wheels) == []     # abi3 и py3-none-win_amd64 годятся
    assert "Новые окна (PyQt5): есть" in build_portable.plan(embed, wheels, tk_from, tmp_path / "out")
    out = tmp_path / "CV"
    build_portable.build(embed, wheels, tk_from, out, ROOT)
    assert "Новые окна (PyQt5): есть" in capsys.readouterr().out
    assert (out / "python" / "Lib" / "site-packages" / "PyQt5" / "Qt5" / "plugins" / "platforms" / "qwindows.dll").is_file()
    launcher = (out / "CV (новые окна).bat").read_text(encoding="cp866")
    assert "pythonw.exe" in launcher and "run.py\" --qt" in launcher


def test_build_with_broken_qt_is_an_error(tmp_path, parts):
    embed, wheels, tk_from = parts
    _qt_wheels(wheels, skip="PyQt5/Qt5/plugins/platforms/qwindows.dll")
    with pytest.raises(build_portable.BuildError, match="qwindows"):
        build_portable.build(embed, wheels, tk_from, tmp_path / "CV", ROOT)


def test_build_without_qt_says_only_old_windows(tmp_path, parts, capsys):
    embed, wheels, tk_from = parts
    assert "НЕТ колеса PyQt5-Qt5" in build_portable.plan(embed, wheels, tk_from, tmp_path / "out")
    build_portable.build(embed, wheels, tk_from, tmp_path / "CV", ROOT)
    assert "только прежние окна" in capsys.readouterr().out


@pytest.mark.parametrize("name, fits", [
    ("PyQt5-5.15.10-cp37-abi3-win_amd64.whl", True),
    ("PyQt5_Qt5-5.15.2-py3-none-win_amd64.whl", True),
    ("six-1.16.0-py2.py3-none-any.whl", True),
    ("numpy-1.24.4-cp38-cp38-win_amd64.whl", True),
    ("numpy-1.24.4-cp39-cp39-win_amd64.whl", False),
    ("PyQt5-5.15.10-cp39-abi3-win_amd64.whl", False),          # abi3 от более новой версии Python
    ("PyQt5_Qt5-5.15.19-py3-none-macosx_11_0_arm64.whl", False),
    ("PyQt5-5.15.10-cp37-abi3-win32.whl", False),
])
def test_wheel_fits_python38_windows64(name, fits):
    assert build_portable.wheel_fits(name) is fits


def _download_wheels():
    spec2 = importlib.util.spec_from_file_location("download_wheels", ROOT / "dist_offline" / "download_wheels.py")
    module = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(module)
    return module


def test_download_list_is_chosen_for_windows_not_for_this_machine(tmp_path):
    """pip проверяет условия по своей машине: на Mac он взял бы PyQt5-Qt5 для Mac, которого для Windows нет."""
    module = _download_wheels()
    lines = module.target_lines(ROOT / "requirements.txt")
    assert "PyQt5-Qt5==5.15.2" in lines and "PyQt5-Qt5==5.15.19" not in lines
    assert "PyQt5==5.15.10" in lines and not any(";" in line or "#" in line for line in lines)
    target = module.write_target_requirements(ROOT / "requirements.txt", tmp_path)
    assert "-r %s" % target in " ".join(module.command(target, tmp_path))


def test_download_marker_rules(tmp_path):
    module = _download_wheels()
    assert module.marker_holds('sys_platform != "darwin"')
    assert not module.marker_holds('sys_platform == "darwin"')
    assert module.marker_holds('sys_platform == "win32" and python_version == "3.8"')
    with pytest.raises(ValueError):
        module.marker_holds('python_version >= "3.8"')          # не понимает — лучше остановиться, чем взять не то
    requirements = tmp_path / "requirements.txt"
    requirements.write_text('a==1\nb==2; sys_platform == "darwin"  # только Mac\n-r more.txt\n', encoding="utf-8")
    (tmp_path / "more.txt").write_text("c==3\n", encoding="utf-8")
    assert module.target_lines(requirements) == ["a==1", "c==3"]


def _check_install():
    spec3 = importlib.util.spec_from_file_location("check_install", ROOT / "dist_offline" / "check_install.py")
    module = importlib.util.module_from_spec(spec3)
    spec3.loader.exec_module(module)
    return module


def test_qt_failure_is_explained_and_old_windows_named(tmp_path, monkeypatch):
    """Qt обрывает процесс без сообщения. Проверка должна сказать, что прежние окна работают,
    и сохранить подробный вывод Qt о поиске модулей."""
    pytest.importorskip("PyQt5")
    module = _check_install()
    monkeypatch.setattr(module, "HERE", tmp_path)
    calls = []

    class Done:
        def __init__(self, code, out, err=b""):
            self.returncode, self.stdout, self.stderr = code, out, err

    def run(command, **kwargs):
        calls.append(kwargs["env"])
        if len(calls) == 1:
            return Done(3221226505, b"", "qt.qpa.plugin: Could not find the Qt platform plugin \"windows\"".encode())
        return Done(1, b"QFactoryLoader::QFactoryLoader() checking directory path ...")

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    with pytest.raises(RuntimeError) as error:
        module.qt_window()
    text = str(error.value)
    assert "Прежние окна работают" in text and "platform plugin" in text
    assert "QT_QPA_PLATFORM" not in calls[0]                  # настоящий оконный модуль, не offscreen
    assert calls[1].get("QT_DEBUG_PLUGINS") == "1"
    assert "checking directory" in (tmp_path / "logs" / "проверка_qt.txt").read_text(encoding="utf-8")


def test_qt_probe_success_line(tmp_path, monkeypatch):
    pytest.importorskip("PyQt5")
    module = _check_install()

    class Done:
        returncode, stdout, stderr = 0, "строка\nQt 5.15.2, модуль экрана windows".encode(), b""

    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: Done())
    assert module.qt_window() == "Qt 5.15.2, модуль экрана windows"
