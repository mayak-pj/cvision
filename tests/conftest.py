"""Общие настройки тестов. Окна Qt в тестах открываются offscreen и на экране не видны."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
# Окна Qt в тестах — offscreen. На Windows этот модуль сам не находит системные шрифты (Digger отмечал
# то же): размеры текста там другие, чем в программе, и ширины окон и подписей проверяются впустую.
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _without_local_clip_model(monkeypatch):
    """Тесты идут без модели CLIP, даже если она скачана в model/: в git её нет, и на GitHub Actions
    её тоже нет. Иначе тест, проходящий здесь, на Windows раскладывает папки по группам иначе.
    Сам CLIP проверяется в test_clipfeat.py, путь к модели там задаётся явно."""
    from photo_renamer import config as cfg

    monkeypatch.setattr(cfg.AppConfig, "clip_path", lambda self: "")
