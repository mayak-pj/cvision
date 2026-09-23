"""Сквозной консольный сценарий на синтетических JPEG: проверка → переименование → откат."""

import os

import pytest

from photo_renamer import cli, oplog
from photo_renamer import config as cfg
from test_matcher_images import PARTS, make_target
from synthetic import make_part, perturb, save_jpeg


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Проект"
    ref = root / "Эталон"  # эталон лежит внутри папки проекта, как на работе
    ref.mkdir(parents=True)
    for k, (name, seed) in enumerate(PARTS.items()):
        save_jpeg(perturb(make_part(seed), seed=500 + k), ref / (name + ".jpg"))
    make_target(root / "SN001", [(7, 0), (1, 0), (2, 0), (8, 0)])
    make_target(root / "Партия 2" / "SN002", [(9, 0), (10, 0), (None, 0)])
    (root / "SN001" / "Рентгенограмма.jpg").write_bytes(b"x")
    (root / "SN001" / "схема.jpeg").write_bytes(b"x")
    return root


@pytest.fixture
def config(tmp_path):
    c = cfg.AppConfig()
    c.logs_dir = str(tmp_path / "logs")
    c.cache_dir = str(tmp_path / "cache")
    # тесты этого файла проверяют переименование и откат, поэтому режим «имя каждому фото»
    # выключен: иначе посторонние снимки тоже получают имена (это проверяется отдельным тестом)
    c.match.always_propose = False
    return c


def listing(root):
    return {os.path.relpath(os.path.join(d, f), str(root)) for d, _, files in os.walk(str(root)) for f in files}


def test_dry_run_changes_nothing(project, config):
    before = listing(project)
    out = []
    log_path = cli.run_batch(str(project / "Эталон"), [str(project)], config, apply=False, assume_yes=False,
                             out=out.append)
    assert listing(project) == before
    assert log_path.endswith("_проверка.csv") and os.path.dirname(log_path) == config.logs_dir
    text = "\n".join(out)
    assert "Найдено папок с фото: 2" in text
    assert "DSC_0001.JPG → Кулер.JPG" in text
    rows = oplog.read_log(log_path)
    assert {r["Действие"] for r in rows} <= {oplog.ACTION_PLAN, oplog.ACTION_KEEP}
    assert {r["Папка"] for r in rows} == {str(project / "SN001"), str(project / "Партия 2" / "SN002")}
    with open(log_path[:-4] + ".txt", encoding="utf-8-sig") as f:
        report = f.read()
    assert str(project / "SN001") in report and "Эталон" in report


def test_apply_then_rollback(project, config):
    before = listing(project)
    log_path = cli.run_batch(str(project / "Эталон"), [str(project)], config, apply=True, assume_yes=True,
                             out=lambda *_: None)
    after = listing(project)
    assert os.path.join("SN001", "Кулер.JPG") in after
    assert os.path.join("SN001", "Процессор 1 фото 2.JPG") in after
    assert os.path.join("SN001", "Рентгенограмма.jpg") in after and os.path.join("SN001", "схема.jpeg") in after
    assert os.path.join("Партия 2", "SN002", "DSC_0003.JPG") in after  # посторонний снимок не тронут
    assert {p for p in after if p.startswith("Эталон")} == {p for p in before if p.startswith("Эталон")}
    assert len(after) == len(before)

    cli.run_rollback(log_path, config, out=lambda *_: None)
    assert listing(project) == before


def test_apply_asks_confirmation(project, config, monkeypatch):
    before = listing(project)
    monkeypatch.setattr("builtins.input", lambda *_: "н")
    cli.run_batch(str(project / "Эталон"), [str(project / "SN001")], config, apply=True, assume_yes=False,
                  out=lambda *_: None)
    assert listing(project) == before


def test_main_help_and_arguments(capsys):
    cli.main([])
    assert "--reference" in capsys.readouterr().out


def test_every_photo_gets_a_name_when_asked(project, config):
    """Режим «имя каждому фото»: посторонний снимок тоже получает предложение, но помечен «проверьте»."""
    config.match.always_propose = True
    cli.run_batch(str(project / "Эталон"), [str(project)], config, apply=True, assume_yes=True,
                  out=lambda *_: None)
    after = listing(project)
    assert os.path.join("Партия 2", "SN002", "DSC_0003.JPG") not in after   # имя предложено и применено
