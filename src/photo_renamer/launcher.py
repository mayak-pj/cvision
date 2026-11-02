"""Запуск окон: прежних (tkinter) или новых (PyQt5). Не загружает ни одну из этих библиотек раньше времени."""

import sys
from typing import Optional

from . import config as cfg


def load_qt():
    """Модуль окна на Qt или None, если PyQt5 здесь не загружается."""
    try:
        from .gui_qt import app
    except ImportError as exc:
        if sys.stderr is not None:
            print("Окно на PyQt5 не запускается (%s), открывается прежнее" % exc, file=sys.stderr)
        return None
    return app


def main(interface: Optional[str] = None) -> None:
    """interface — выбор на этот запуск; None — как в настройках."""
    config, warnings = cfg.load_config()
    if (interface or config.interface) == "qt":
        qt = load_qt()
        if qt is not None:
            qt.main(config, warnings)
            return
    from .gui import app

    app.main()
