"""Скачивание модели CLIP (выполняется на машине с интернетом).

Модель — часть CLIP ViT-B/32, отвечающая за изображения, в формате ONNX. На рабочем компьютере
она просто лежит рядом с программой: интернет и права администратора не нужны.

    python tools/get_clip_model.py                      # положить в model/ рядом с программой
    python tools/get_clip_model.py --dest D:\\CV\\model
    python tools/get_clip_model.py --check              # только проверить, что уже скачано

Файл весит около 335 МБ. Если скачивание прервалось, запустите команду ещё раз: файл
докачивается заново целиком, недокачанный файл не остаётся.
"""

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from photo_renamer.console import setup as console_setup  # noqa: E402

# Имя файла модели. Повторено здесь намеренно: скрипт должен работать на машине, где ещё
# не установлены numpy и Pillow (например, на раннере сборки), а photo_renamer.clipfeat их
# импортирует. Совпадение с clipfeat.MODEL_FILE проверяется тестом.
MODEL_FILE = "clip-vit-b32-vision.onnx"

URL = "https://huggingface.co/Qdrant/clip-ViT-B-32-vision/resolve/main/model.onnx"
EXPECTED_SIZE = 351686194
# SHA-256 проверенного файла. Если он не совпал, файл повреждён или подменён — такой не используем.
EXPECTED_SHA256 = "c68d3d9a200ddd2a8c8a5510b576d4c94d1ae383bf8b36dd8c084f94e1fb4d63"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def describe(path: str) -> str:
    if not os.path.isfile(path):
        return "нет файла: %s" % path
    size = os.path.getsize(path)
    ok = "размер верный" if size == EXPECTED_SIZE else "ВНИМАНИЕ: ожидался размер %d" % EXPECTED_SIZE
    return "%s — %.0f МБ, %s\n    SHA-256: %s" % (path, size / 1e6, ok, sha256(path))


def download(url: str, target: str) -> int:
    folder = os.path.dirname(os.path.abspath(target))
    os.makedirs(folder, exist_ok=True)
    print("Скачивание: %s" % url)
    handle, temporary = tempfile.mkstemp(dir=folder, suffix=".part")
    os.close(handle)
    try:
        with urllib.request.urlopen(url) as response, open(temporary, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    sys.stdout.write("\r    %3.0f%% (%.0f из %.0f МБ)" % (100.0 * done / total, done / 1e6, total / 1e6))
                    sys.stdout.flush()
        print()
        if EXPECTED_SHA256 and sha256(temporary) != EXPECTED_SHA256:
            print("ОШИБКА: контрольная сумма не совпала, файл не сохранён")
            return 1
        shutil.move(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    print("Готово: %s" % describe(target))
    return 0


def main(argv=None) -> int:
    console_setup()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_dest = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model")
    parser.add_argument("--dest", default=default_dest, help="папка, куда положить модель")
    parser.add_argument("--url", default=URL, help="откуда скачивать")
    parser.add_argument("--check", action="store_true", help="только проверить уже скачанный файл")
    args = parser.parse_args(argv)

    target = os.path.join(args.dest, MODEL_FILE)
    if args.check:
        print(describe(target))
        return 0 if os.path.isfile(target) else 1
    if os.path.isfile(target) and os.path.getsize(target) == EXPECTED_SIZE:
        print("Модель уже на месте: %s" % describe(target))
        return 0
    return download(args.url, target)


if __name__ == "__main__":
    sys.exit(main())
