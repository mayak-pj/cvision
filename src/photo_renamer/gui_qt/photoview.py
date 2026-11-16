"""Просмотр фото крупно на PyQt5. Одно окно на редактор эталона и на пакетное переименование."""

import os
from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import QImage, QKeySequence, QPainter, QPixmap
from PyQt5.QtWidgets import QDialog, QLabel, QShortcut, QSizePolicy, QVBoxLayout, QWidget

from .. import features as feat
from . import theme as theme_mod

VIEW_SIZE = 1600      # до какого размера читается фото для просмотра
ZOOM_SIZE = 6000      # при приближении фото перечитывается крупнее: видны надписи и серийные номера
ZOOM_STEP = 1.25      # во сколько раз приближает один щелчок колеса
MAX_ZOOM = 12.0       # во сколько раз крупнее, чем «во весь размер окна»


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


class PhotoCanvas(QWidget):
    """Фото во весь размер окна; колесо приближает к точке под курсором, мышь двигает,
    двойной щелчок возвращает «во весь размер»."""

    def __init__(self, on_zoom: Optional[Callable[[], None]] = None):
        super().__init__()
        self.pixmap: Optional[QPixmap] = None
        self.zoom = 1.0                     # 1 — фото целиком в окне
        self.center = QPointF(0.5, 0.5)     # какая точка фото (в долях) стоит в середине окна
        self.on_zoom = on_zoom              # первое приближение: можно перечитать фото крупнее
        self._drag = None
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)   # размер задаёт окно
        self.setMinimumSize(200, 200)
        self.setMouseTracking(False)

    def set_pixmap(self, pixmap: Optional[QPixmap], keep_view: bool = False) -> None:
        self.pixmap = pixmap
        if not keep_view:
            self.reset()
        self.update()

    def reset(self) -> None:
        self.zoom = 1.0
        self.center = QPointF(0.5, 0.5)
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def _photo_size(self):
        """Размер фото на экране (логические точки) при текущем приближении."""
        width = self.pixmap.width() / self.pixmap.devicePixelRatio()
        height = self.pixmap.height() / self.pixmap.devicePixelRatio()
        fit = min(self.width() / width, self.height() / height)
        return width * fit * self.zoom, height * fit * self.zoom

    def photo_rect(self) -> QRectF:
        width, height = self._photo_size()
        left = self.width() / 2 - self.center.x() * width
        top = self.height() / 2 - self.center.y() * height
        return QRectF(left, top, width, height)

    def _clamp(self) -> None:
        """Край фото не уходит внутрь окна дальше, чем нужно: пустоты вокруг не бывает."""
        width, height = self._photo_size()
        for axis, size, view in (("x", width, self.width()), ("y", height, self.height())):
            half = view / 2 / size
            value = getattr(self.center, axis)()
            value = 0.5 if half >= 0.5 else min(max(value, half), 1 - half)
            getattr(self.center, "set" + axis.upper())(value)

    def paintEvent(self, _event) -> None:
        if self.pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(self.photo_rect(), self.pixmap, QRectF(self.pixmap.rect()))

    def zoom_at(self, factor: float, point: QPointF) -> None:
        """Приближает так, чтобы точка фото под курсором осталась под курсором."""
        if self.pixmap is None:
            return
        rect = self.photo_rect()
        spot = QPointF((point.x() - rect.left()) / rect.width(), (point.y() - rect.top()) / rect.height())
        new_zoom = min(max(self.zoom * factor, 1.0), MAX_ZOOM)
        if new_zoom == self.zoom:
            return
        first = self.zoom == 1.0 and new_zoom > 1.0
        self.zoom = new_zoom
        width, height = self._photo_size()
        self.center = QPointF(spot.x() - (point.x() - self.width() / 2) / width,
                              spot.y() - (point.y() - self.height() / 2) / height)
        self._clamp()
        self.setCursor(Qt.OpenHandCursor if self.zoom > 1.0 else Qt.ArrowCursor)
        self.update()
        if first and self.on_zoom:
            self.on_zoom()

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.zoom_at(ZOOM_STEP ** steps, QPointF(event.pos()))
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.zoom > 1.0:
            self._drag = (QPointF(event.pos()), QPointF(self.center))
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            start, center = self._drag
            width, height = self._photo_size()
            moved = QPointF(event.pos()) - start
            self.center = QPointF(center.x() - moved.x() / width, center.y() - moved.y() / height)
            self._clamp()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.OpenHandCursor if self.zoom > 1.0 else Qt.ArrowCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.pixmap is not None:
            self._clamp()


class LargeView(QDialog):
    """Просмотр фото крупно: стрелки — листать, колесо — приблизить, мышь — двигать фото,
    двойной щелчок — снова целиком, Esc — закрыть.

    Окно поднимается над тем, из которого его открыли. Фото с сетевого диска читается небыстро
    (15 МБ), поэтому окно показывается сразу с надписью «загрузка», а прочитанное фото держится
    в памяти: при изменении размера окна файл заново не читается.

    on_show(имя) — какое фото сейчас открыто (редактор выделяет его в сетке); keys — сочетания
    клавиш, которые работают и в этом окне (в редакторе Ctrl+S и Ctrl+Z переименовывают открытое фото).
    on_key(клавиша) — клавиши без Ctrl (цифры, Enter в окне переименования); True — клавиша обработана.
    hint(имя) — строка под фото: в окне переименования новое имя и варианты на цифры.
    """

    def __init__(self, parent, folder: str, photos: List[str], index: int,
                 on_show: Optional[Callable[[str], None]] = None,
                 keys: Optional[Dict[str, Callable[[], None]]] = None,
                 on_key: Optional[Callable[[int], bool]] = None,
                 hint: Optional[Callable[[str], str]] = None):
        super().__init__(parent)
        self.folder = folder
        self.photos = list(photos)
        self.index = index
        self.on_show = on_show
        self.on_key = on_key
        self.hint_text = hint
        self._pixmap: Optional[QPixmap] = None      # прочитанное фото
        self._loaded: Optional[str] = None          # какое фото прочитано (имя файла)
        self._zoomed: Optional[str] = None          # какое фото прочитано крупно, для приближения
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        margin = theme_mod.SPACE["sm"]
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(margin)
        self.canvas = PhotoCanvas(on_zoom=self._load_large)
        layout.addWidget(self.canvas, 1)
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.caption)
        self.hint = QLabel()
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setVisible(hint is not None)
        layout.addWidget(self.hint)
        for sequence, action in (keys or {}).items():
            QShortcut(QKeySequence(sequence), self, activated=action)
        self.caption.setText("%s — загрузка…" % self.photos[self.index])
        self.setWindowTitle(self.photos[self.index])
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self.show_photo)

    @property
    def name(self) -> str:
        return self.photos[self.index]

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Left:
            self.step(-1)
        elif event.key() == Qt.Key_Right:
            self.step(1)
        elif (self.on_key is not None and not event.modifiers() & ~Qt.KeypadModifier
              and self.on_key(event.key())):
            pass
        else:
            super().keyPressEvent(event)        # Esc закрывает окно

    def step(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.photos)
        self.show_photo()

    def set_photos(self, photos: List[str], renamed: Dict[str, str]) -> None:
        """Фото переименовали, пока окно открыто: тот же кадр под новым именем, листание — в новом порядке."""
        current = renamed.get(self.name, self.name)
        self.photos = list(photos)
        self.index = self.photos.index(current) if current in self.photos else min(self.index, len(self.photos) - 1)
        for attr in ("_loaded", "_zoomed"):
            value = getattr(self, attr)
            if value is not None:
                setattr(self, attr, renamed.get(value, value))
        self.show_photo()

    def show_name(self, photos: List[str], name: str) -> None:
        """Другой порядок листания и фото, которое показать (окно переименования: после правки
        выделение уходит на следующую строку таблицы, и просмотр идёт за ним)."""
        self.photos = list(photos)
        if name in self.photos:
            self.index = self.photos.index(name)
        else:
            self.index = min(self.index, len(self.photos) - 1)
        self.show_photo()

    def _read(self, name: str, size: int) -> QPixmap:
        return to_pixmap(feat.load_image(os.path.join(self.folder, name), size))

    def show_photo(self) -> None:
        name = self.name
        self.setWindowTitle(name)
        if self._loaded != name:
            self.caption.setText("%s — загрузка…" % name)
            # только перерисовка: стрелка, нажатая во время чтения, обработается после него,
            # а не вклинится в середину и не покажет одно фото под подписью другого
            self.caption.repaint()
            try:
                # крупный просмотр читает фото в большем размере, чем миниатюра из кэша
                self._pixmap = self._read(name, VIEW_SIZE)
            except Exception as exc:
                self._pixmap = None
                self._loaded = None
                self.canvas.set_pixmap(None)
                self.caption.setText("Не удалось открыть: %s" % exc)
                if self.on_show:
                    self.on_show(name)
                self._show_hint()
                return
            self._loaded, self._zoomed = name, None
            self.canvas.set_pixmap(self._pixmap)
        self.caption.setText("%s   (%d из %d)" % (name, self.index + 1, len(self.photos)))
        if self.on_show:
            self.on_show(name)
        self._show_hint()

    def _show_hint(self) -> None:
        if self.hint_text is not None:
            self.hint.setText(self.hint_text(self.name))

    def _load_large(self) -> None:
        """Первое приближение: фото перечитывается крупнее, иначе надписи на детали расплываются."""
        name = self._loaded
        if name is None or self._zoomed == name:
            return
        self.caption.setText("%s — загрузка крупнее…" % name)
        self.caption.repaint()
        try:
            self._pixmap = self._read(name, ZOOM_SIZE)
        except Exception:            # не вышло — остаётся то, что было прочитано
            pass
        else:
            self._zoomed = name
            self.canvas.set_pixmap(self._pixmap, keep_view=True)
        self.caption.setText("%s   (%d из %d)" % (name, self.index + 1, len(self.photos)))
