import hashlib
import os

import numpy as np
import pytest
from PIL import Image

from photo_renamer.features import (
    FG_HIST,
    HIST,
    PHASH,
    TINY,
    FeatureError,
    FeatureExtractor,
    combine,
    extract_many,
    hsv_histogram,
    load_image,
    phash_bits,
)
from synthetic import make_part, perturb, save_jpeg


def test_phash_matches_imagehash():
    imagehash = pytest.importorskip("imagehash")
    for seed in range(10):
        img = make_part(seed)
        assert np.array_equal(phash_bits(img), imagehash.phash(img).hash.ravel())
        assert np.array_equal(phash_bits(img, hash_size=16), imagehash.phash(img, hash_size=16).hash.ravel())


def test_histogram_normalized():
    hist = hsv_histogram(make_part(1), bins=(12, 4, 4))
    assert hist.shape == (192,)
    assert hist.sum() == pytest.approx(1.0)
    assert (hist >= 0).all()


def test_distances_identity_and_range():
    ex = FeatureExtractor()
    feats = [ex.from_image(make_part(s)) for s in range(6)]
    comps = ex.distance_components(feats, feats)
    for name in (PHASH, HIST):
        m = comps[name]
        assert m.shape == (6, 6)
        assert np.allclose(np.diag(m), 0.0, atol=1e-6)
        assert (m >= 0).all() and (m <= 1).all()
    # несвязанные изображения: около половины бит pHash различается
    off = comps[PHASH][~np.eye(6, dtype=bool)]
    assert off.mean() > 0.3


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_rotation_invariance(angle):
    img = make_part(3)
    rotated = img.rotate(angle, expand=True)
    with_rot = FeatureExtractor(rotations=4)
    d = with_rot.distance_components([with_rot.from_image(img)], [with_rot.from_image(rotated)])
    assert d[PHASH][0, 0] == 0.0
    no_rot = FeatureExtractor(rotations=1)
    d = no_rot.distance_components([no_rot.from_image(img)], [no_rot.from_image(rotated)])
    assert d[PHASH][0, 0] > 0.2


def test_exif_orientation_is_applied(tmp_path):
    img = make_part(4)
    save_jpeg(img, tmp_path / "plain.jpg")
    # пиксели сохранены «на боку», тег ориентации 6 (повернуть на 90° по часовой) их выпрямляет
    exif = Image.Exif()
    exif[0x0112] = 6
    save_jpeg(img.transpose(Image.ROTATE_90), tmp_path / "tagged.jpg", exif=exif)

    plain = load_image(str(tmp_path / "plain.jpg"))
    tagged = load_image(str(tmp_path / "tagged.jpg"))
    assert plain.size == tagged.size
    ex = FeatureExtractor(rotations=1)
    d = ex.distance_components([ex.from_image(plain)], [ex.from_image(tagged)])
    assert d[PHASH][0, 0] <= 0.05


def test_large_jpeg_is_downscaled_on_decode(tmp_path):
    big = make_part(5, size=(4000, 3000))
    path = tmp_path / "big.jpg"
    save_jpeg(big, path, quality=95)
    loaded = load_image(str(path), work_size=256)
    assert max(loaded.size) == 256
    assert loaded.mode == "RGB"

    # быстрый путь (draft) даёт те же признаки, что полное декодирование того же файла
    with Image.open(str(path)) as full:
        full = full.convert("RGB")
    full.thumbnail((256, 256), Image.LANCZOS)
    ex = FeatureExtractor()
    d = ex.distance_components([ex.from_file(str(path))], [ex.from_image(full)])
    assert d[PHASH][0, 0] <= 0.05
    assert d[HIST][0, 0] <= 0.05


def test_file_is_not_modified(tmp_path):
    path = tmp_path / "a.jpg"
    save_jpeg(make_part(6), path)
    before = (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(str(path)).st_mtime_ns)
    FeatureExtractor().from_file(str(path))
    after = (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(str(path)).st_mtime_ns)
    assert before == after


def test_bad_files_reported_not_fatal(tmp_path):
    good = tmp_path / "good.jpg"
    save_jpeg(make_part(7), good)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff not really a jpeg")
    missing = tmp_path / "missing.jpg"

    with pytest.raises(FeatureError):
        load_image(str(broken))

    progress = []
    feats, errors = extract_many(FeatureExtractor(), [str(good), str(broken), str(missing)],
                                 on_progress=lambda d, t: progress.append((d, t)))
    assert feats[0] is not None and feats[1] is None and feats[2] is None
    assert [e.path for e in errors] == [str(broken), str(missing)]
    assert errors[1].reason == "файл не найден"
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_combine_weights():
    comps = {PHASH: np.array([[0.2]]), HIST: np.array([[0.6]])}
    assert combine(comps, {PHASH: 1, HIST: 1})[0, 0] == pytest.approx(0.4)
    assert combine(comps, {PHASH: 3, HIST: 1})[0, 0] == pytest.approx(0.3)
    assert combine(comps, {PHASH: 1, HIST: 0, "orb": 5})[0, 0] == pytest.approx(0.2)
    with pytest.raises(ValueError):
        combine(comps, {PHASH: 0})


def test_nearest_reference_is_same_part():
    """Переснятая деталь (в том числе повёрнутая) ближе всего к своему эталону по phash (с поворотами) + hist."""
    n_parts = 15
    ex = FeatureExtractor(rotations=4)
    refs = [ex.from_image(make_part(s)) for s in range(n_parts)]
    shots, truth = [], []
    for s in range(n_parts):
        for k, angle in enumerate((0, 0, 90, 180)):
            shots.append(ex.from_image(perturb(make_part(s), seed=s * 10 + k, rotate=angle)))
            truth.append(s)
    dist = combine(ex.distance_components(shots, refs), {PHASH: 1.0, HIST: 1.0})
    predicted = dist.argmin(axis=1)
    accuracy = (predicted == np.array(truth)).mean()
    assert accuracy == 1.0, "точность %.2f" % accuracy


def test_default_features_nearest_reference_without_rotation():
    """Признаки по умолчанию (fg_hist + tiny): переснятая деталь без поворота ближе всего к своему эталону."""
    from photo_renamer.features import component_scales

    ex = FeatureExtractor()
    refs = [ex.from_image(make_part(s)) for s in range(15)]
    shots = [ex.from_image(perturb(make_part(s), seed=s * 10 + k)) for s in range(15) for k in range(3)]
    scales = component_scales(ex.distance_components(refs, refs), ~np.eye(15, dtype=bool))
    comps = {k: v * scales[k] for k, v in ex.distance_components(shots, refs).items()}
    dist = combine(comps, {FG_HIST: 2.0, TINY: 1.0})
    # синтетические искажения яркости жёстче реальных, одна ошибка из 45 допустима
    assert (dist.argmin(axis=1) == np.repeat(np.arange(15), 3)).mean() >= 0.95


def test_foreground_mask_ignores_background():
    from photo_renamer.features import foreground_histogram, foreground_mask

    img = make_part(2)
    mask = foreground_mask(img)
    assert 0.05 < mask.mean() < 0.95
    # один и тот же предмет на фонах разного оттенка даёт близкие гистограммы без фона
    arr = np.asarray(img).copy()
    background = ~mask
    arr[background] = (205, 215, 230)
    other = Image.fromarray(arr)
    plain = hsv_histogram(img), hsv_histogram(other)
    fg = foreground_histogram(img), foreground_histogram(other)
    d_plain = 1 - np.minimum(*plain).sum()
    d_fg = 1 - np.minimum(*fg).sum()
    assert d_fg < d_plain / 3


def test_component_scales():
    from photo_renamer.features import component_scales

    comps = {PHASH: np.array([[0.0, 0.4], [0.4, 0.0]]), TINY: np.array([[0.0, 0.1], [0.1, 0.0]])}
    different = np.array([[False, True], [True, False]])
    scales = component_scales(comps, different)
    assert scales[PHASH] == pytest.approx(1.25) and scales[TINY] == pytest.approx(5.0)


def test_white_balance_makes_background_neutral_and_matches_lighting():
    from photo_renamer.features import balance_by_background, foreground_mask

    img = make_part(3)
    arr = np.asarray(img, dtype=np.float32) * np.array([0.8, 0.9, 1.15])  # другое освещение
    shifted = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    balanced = np.asarray(balance_by_background(shifted), dtype=np.float32)
    background = balanced[~foreground_mask(img)]
    assert np.abs(np.median(background, axis=0) - 128).max() <= 3

    plain, wb = FeatureExtractor(), FeatureExtractor(white_balance=True)
    d_plain = plain.distance_components([plain.from_image(img)], [plain.from_image(shifted)])[FG_HIST][0, 0]
    d_wb = wb.distance_components([wb.from_image(img)], [wb.from_image(shifted)])[FG_HIST][0, 0]
    assert d_wb < d_plain
