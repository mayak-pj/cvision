import os
import sys
import unicodedata

import pytest
from PIL import Image

from photo_renamer import scanner
from photo_renamer.scanner import (
    SORT_BY_EXIF,
    find_photo_folders,
    is_excluded_name,
    is_jpg_name,
    is_service_name,
    list_photos,
    natural_key,
    to_extended_path,
)


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def make_jpeg(path, exif_date=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGB", (8, 8), (200, 100, 50))
    exif = Image.Exif()
    if exif_date:
        exif[0x8769] = {36867: exif_date}
    img.save(path, "JPEG", exif=exif.tobytes())


# --- фильтры по имени ---------------------------------------------------------

@pytest.mark.parametrize("name", ["a.jpg", "b.JPG", "c.Jpg", "фото 1.jPg", "x.y.jpg"])
def test_jpg_accepted(name):
    assert is_jpg_name(name)


@pytest.mark.parametrize("name", ["a.jpeg", "b.JPEG", "c.png", "d.PNG", "e.txt", "f.jpg.bak", "jpg", ".jpgx"])
def test_non_jpg_rejected(name):
    assert not is_jpg_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "рентгенограмма.jpg",
        "Рентгенограмма 1.jpg",
        "РЕНТГЕНОГРАММА.JPG",
        "плата_рентгенограмма_2.jpg",
        "xРеНтГеНоГрАмМаx.jpg",
        unicodedata.normalize("NFD", "Рентгенограмма.jpg"),
    ],
)
def test_xray_excluded(name):
    assert is_excluded_name(name)


@pytest.mark.parametrize("name", ["рентген.jpg", "грамма.jpg", "Процессор 1 фото 1.jpg"])
def test_other_names_not_excluded(name):
    assert not is_excluded_name(name)


@pytest.mark.parametrize("name", ["._File 8.JPG", "._Кабель miniSAS.JPG", "._a.jpg"])
def test_macos_service_files_detected(name):
    assert is_service_name(name)


@pytest.mark.parametrize("name", ["File 8.JPG", "_File 8.JPG", ".File 8.JPG", "a._b.jpg"])
def test_ordinary_names_are_not_service(name):
    assert not is_service_name(name)


def test_macos_service_files_are_not_photos(tmp_path):
    folder = str(tmp_path / "SN1")
    for name in ["File 8.JPG", "._File 8.JPG", "._Рентгенограмма.jpg"]:
        touch(os.path.join(folder, name))

    scan = list_photos(folder)
    assert scan.photos == ["File 8.JPG"]
    assert scan.excluded == []

    only_service = str(tmp_path / "SN2")
    touch(os.path.join(only_service, "._File 1.JPG"))
    result = find_photo_folders(str(tmp_path))
    assert [f.path for f in result.folders] == [folder]


# --- сортировка ---------------------------------------------------------------

def test_natural_sort():
    names = ["DSC_10.jpg", "DSC_2.jpg", "dsc_1.JPG", "IMG_0010.jpg", "IMG_0009.jpg", "DSC_2 (1).jpg"]
    assert sorted(names, key=natural_key) == [
        "dsc_1.JPG",
        "DSC_2.jpg",
        "DSC_2 (1).jpg",
        "DSC_10.jpg",
        "IMG_0009.jpg",
        "IMG_0010.jpg",
    ]


def test_natural_sort_is_total_for_case_variants():
    # Имена, отличающиеся только регистром, упорядочены детерминированно
    assert sorted(["a.jpg", "A.jpg"], key=natural_key) == sorted(["A.jpg", "a.jpg"], key=natural_key)


# --- содержимое одной папки ---------------------------------------------------

def test_list_photos_filters_and_sorts(tmp_path):
    folder = tmp_path / "SN001"
    for name in [
        "DSC_10.JPG", "DSC_2.jpg", "DSC_1.jpg",
        "Рентгенограмма.jpg", "снимок_РЕНТГЕНОГРАММА_2.JPG",
        "scan.jpeg", "scan2.JPEG", "logo.png", "notes.txt",
    ]:
        touch(str(folder / name))
    os.makedirs(str(folder / "sub.jpg"))  # папка с «фото»-именем — не файл

    scan = list_photos(str(folder))
    assert scan.photos == ["DSC_1.jpg", "DSC_2.jpg", "DSC_10.JPG"]
    assert scan.excluded == ["Рентгенограмма.jpg", "снимок_РЕНТГЕНОГРАММА_2.JPG"]
    assert scan.photo_paths[0] == os.path.join(str(folder), "DSC_1.jpg")


def test_list_photos_exif_order(tmp_path):
    folder = tmp_path / "SN"
    make_jpeg(str(folder / "a.jpg"), "2024:05:01 10:00:03")
    make_jpeg(str(folder / "b.jpg"), "2024:05:01 10:00:01")
    make_jpeg(str(folder / "c.jpg"))  # без даты — в конец
    make_jpeg(str(folder / "d.jpg"), "2024:05:01 10:00:02")
    touch(str(folder / "e.jpg"), b"not a jpeg")  # битый файл — без даты, в конец

    scan = list_photos(str(folder), sort=SORT_BY_EXIF)
    assert scan.photos == ["b.jpg", "d.jpg", "a.jpg", "c.jpg", "e.jpg"]
    assert scan.missing_date == ["c.jpg", "e.jpg"]


def test_list_photos_unknown_sort(tmp_path):
    touch(str(tmp_path / "a.jpg"))
    with pytest.raises(ValueError):
        list_photos(str(tmp_path), sort="size")


# --- рекурсивный обход --------------------------------------------------------

@pytest.fixture
def project(tmp_path):
    """
    project/
      root.jpg                        <- фото в самом корне
      Эталон/ ref.jpg, sub/r.jpg      <- исключается целиком
      Партия 2/SN10/a.jpg
      Партия 2/SN9/a.jpg
      SN1/ a.jpg, b.JPG, Рентгенограмма.jpg
      SN1/extra/ c.jpg                <- и фото в папке, и подпапка с фото
      SN2/ only.png, only.jpeg        <- нет .jpg — не папка с фото
      SN3/ Рентгенограмма.jpg         <- только рентгенограммы — не папка с фото
      empty/
    """
    root = tmp_path / "project"
    touch(str(root / "root.jpg"))
    touch(str(root / "Эталон" / "ref.jpg"))
    touch(str(root / "Эталон" / "sub" / "r.jpg"))
    touch(str(root / "Партия 2" / "SN10" / "a.jpg"))
    touch(str(root / "Партия 2" / "SN9" / "a.jpg"))
    touch(str(root / "SN1" / "a.jpg"))
    touch(str(root / "SN1" / "b.JPG"))
    touch(str(root / "SN1" / "Рентгенограмма.jpg"))
    touch(str(root / "SN1" / "extra" / "c.jpg"))
    touch(str(root / "SN2" / "only.png"))
    touch(str(root / "SN2" / "only.jpeg"))
    touch(str(root / "SN3" / "Рентгенограмма.jpg"))
    os.makedirs(str(root / "empty"))
    return root


def rel(result, root):
    return [os.path.relpath(f.path, str(root)) for f in result.folders]


def test_find_folders_recursive_with_exclusion(project):
    result = find_photo_folders(str(project), exclude=[str(project / "Эталон")])
    assert result.errors == []
    # латиница раньше кириллицы, внутри — естественный порядок (SN9 < SN10)
    assert rel(result, project) == [
        ".",
        "SN1",
        os.path.join("SN1", "extra"),
        os.path.join("Партия 2", "SN9"),
        os.path.join("Партия 2", "SN10"),
    ]
    sn1 = result.folders[1]
    assert sn1.photos == ["a.jpg", "b.JPG"]
    assert sn1.excluded == ["Рентгенограмма.jpg"]


def test_exclusion_path_is_normalized(project):
    ref = str(project / "Эталон") + os.sep + "." + os.sep  # «Эталон/./»
    result = find_photo_folders(str(project), exclude=[ref])
    assert "Эталон" not in "".join(rel(result, project))


def test_without_exclusion_reference_is_found(project):
    found = rel(find_photo_folders(str(project)), project)
    assert "Эталон" in found and os.path.join("Эталон", "sub") in found


def test_missing_root(tmp_path):
    result = find_photo_folders(str(tmp_path / "нет такой"))
    assert result.folders == []
    assert len(result.errors) == 1


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="chmod-права POSIX")
def test_unreadable_folder_is_reported_not_fatal(project):
    locked = project / "SN1" / "extra"
    os.chmod(str(locked), 0)
    try:
        result = find_photo_folders(str(project), exclude=[str(project / "Эталон")])
    finally:
        os.chmod(str(locked), 0o755)
    assert os.path.join("SN1", "extra") not in rel(result, project)
    assert "SN1" in rel(result, project)
    assert [os.path.relpath(e.path, str(project)) for e in result.errors] == [os.path.join("SN1", "extra")]
    assert result.errors[0].message == "Нет доступа к папке"


@pytest.mark.skipif(not hasattr(os, "symlink") or sys.platform == "win32", reason="нужны symlink")
def test_symlink_loop_not_followed(project):
    os.symlink(str(project), str(project / "SN1" / "loop"))
    touch(str(project / "SN1" / "x.jpg"))
    os.symlink(str(project / "SN1" / "x.jpg"), str(project / "SN1" / "link.jpg"))
    result = find_photo_folders(str(project), exclude=[str(project / "Эталон")])
    assert not any("loop" in p for p in rel(result, project))
    assert result.folders[1].photos == ["a.jpg", "b.JPG", "x.jpg"]  # ссылка на файл не считается фото


# --- длинные пути Windows -----------------------------------------------------

def test_extended_path_short_unchanged():
    assert to_extended_path(r"C:\short\path") == r"C:\short\path"


def test_extended_path_local_and_unc():
    long_local = "C:\\" + "a" * 250
    long_unc = "\\\\server\\share\\" + "b" * 250
    assert to_extended_path(long_local) == "\\\\?\\" + long_local
    assert to_extended_path(long_unc) == "\\\\?\\UNC\\server\\share\\" + "b" * 250
    already = "\\\\?\\" + long_local
    assert to_extended_path(already) == already


def test_io_path_noop_on_posix(monkeypatch):
    monkeypatch.setattr(scanner.os, "name", "posix")
    assert scanner.io_path("/x" * 200) == "/x" * 200


@pytest.mark.parametrize("name", ["a.tif", "b.TIFF", "c.tiff", "d.jpeg", "e.JPEG", "f.png"])
def test_other_image_formats_are_ignored(name):
    assert not is_jpg_name(name)


def test_exif_order_differs_flag(tmp_path):
    folder = tmp_path / "SN"
    os.makedirs(str(folder))
    for name in ("b.jpg", "a.jpg", "c.jpg"):
        touch(str(folder / name))
    dates = {"a.jpg": "2024:05:01 10:00:30", "b.jpg": "2024:05:01 10:00:10", "c.jpg": "2024:05:01 10:00:20"}
    scan = list_photos(str(folder), sort=SORT_BY_EXIF, date_source=lambda p: dates[os.path.basename(p)])
    assert scan.photos == ["b.jpg", "c.jpg", "a.jpg"]
    assert scan.order_differs

    same = {"a.jpg": "2024:05:01 10:00:10", "b.jpg": "2024:05:01 10:00:20", "c.jpg": "2024:05:01 10:00:30"}
    scan = list_photos(str(folder), sort=SORT_BY_EXIF, date_source=lambda p: same[os.path.basename(p)])
    assert scan.photos == ["a.jpg", "b.jpg", "c.jpg"] and not scan.order_differs


def test_huge_photo_does_not_break_the_scan(tmp_path):
    """Фото на сотни миллионов пикселей не должно обрушивать анализ проекта.

    Случай с рабочей машины: «Image size (244941840 pixels) exceeds limit … decompression
    bomb DOS attack». Pillow возбуждает DecompressionBombError, который не наследуется
    от OSError, и раньше он проходил мимо всех перехватов.
    """
    from PIL import Image

    photo = tmp_path / "огромное фото.jpg"
    Image.new("RGB", (64, 48), (120, 130, 140)).save(photo, "JPEG")

    limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = 10          # как будто файл больше порога
        assert scanner.read_exif_datetime(str(photo)) is None      # не падаем, просто нет даты
        assert scanner.list_photos(str(tmp_path), date_source=scanner.read_exif_datetime).photos == \
            ["огромное фото.jpg"]
    finally:
        Image.MAX_IMAGE_PIXELS = limit


def test_large_images_are_allowed():
    """Порог Pillow поднят: свои же большие снимки должны открываться, а не считаться атакой."""
    from PIL import Image

    limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = 1000
        scanner.allow_large_images()
        assert Image.MAX_IMAGE_PIXELS == scanner.LARGE_IMAGE_LIMIT
    finally:
        Image.MAX_IMAGE_PIXELS = limit
