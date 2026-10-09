"""Проверка портативной сборки. Запускается ИМЕННО встроенным Python дистрибутива:

    python\\python.exe check_install.py

Проверяет то, что обычно ломается на рабочей машине без интернета и без прав администратора:
версию Windows и Python, наличие библиотек, работу окна (tkinter), чтение JPEG, запись в logs
и cache, работу модели CLIP (onnxruntime) с замером скорости, а также сам алгоритм
на маленьком проверочном изображении.
"""

import os
import platform
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
# в собранном дистрибутиве код лежит рядом (HERE/src), в репозитории — на уровень выше
for candidate in (HERE / "src", HERE.parent / "src"):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))
        break

problems = []
notes = []


def check(title, function):
    try:
        result = function()
    except Exception as exc:
        problems.append("%s: %s" % (title, exc))
        print("[ОШИБКА] %-34s %s" % (title, exc))
        if os.environ.get("PHOTO_RENAMER_DEBUG"):
            traceback.print_exc()
    else:
        print("[ ок   ] %-34s %s" % (title, result if result is not None else ""))


def own_python():
    """Проверка запущена встроенным Python дистрибутива, а не каким-то другим.

    Частая ошибка: берут отдельно скачанный embeddable Python, в котором нет ни библиотек,
    ни tkinter, и получают непонятное «No module named numpy».
    """
    bundled = HERE / "python" / ("python.exe" if os.name == "nt" else "python")
    if not bundled.exists():
        return "рядом нет папки python — проверка идёт тем Python, которым запущена"
    if Path(sys.executable).resolve() != bundled.resolve():
        raise RuntimeError('запустите проверку встроенным Python дистрибутива:\n'
                           '            "%s" "%s"\n'
                           '            сейчас она запущена из %s' % (bundled, HERE / "check_install.py",
                                                                      sys.executable))
    return "встроенный Python дистрибутива"


def windows_version():
    if os.name != "nt":
        notes.append("Проверка запущена не на Windows: часть проверок ничего не докажет.")
        return "%s (не Windows)" % platform.platform()
    version = sys.getwindowsversion()
    if (version.major, version.minor) < (6, 1):
        raise RuntimeError("нужна Windows 7 SP1 или новее")
    return platform.platform()


def python_version():
    if sys.version_info[:2] != (3, 8):
        raise RuntimeError("ожидался Python 3.8, запущен %s" % sys.version.split()[0])
    return "%s, %d бит" % (sys.version.split()[0], 64 if sys.maxsize > 2 ** 32 else 32)


def libraries():
    import numpy
    import PIL
    import scipy
    from scipy.optimize import linear_sum_assignment

    assert linear_sum_assignment is not None
    return "numpy %s, scipy %s, Pillow %s" % (numpy.__version__, scipy.__version__, PIL.__version__)


def window():
    from photo_renamer.gui import theme  # находит библиотеки Tcl/Tk рядом с python.exe

    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    version = root.tk.call("info", "patchlevel")
    theme.apply(root, "light")
    root.destroy()
    return "Tk %s, светлое оформление применяется" % version


def jpeg_and_features():
    import numpy
    from PIL import Image

    from photo_renamer import features as feat

    image = Image.fromarray((numpy.random.RandomState(0).rand(240, 320, 3) * 255).astype("uint8"))
    sample = HERE / "cache" / "проверка.jpg"
    sample.parent.mkdir(exist_ok=True)
    image.save(sample, "JPEG", quality=90)
    extractor = feat.FeatureExtractor()
    first = extractor.from_file(str(sample))
    second = extractor.from_file(str(sample))
    distance = extractor.distance_components([first], [second])[feat.FG_HIST][0, 0]
    sample.unlink()
    if distance > 1e-6:
        raise RuntimeError("признаки одного и того же фото различаются")
    return "чтение JPEG и расчёт признаков работают"


def clip_model():
    """Главная проверка для распознавания: работает ли onnxruntime и грузится ли модель CLIP.

    Без неё программа тоже работает, но заметно хуже: обычные признаки путаются, когда
    свет или камера отличаются от эталонных. Поэтому это не ошибка, а замечание.
    """
    import time

    import numpy
    from PIL import Image

    from photo_renamer import clipfeat, config as cfg

    error = clipfeat.runtime_error()
    if error:
        notes.append("onnxruntime не загрузился — CLIP не используется, распознавание будет слабее.\n"
                     "            %s\n"
                     "            Если это «DLL load failed», рядом с python.exe не хватает библиотек "
                     "Visual C++ (msvcp140.dll, vcruntime140_1.dll) — пересоберите дистрибутив." % error)
        return "недоступен: %s" % error[:80]
    config, _ = cfg.load_config()
    path = config.clip_path()
    if not os.path.isfile(path):
        notes.append("нет файла модели %s — CLIP не используется. "
                     "Скачайте его на машине с интернетом: python tools/get_clip_model.py" % path)
        return "модель не найдена (программа работает без неё)"
    encoder = clipfeat.load(path, config.clip_threads or None)
    if encoder is None:
        raise RuntimeError("onnxruntime есть, но модель не загрузилась: %s" % path)
    image = Image.fromarray((numpy.random.RandomState(1).rand(400, 260, 3) * 255).astype("uint8"))
    encoder.encode_one(image)                      # первый вызов прогревает сессию
    started = time.time()
    vectors = encoder.encode([image] * 4)
    spent = (time.time() - started) / 4
    if vectors.shape != (4, clipfeat.EMBEDDING_SIZE):
        raise RuntimeError("модель вернула не то, что ожидалось: %s" % (vectors.shape,))
    import onnxruntime

    return "onnxruntime %s, %.0f мс на фото (~%.0f с на папку из 250 фото)" % (
        onnxruntime.__version__, spent * 1000, spent * 250)


def folders():
    for name in ("logs", "cache"):
        path = HERE / name
        path.mkdir(exist_ok=True)
        probe = path / "проверка записи.tmp"
        probe.write_text("ок", encoding="utf-8")
        probe.unlink()
    return "папки logs и cache доступны для записи"


def settings():
    from photo_renamer import config as cfg

    config, warnings = cfg.load_config()
    for warning in warnings:
        notes.append("настройки: " + warning)
    return "порог уверенности %.0f%%, оформление %s" % (config.match.min_confidence, config.theme)


def main():
    for stream in (sys.stdout, sys.stderr):   # чтобы кодировка консоли не роняла вывод
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    print("Проверка портативной сборки\n" + "-" * 62)
    check("Чем запущена проверка", own_python)
    check("Windows", windows_version)
    check("Python", python_version)
    check("Библиотеки", libraries)
    check("Окно (tkinter)", window)
    check("Чтение JPEG и признаки", jpeg_and_features)
    check("Модель CLIP", clip_model)
    check("Папки logs и cache", folders)
    check("Настройки", settings)
    print("-" * 62)
    for note in notes:
        print("Замечание: " + note)
    if problems:
        print("\nНЕ ГОТОВО. Ошибок: %d" % len(problems))
        for problem in problems:
            print("  - " + problem)
        print("\nПодробности: задайте переменную PHOTO_RENAMER_DEBUG=1 и запустите снова.")
        return 1
    print("\nВсё в порядке: программу можно запускать «CV.bat».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
