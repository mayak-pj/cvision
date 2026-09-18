import json
import os

from photo_renamer import config as cfg


def write(path, data, bom=False):
    text = json.dumps(data, ensure_ascii=False)
    with open(str(path), "w", encoding="utf-8-sig" if bom else "utf-8") as f:
        f.write(text)


def test_missing_file_gives_defaults(tmp_path):
    config, warnings = cfg.load_config(str(tmp_path / "нет.json"))
    assert warnings == []
    assert config.match.min_confidence == 60
    assert config.logs_path() == os.path.join(cfg.APP_DIR, "logs")


def test_example_config_is_valid():
    config, warnings = cfg.load_config(os.path.join(cfg.APP_DIR, "config.example.json"))
    assert warnings == []
    assert "Кабель DP" in config.problem_categories


def test_values_are_applied_and_bom_accepted(tmp_path):
    path = tmp_path / "config.json"
    write(path, {"min_confidence": 75, "weights": {"phash": 1, "hist": 0}, "hist_bins": [8, 3, 3],
                 "problem_categories": ["Кабель DP"], "rotations": 1, "_комментарий": "x"}, bom=True)
    config, warnings = cfg.load_config(str(path))
    assert warnings == []
    assert config.match.min_confidence == 75
    assert config.match.weights == {"phash": 1.0, "hist": 0.0}
    assert config.hist_bins == (8, 3, 3)
    assert config.rotations == 1
    assert config.extractor().rotations == 1


def test_invalid_values_fall_back_with_warnings(tmp_path):
    path = tmp_path / "config.json"
    write(path, {"min_confidence": 150, "max_distance": "много", "weights": {"orb": 1}, "rotations": 3,
                 "top_n": 2.5, "sort": "size", "неизвестно": 1, "hist_bins": [1, 2]})
    config, warnings = cfg.load_config(str(path))
    assert len(warnings) == 8
    assert config.match.min_confidence == 60 and config.match.max_distance == 0.35
    assert config.rotations == 1 and config.sort == "name"


def test_broken_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ не json", encoding="utf-8")
    config, warnings = cfg.load_config(str(path))
    assert len(warnings) == 1 and config.match.min_confidence == 60


def test_save_load_roundtrip(tmp_path):
    config = cfg.AppConfig()
    config.match.min_confidence = 70
    config.problem_categories = ["Кабель HDMI"]
    path = str(tmp_path / "config.json")
    cfg.save_config(config, path)
    loaded, warnings = cfg.load_config(path)
    assert warnings == []
    assert cfg.to_dict(loaded) == cfg.to_dict(config)
