"""Просмотр фото крупно: окно должно появляться поверх и не перечитывать файл зря."""

import os

import pytest

tk = pytest.importorskip("tkinter")

from photo_renamer.gui import photoview  # noqa: E402
from synthetic import make_part, save_jpeg  # noqa: E402


@pytest.fixture
def view(tmp_path, monkeypatch):
    folder = tmp_path / "SN1"
    os.makedirs(str(folder))
    names = []
    for seed in (1, 2, 3):
        name = "IMG_%04d.JPG" % seed
        save_jpeg(make_part(seed), folder / name)
        names.append(name)
    reads = []
    real = photoview.feat.load_image
    monkeypatch.setattr(photoview.feat, "load_image",
                        lambda path, size: (reads.append(path), real(path, size))[1])
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("нет графической среды: %s" % exc)
    root.withdraw()
    window = photoview.LargeView(root, str(folder), names, 0, cache=None)
    window.update()
    yield window, reads, names
    window.destroy()
    root.destroy()


def test_view_is_raised_above_the_window_it_was_opened_from(view):
    """Окно редактора развёрнуто во весь экран: просмотр, открывшийся позади, не виден,
    и двойной клик выглядит как неработающий (замечание с рабочей машины)."""
    window, _, names = view
    assert window.wm_transient()                    # привязано к родителю, значит всегда над ним
    assert window.title() == names[0]
    assert "1 из 3" in window.caption.get()


def test_file_is_read_once_per_photo(view):
    """Файл на сетевом диске весит 15 МБ: при изменении размера окна он не перечитывается."""
    window, reads, names = view
    assert len(reads) == 1
    window._on_configure()                          # то же окно, тот же размер — чтения нет
    assert len(reads) == 1
    window.geometry("600x500")
    window.update()
    assert len(reads) == 1                          # размер другой, но фото уже в памяти
    window.step(1)
    assert len(reads) == 2 and reads[1].endswith(names[1])


def test_unreadable_photo_does_not_break_the_view(view, monkeypatch):
    """Непрочитанное фото — это надпись в окне, а не исключение посреди просмотра."""
    window, _, _ = view
    monkeypatch.setattr(photoview.feat, "load_image",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("файл недоступен")))
    window.step(1)
    assert "Не удалось открыть" in window.caption.get()
