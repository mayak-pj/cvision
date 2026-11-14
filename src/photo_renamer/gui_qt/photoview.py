"""Просмотр фото крупно на PyQt5. Одно окно на редактор эталона и на пакетное переименование."""

import os
from typing import List, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QDialog, QLabel, QSizePolicy, QVBoxLayout

from .. import features as feat
from . import theme as theme_mod

VIEW_SIZE = 1600      # до какого размера читается фото для просмотра


def to_pixmap(image, size: Optional[int] = None, ratio: float = 1.0) -> QPixmap:
    """Изображение PIL → QPixmap не больше size логических точек (без PIL.ImageQt).

    ratio — плотность экрана: на экранах Retina на одну точку приходится несколько пикселей.
    Если у картинки столько пикселей нет, берётся меньшая плотность: размер на экране важнее.
    """
    rgb = image.convert("RGB")
    if size is not None:
        ratio = max(1.0, min(ratio, max(rgb.size) / float(size)))
        rgb.thumbnail((int(size * ratio), int(size * ratio)))
    data = rgb.tobytes()
    width, height = rgb.size
    pixmap = QPixmap.fromImage(QImage(data, width, height, 3 * width, QImage.Format_RGB888))
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


class LargeView(QDialog):
    """Просмотр фото крупно: стрелки — листать, Esc — закрыть.

    Окно поднимается над тем, из которого его открыли. Фото с сетевого диска читается небыстро
    (15 МБ), поэтому окно показывается сразу с надписью «загрузка», а прочитанное фото держится
    в памяти: при изменении размера окна файл заново не читается.
    """

    def __init__(self, parent, folder: str, photos: List[str], index: int):
        super().__init__(parent)
        self.folder = folder
        self.photos = photos
        self.index = index
        self._pixmap: Optional[QPixmap] = None      # прочитанное фото
        self._loaded: Optional[int] = None          # для какого кадра оно прочитано
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        margin = theme_mod.SPACE["sm"]
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(margin)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        # размер задаёт окно, а не картинка: иначе окно нельзя было бы уменьшить
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.label.setMinimumSize(200, 200)
        layout.addWidget(self.label, 1)
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.caption)
        self.caption.setText("%s — загрузка…" % self.photos[self.index])
        self.setWindowTitle(self.photos[self.index])
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self.show_photo)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Left:
            self.step(-1)
        elif event.key() == Qt.Key_Right:
            self.step(1)
        else:
            super().keyPressEvent(event)        # Esc закрывает окно

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._draw()

    def step(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.photos)
        self.show_photo()

    def show_photo(self) -> None:
        name = self.photos[self.index]
        self.setWindowTitle(name)
        if self._loaded != self.index:
            self.caption.setText("%s — загрузка…" % name)
            # только перерисовка: стрелка, нажатая во время чтения, обработается после него,
            # а не вклинится в середину и не покажет одно фото под подписью другого
            self.caption.repaint()
            try:
                # крупный просмотр читает фото в большем размере, чем миниатюра из кэша
                self._pixmap = to_pixmap(feat.load_image(os.path.join(self.folder, name), VIEW_SIZE))
            except Exception as exc:
                self._pixmap = None
                self._loaded = None
                self.label.clear()
                self.caption.setText("Не удалось открыть: %s" % exc)
                return
            self._loaded = self.index
        self.caption.setText("%s   (%d из %d)" % (name, self.index + 1, len(self.photos)))
        self._draw()

    def _draw(self) -> None:
        if self._pixmap is None:
            return
        ratio = self.devicePixelRatioF()
        scaled = self._pixmap.scaled(self.label.size() * ratio, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.label.setPixmap(scaled)
