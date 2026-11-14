"""Оформление как в Digger: стиль Fusion, палитра и QSS из одного набора токенов, значки Lucide.

Значки перекрашиваются под тему: в их файлах цвет линий — currentColor (лицензия — assets/icons/LICENSE.txt).
"""

import io
import os
import tempfile
from string import Template
from typing import Dict, Optional

from PyQt5.QtCore import QByteArray, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PyQt5.QtSvg import QSvgRenderer

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons")

SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
RADIUS = {"sm": 6, "md": 10}
FONT_SIZE = {"small": 12, "body": 13, "title": 15}       # пиксели: одинаково на Win7 и на Mac
ICON_SIZE = {"sm": 16, "md": 20}
CONTROL_HEIGHT = 30
UI_FONTS = ["Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans", "Tahoma", "Arial"]

LIGHT = {
    "bg": "#f4f6f9",              # фон окна
    "surface": "#ffffff",         # карточки, поля, списки
    "surface_alt": "#eef1f5",     # заголовки таблиц, наведение
    "border": "#d5dae1",
    "border_strong": "#b6bec9",
    "text": "#1c2430",
    "text_muted": "#647082",
    "text_disabled": "#a3acb8",
    "accent": "#2563eb",
    "accent_hover": "#1d4fd8",
    "accent_pressed": "#1a43b8",
    "accent_soft": "#e3ecfd",     # выделенная строка
    "on_accent": "#ffffff",
    "success": "#1a7f37",
    "warning": "#b26a00",
    "danger": "#c62828",
    "icon": "#465264",
}

DARK = {
    "bg": "#171a1f",
    "surface": "#20242b",
    "surface_alt": "#2a2f38",
    "border": "#363c47",
    "border_strong": "#4a5260",
    "text": "#e6e9ee",
    "text_muted": "#9aa4b2",
    "text_disabled": "#5f6875",
    "accent": "#5b8cff",
    "accent_hover": "#7aa2ff",
    "accent_pressed": "#4a78e6",
    "accent_soft": "#263556",
    "on_accent": "#0e1116",
    "success": "#4cc26b",
    "warning": "#e0a030",
    "danger": "#ff6b6b",
    "icon": "#b4bcc8",
}

THEMES = {"light": LIGHT, "dark": DARK}

QSS = u"""
QWidget { color: $text; font-size: ${font_body}px; }
QMainWindow, QDialog, QStatusBar, QToolBar { background: $bg; }
QWidget:disabled { color: $text_disabled; }
QToolTip { background: $text; color: $surface; border: none; padding: ${space_xs}px ${space_sm}px; }

QMenu { background: $surface; border: 1px solid $border; padding: ${space_xs}px; }
QMenu::item { padding: ${space_xs}px ${space_xl}px ${space_xs}px ${space_md}px; border-radius: ${radius_sm}px; }
QMenu::item:selected { background: $accent_soft; color: $text; }
QMenu::item:disabled { color: $text_disabled; }
QMenu::separator { height: 1px; background: $border; margin: ${space_xs}px ${space_sm}px; }

QToolBar { border: none; border-bottom: 1px solid $border; padding: ${space_xs}px ${space_sm}px; spacing: ${space_xs}px; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: ${radius_sm}px;
              padding: ${space_xs}px ${space_sm}px; }
QToolButton:hover { background: $surface_alt; }
QToolButton:pressed { background: $accent_soft; }

QPushButton { background: $surface; border: 1px solid $border; border-radius: ${radius_sm}px;
              padding: 0 ${space_md}px; min-height: ${button_inner}px; }
QPushButton:hover { background: $surface_alt; border-color: $border_strong; }
QPushButton:pressed { background: $accent_soft; }
QPushButton[accent="true"] { background: $accent; border-color: $accent; color: $on_accent; }
QPushButton[accent="true"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[accent="true"]:pressed { background: $accent_pressed; }
QPushButton:disabled { background: $bg; border-color: $border; color: $text_disabled; }
QPushButton::menu-indicator { image: url("$img_chevron_down"); width: ${icon_size_sm}px; height: ${icon_size_sm}px;
                              subcontrol-origin: padding; subcontrol-position: right center; right: ${space_sm}px; }

QPushButton[candidate="true"] { text-align: left; padding: 0 ${space_sm}px; }

QLineEdit, QTreeView, QListView, QSpinBox {
    background: $surface; border: 1px solid $border; border-radius: ${radius_sm}px;
    selection-background-color: $accent_soft; selection-color: $text; }
QLineEdit, QSpinBox { min-height: ${button_inner}px; padding: 0 ${space_sm}px; }
QLineEdit:focus, QSpinBox:focus { border-color: $accent; }
QLineEdit:disabled, QSpinBox:disabled { background: $bg; }
QSpinBox::up-button, QSpinBox::down-button { width: ${icon_size_sm}px; border: none; background: transparent; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: $surface_alt; }
QSpinBox::up-arrow { image: url("$img_chevron_up"); width: ${space_md}px; height: ${space_md}px; }
QSpinBox::down-arrow { image: url("$img_chevron_down"); width: ${space_md}px; height: ${space_md}px; }

QListView { outline: none; show-decoration-selected: 1; }
QListView::item { padding: ${space_xs}px; border: none; }
QListView::item:hover { background: $surface_alt; }
QListView::item:selected { background: $accent_soft; color: $text; }

QTreeView { outline: none; show-decoration-selected: 1; }
QTreeView::item { padding: ${space_xs}px 0; }
QTreeView::item:hover, QTreeView::branch:hover { background: $surface_alt; }
QTreeView::item:selected, QTreeView::branch:selected { background: $accent_soft; color: $text; }
QHeaderView { background: $surface_alt; border: none; }
QHeaderView::section { background: $surface_alt; color: $text_muted; border: none; border-right: 1px solid $border;
                       border-bottom: 1px solid $border; padding: ${space_xs}px ${space_sm}px; }

QScrollBar:vertical { background: transparent; width: ${space_md}px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: ${space_md}px; margin: 0; }
QScrollBar::handle { background: $border_strong; border-radius: 3px; margin: 3px; }
QScrollBar::handle:vertical { min-height: ${space_xl}px; }
QScrollBar::handle:horizontal { min-width: ${space_xl}px; }
QScrollBar::handle:hover { background: $text_muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QProgressBar { background: $surface_alt; border: none; border-radius: 3px; max-height: 6px; text-align: center; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }
QStatusBar { border-top: 1px solid $border; color: $text_muted; }
QStatusBar::item { border: none; }
QStatusBar QLabel { padding: 0 ${space_sm}px; }

QFrame#card { background: $surface; border: 1px solid $border; border-radius: ${radius_md}px; }
QFrame#line { background: $border; border: none; max-height: 1px; }
QLabel[muted="true"] { color: $text_muted; }
QLabel[role="title"] { font-weight: bold; }
"""


def tokens(name: str) -> Dict[str, object]:
    """Плоский словарь темы: цвета + space_*, radius_*, font_*, icon_size_*."""
    values: Dict[str, object] = dict(THEMES[name])
    for prefix, group in (("space", SPACE), ("radius", RADIUS), ("font", FONT_SIZE), ("icon_size", ICON_SIZE)):
        for key, value in group.items():
            values["%s_%s" % (prefix, key)] = value
    values["button_inner"] = CONTROL_HEIGHT - 2          # высота без рамки
    return values


def _svg(name: str, color: str) -> bytes:
    with io.open(os.path.join(ICON_DIR, name + ".svg"), encoding="utf-8") as f:
        return f.read().replace("currentColor", color).encode("utf-8")


def _pixmap(name: str, color: str, size: int, scale: float = 2.0) -> QPixmap:
    side = int(round(size * scale))
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(_svg(name, color))).render(painter, QRectF(0, 0, side, side))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def _write_colored(name: str, color: str, folder: str) -> str:
    """Перекрашенный значок файлом: QSS берёт картинки только по пути. Косые черты — прямые."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s_%s.svg" % (name, color.lstrip("#")))
    with open(path, "wb") as f:
        f.write(_svg(name, color))
    return path.replace("\\", "/")


class Theme:
    def __init__(self, name: str):
        self.name = name
        self.values = tokens(name)

    def color(self, key: str) -> str:
        return str(self.values[key])

    def qcolor(self, key: str) -> QColor:
        return QColor(self.color(key))

    def icon(self, name: str, color: str = "icon") -> QIcon:
        size = ICON_SIZE["md"]
        icon = QIcon()
        icon.addPixmap(_pixmap(name, self.color(color), size), QIcon.Normal)
        icon.addPixmap(_pixmap(name, self.color("text_disabled"), size), QIcon.Disabled)
        return icon

    def palette(self) -> QPalette:
        """Палитра Fusion: всё, что рисуется не через QSS."""
        palette = QPalette()
        for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "surface"),
                          (QPalette.AlternateBase, "bg"), (QPalette.Text, "text"), (QPalette.Button, "surface"),
                          (QPalette.ButtonText, "text"), (QPalette.BrightText, "on_accent"),
                          (QPalette.Highlight, "accent_soft"), (QPalette.HighlightedText, "text"),
                          (QPalette.ToolTipBase, "text"), (QPalette.ToolTipText, "surface"),
                          (QPalette.Link, "accent"), (QPalette.Mid, "border"), (QPalette.Dark, "border_strong")):
            palette.setColor(role, self.qcolor(key))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            palette.setColor(QPalette.Disabled, role, self.qcolor("text_disabled"))
        return palette


def resolve(mode: str, app) -> str:
    """Настройка theme → имя темы. «system» определяется по палитре системы до применения своей."""
    if mode in THEMES:
        return mode
    return "dark" if app.style().standardPalette().window().color().lightness() < 128 else "light"


def _app_font() -> QFont:
    database = QFontDatabase()
    available = {family.lower(): family for family in database.families()}
    font = QFont(QFontDatabase.systemFont(QFontDatabase.GeneralFont))
    for name in UI_FONTS:
        if name.lower() in available:
            font.setFamily(available[name.lower()])
            break
    font.setPixelSize(FONT_SIZE["body"])
    return font


def apply(app, name: str, cache_dir: Optional[str] = None) -> Theme:
    """Применяет тему ко всему приложению. cache_dir — куда положить перекрашенные стрелки для QSS."""
    theme = Theme(name)
    values = dict(theme.values)
    folder = cache_dir or os.path.join(tempfile.gettempdir(), "cv_theme")
    try:
        values["img_chevron_down"] = _write_colored("chevron-down", theme.color("text_muted"), folder)
        values["img_chevron_up"] = _write_colored("chevron-up", theme.color("text_muted"), folder)
    except OSError:                      # папка недоступна — оформление остаётся, только без стрелок
        values["img_chevron_down"] = values["img_chevron_up"] = ""
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setFont(_app_font())
    app.setStyleSheet(Template(QSS).substitute(values))
    return theme
