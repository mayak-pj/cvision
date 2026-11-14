"""То, что нужно сразу нескольким окнам на PyQt5."""

from typing import Sequence

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication, QFrame, QMessageBox, QPushButton

MAX_SIDES = 9999          # не 10: число подставляется и по числу выделенных фото, обрезать его нельзя
SIDE_PADDING = 64         # полоса прокрутки, рамки и отступы правой колонки
MIN_SIDE_WIDTH, MAX_SIDE_WIDTH = 280, 460


class WorkerHost:
    """То, что фоновой задаче нужно от окна: отложенный вызов и обработка событий."""

    @staticmethod
    def after(milliseconds: int, function, *args) -> None:
        QTimer.singleShot(milliseconds, lambda: function(*args))

    @staticmethod
    def update() -> None:
        QApplication.processEvents()


def ask(parent, title: str, question: str) -> bool:
    """Вопрос с русскими «Да» и «Нет»: перевода стандартных кнопок Qt в программе нет."""
    box = QMessageBox(QMessageBox.Question, title, question, QMessageBox.NoButton, parent)
    yes = box.addButton("Да", QMessageBox.YesRole)
    box.setDefaultButton(yes)
    box.addButton("Нет", QMessageBox.NoRole)
    box.exec_()
    return box.clickedButton() is yes


def keep_focus_on_click(window) -> None:
    """Щелчок по кнопке оставляет фокус на месте: на Windows она забрала бы его, и клавиши таблицы
    и сетки перестали бы работать. Клавишей Tab до кнопок по-прежнему можно дойти."""
    for button in window.findChildren(QPushButton):
        button.setFocusPolicy(Qt.TabFocus)


def fit_to_screen(window) -> None:
    """Окно занимает почти весь экран, но с отступом сверху: шапка не уходит под родительское окно."""
    screen = QApplication.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry()
    width = max(window.minimumWidth(), min(int(area.width() * 0.92), area.width() - 20))
    height = max(window.minimumHeight(), min(int(area.height() * 0.88), area.height() - 20))
    window.setGeometry(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2,
                       width, height)


def line() -> QFrame:
    """Тонкая разделительная линия."""
    frame = QFrame()
    frame.setObjectName("line")
    frame.setFixedHeight(1)
    return frame


def side_width(list_widget, names: Sequence[str]) -> int:
    """Ширина правой колонки — по самому длинному названию: обрезанный список читать невозможно."""
    needed = max((list_widget.fontMetrics().horizontalAdvance(name) for name in names), default=0)
    return max(MIN_SIDE_WIDTH, min(needed + SIDE_PADDING, MAX_SIDE_WIDTH))
