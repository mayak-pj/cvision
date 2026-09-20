"""Кэш миниатюр и признаков."""

import os

import numpy as np
import pytest

from photo_renamer import cache as cache_mod
from photo_renamer.cache import PhotoCache, file_key
from photo_renamer.features import FeatureError, FeatureExtractor
from synthetic import make_part, save_jpeg


@pytest.fixture
def photo_dir(tmp_path):
    folder = tmp_path / "SN"
    folder.mkdir()
    for s in range(3):
        save_jpeg(make_part(s, size=(1200, 800)), folder / ("DSC_%04d.JPG" % s))
    return folder


def count_loads(monkeypatch):
    calls = []
    real = cache_mod.feat.load_image
    monkeypatch.setattr(cache_mod.feat, "load_image", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    return calls


def test_features_equal_to_uncached(tmp_path, photo_dir):
    ex = FeatureExtractor()
    cache = PhotoCache(str(tmp_path / "c.sqlite"))
    path = str(photo_dir / "DSC_0000.JPG")
    cached = cache.features(path, ex)
    direct = ex.from_file(path)
    for name in ("fg_hist", "lbp", "hog", "tiny", "hist", "phash"):
        assert np.array_equal(getattr(cached, name), getattr(direct, name))
    thumb = cache.thumbnail(path)
    assert max(thumb.size) == cache_mod.THUMB_SIZE


def test_second_read_uses_cache_and_survives_rename(tmp_path, photo_dir, monkeypatch):
    ex = FeatureExtractor()
    cache = PhotoCache(str(tmp_path / "c.sqlite"))
    paths = [str(photo_dir / n) for n in sorted(os.listdir(str(photo_dir)))]
    calls = count_loads(monkeypatch)
    cache.extract_many(ex, paths)
    assert len(calls) == 3
    os.rename(paths[0], str(photo_dir / "Кулер.JPG"))  # переименование не меняет содержимое
    feats, errors = cache.extract_many(ex, [str(photo_dir / "Кулер.JPG")] + paths[1:])
    assert errors == [] and all(f is not None for f in feats)
    assert len(calls) == 3  # ни одного нового чтения фото целиком
    cache.thumbnail(paths[1])
    assert len(calls) == 3


def test_cache_persists_between_sessions(tmp_path, photo_dir, monkeypatch):
    db = str(tmp_path / "c.sqlite")
    path = str(photo_dir / "DSC_0001.JPG")
    c1 = PhotoCache(db)
    c1.features(path, FeatureExtractor())
    c1.close()
    calls = count_loads(monkeypatch)
    PhotoCache(db).features(path, FeatureExtractor())
    assert calls == []


def test_changed_settings_recompute_features_only(tmp_path, photo_dir, monkeypatch):
    cache = PhotoCache(str(tmp_path / "c.sqlite"))
    path = str(photo_dir / "DSC_0001.JPG")
    cache.features(path, FeatureExtractor())
    calls = count_loads(monkeypatch)
    other = cache.features(path, FeatureExtractor(white_balance=True))
    assert other is not None and calls == []        # новые признаки посчитаны из миниатюры
    cache.features(path, FeatureExtractor())
    cache.thumbnail(path)
    assert calls == []                              # старые признаки и миниатюра остались


def test_same_size_different_content_not_confused(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"x" * 1000)
    b.write_bytes(b"y" * 1000)
    os.utime(str(a), (1, 1))
    os.utime(str(b), (1, 1))
    assert file_key(str(a)) != file_key(str(b))


def test_errors(tmp_path, photo_dir):
    cache = PhotoCache(str(tmp_path / "c.sqlite"))
    broken = photo_dir / "broken.JPG"
    broken.write_bytes(b"not a jpeg")
    feats, errors = cache.extract_many(FeatureExtractor(), [str(broken), str(photo_dir / "нет.JPG")])
    assert feats == [None, None]
    assert [e.reason for e in errors][1] == "файл не найден"
    with pytest.raises(FeatureError):
        cache.thumbnail(str(broken))


def test_eviction_keeps_size_under_limit(tmp_path, photo_dir):
    cache = PhotoCache(str(tmp_path / "c.sqlite"), max_mb=0.03)  # ~30 КБ: помещается одна-две записи
    paths = [str(photo_dir / n) for n in sorted(os.listdir(str(photo_dir)))]
    for p in paths:
        cache.features(p, FeatureExtractor())
    cache.evict()
    assert cache.size_bytes() <= cache.max_bytes
    assert cache.features(paths[-1], FeatureExtractor()) is not None  # удалённое пересчитывается


def test_new_settings_reuse_thumbnail_without_reading_photo(tmp_path, photo_dir, monkeypatch):
    """Смена настроек признаков не должна приводить к повторному чтению фото (важно для сети)."""
    cache = PhotoCache(str(tmp_path / "c.sqlite"))
    path = str(photo_dir / "DSC_0002.JPG")
    cache.features(path, FeatureExtractor())
    calls = count_loads(monkeypatch)
    other = cache.features(path, FeatureExtractor(hist_bins=(8, 4, 4)))
    assert other is not None and calls == []        # фото не перечитывалось
    assert cache.features(path, FeatureExtractor(hist_bins=(8, 4, 4))) is not None
    assert calls == []
