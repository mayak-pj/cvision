"""Признак CLIP: подключение модели, расстояния, работа без модели."""

import numpy as np
import pytest
from PIL import Image

from photo_renamer import clipfeat
from photo_renamer import features as feat
from photo_renamer.matcher import MatchConfig


class FakeEncoder:
    """Заменяет модель: «эмбеддинг» считается по среднему цвету, но ведёт себя как настоящий."""

    version = "fake"

    def encode(self, images):
        out = []
        for img in images:
            a = np.asarray(img.convert("RGB"), dtype=np.float32).reshape(-1, 3).mean(axis=0)
            v = np.concatenate([a, np.ones(clipfeat.EMBEDDING_SIZE - 3, dtype=np.float32)])
            out.append(v / np.linalg.norm(v))
        return np.stack(out)

    def encode_one(self, img):
        return self.encode([img])[0]


def image(color):
    return Image.new("RGB", (400, 260), color)


def test_preprocess_gives_clip_input():
    a = clipfeat.preprocess(image((10, 200, 30)))
    assert a.shape == (3, clipfeat.INPUT_SIZE, clipfeat.INPUT_SIZE)
    assert a.dtype == np.float32


def test_features_without_model_have_no_clip():
    f = feat.FeatureExtractor().from_image(image((10, 20, 30)))
    assert f.clip is None
    components = feat.FeatureExtractor().distance_components([f], [f])
    assert feat.CLIP not in components


def test_features_with_model_have_clip_and_distance():
    extractor = feat.FeatureExtractor(encoder=FakeEncoder())
    near = [extractor.from_image(image((10, 20, 30))), extractor.from_image(image((12, 22, 33)))]
    far = extractor.from_image(image((200, 30, 30)))
    assert near[0].clip.shape == (clipfeat.EMBEDDING_SIZE,)
    assert np.isclose(np.linalg.norm(near[0].clip), 1.0, atol=1e-5)
    components = extractor.distance_components(near, [near[1], far])
    assert feat.CLIP in components
    assert components[feat.CLIP][0, 0] < components[feat.CLIP][0, 1]   # похожее ближе непохожего


def test_model_version_is_part_of_feature_version():
    """Иначе после подключения модели программа взяла бы из кэша признаки без CLIP."""
    plain = feat.FeatureExtractor().version()
    with_model = feat.FeatureExtractor(encoder=FakeEncoder()).version()
    assert plain != with_model


def test_missing_model_file_is_not_an_error(tmp_path):
    assert clipfeat.load(str(tmp_path / "нет.onnx")) is None
    assert "не используется" in clipfeat.describe(str(tmp_path / "нет.onnx"))


def test_weights_depend_on_model():
    config = MatchConfig()
    assert feat.CLIP in config.for_features(True)
    assert feat.CLIP not in config.for_features(False)
    assert config.for_features(False) == config.weights


def test_download_tool_uses_the_same_file_name():
    """Имя файла модели в инструменте скачивания и в программе должно совпадать."""
    import os
    import re

    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "get_clip_model.py")
    with open(tool, encoding="utf-8") as f:
        text = f.read()
    found = re.search(r'^MODEL_FILE = "([^"]+)"', text, re.M)
    assert found and found.group(1) == clipfeat.MODEL_FILE
