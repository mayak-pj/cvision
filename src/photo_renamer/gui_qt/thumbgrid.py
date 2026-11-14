"""Сетка миниатюр на PyQt5 с выделением как в Проводнике.

Щелчок, Ctrl+щелчок, Shift+щелчок, рамка мышью и Ctrl+A — стандартное поведение QListView, Esc снимает
выделение, Ctrl+колесо меняет размер. Ячейки рисуются по мере показа: на папках в сотни фото смена
размера не пересчитывает то, чего не видно. Миниатюры читаются в фоновой задаче со своим кэшем,
потому что соединение SQLite принадлежит создавшему его потоку.
"""

from typing import Callable, List, Optional, Sequence, Set, Tuple

from PyQt5.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QFontMetrics, QPen, QTextLayout, QTextOption
from PyQt5.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from . import theme as theme_mod
from .photoview import to_pixmap

MIN_SIZE, MAX_SIZE = 100, 400
DEFAULT_SIZE = 160
WHEEL_STEP = 20
GAP = 8                # между ячейками
PADDING = 6            # от рамки ячейки до содержимого
LABEL_HEIGHT = 36      # две строки подписи


class _Cell:
    """Одно фото в сетке. Переезжает вместе с миниатюрой, когда сетка переставляется."""

    def __init__(self, name: str, path: str, template: bool, row: int):
        self.name = name
        self.path = path
        self.template = template          # имя уже по шаблону
        self.row = row
        self.image = None                 # None — ещё не прочитано, False — прочитать не удалось
        self.pixmap = None
        self.pixmap_size = 0


class _Model(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.cells: List[_Cell] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.cells)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role in (Qt.DisplayRole, Qt.ToolTipRole):
            return self.cells[index.row()].name
        return None

    def replace(self, cells: List[_Cell]) -> None:
        self.beginResetModel()
        self.cells = cells
        for row, cell in enumerate(cells):
            cell.row = row
        self.endResetModel()

    def changed(self, cell: _Cell) -> None:
        index = self.index(cell.row)
        self.dataChanged.emit(index, index)


def label_lines(text: str, metrics: QFontMetrics, font, width: int) -> str:
    """Подпись в две строки. Сокращается середина второй: номер экземпляра и фото стоят в конце имени."""
    layout = QTextLayout(text, font)
    layout.setTextOption(QTextOption(Qt.AlignLeft))
    option = layout.textOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    first = layout.createLine()
    first.setLineWidth(width)
    length = first.textLength()
    layout.endLayout()
    rest = text[length:].strip()
    if not rest:
        return text
    return "%s\n%s" % (text[:length].strip(), metrics.elidedText(rest, Qt.ElideMiddle, width))


class _CellDelegate(QStyledItemDelegate):
    def __init__(self, grid: "ThumbnailGrid"):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index) -> QSize:
        return self.grid.gridSize()

    def paint(self, painter, option, index) -> None:
        grid = self.grid
        theme = grid.theme
        cell = grid.model_.cells[index.row()]
        size = grid.cell_size
        card = option.rect.adjusted(GAP // 2, GAP // 2, -(GAP // 2), -(GAP // 2))
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        fill = "accent_soft" if selected else "surface_alt" if hovered else "surface"

        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        painter.setPen(QPen(theme.qcolor("accent" if selected else "border"), 2 if selected else 1))
        painter.setBrush(theme.qcolor(fill))
        radius = theme_mod.RADIUS["sm"]
        painter.drawRoundedRect(card.adjusted(1, 1, -1, -1), radius, radius)

        photo = QRect(card.left() + PADDING, card.top() + PADDING, size, size)
        pixmap = grid.pixmap_for(cell)
        if pixmap is not None:
            ratio = pixmap.devicePixelRatio()
            width, height = int(pixmap.width() / ratio), int(pixmap.height() / ratio)
            painter.drawPixmap(photo.left() + (size - width) // 2, photo.top() + (size - height) // 2, pixmap)
        else:
            painter.setPen(theme.qcolor("text_muted"))
            painter.drawText(photo, Qt.AlignCenter, "…" if cell.image is None else "нет\nпревью")

        label = QRect(photo.left(), photo.bottom() + 1, size, LABEL_HEIGHT)
        painter.setPen(theme.qcolor("success" if cell.template else "text"))
        painter.setFont(option.font)
        painter.drawText(label, Qt.AlignHCenter | Qt.AlignVCenter,
                         label_lines(cell.name, QFontMetrics(option.font), option.font, size))
        painter.restore()


class ThumbnailGrid(QListView):
    selection_changed = pyqtSignal()
    photo_activated = pyqtSignal(int)          # двойной щелчок: номер ячейки
    load_progress = pyqtSignal(int, int)       # прочитано, всего

    def __init__(self, theme: theme_mod.Theme, worker, open_cache: Callable[[], object],
                 cell_size: int = DEFAULT_SIZE):
        """open_cache вызывается внутри фонового потока и создаёт там свой кэш."""
        super().__init__()
        self.theme = theme
        self.worker = worker
        self.open_cache = open_cache
        self.cell_size = cell_size
        self.model_ = _Model()
        self._generation = 0      # задача прошлого поколения читала уже устаревшие пути
        self._restart = False
        self._stopped = False
        self._anchor: Optional[int] = None     # откуда считается диапазон при Shift+щелчке

        self.setModel(self.model_)
        self.setItemDelegate(_CellDelegate(self))
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)     # после IconMode: иначе ячейки можно перетаскивать
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionRectVisible(True)
        self.setDragEnabled(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setMouseTracking(True)            # подсветка ячейки под курсором
        self._apply_grid_size()
        self.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit())
        self.doubleClicked.connect(lambda index: self.photo_activated.emit(index.row()))

    # --- содержимое ------------------------------------------------------------------------

    @property
    def cells(self) -> List[_Cell]:
        return self.model_.cells

    def set_items(self, items: Sequence[Tuple[str, str]], template: Sequence[bool] = ()) -> None:
        flags = list(template) + [False] * (len(items) - len(template))
        self.model_.replace([_Cell(name, path, flags[row], row) for row, (name, path) in enumerate(items)])
        self._anchor = None
        self.selection_changed.emit()
        self._start_loading()

    def reorder(self, order: Sequence[int], items: Sequence[Tuple[str, str]], template: Sequence[bool] = ()) -> None:
        """Показывает те же фото в другом порядке, не перечитывая миниатюры.

        order[i] — какая ячейка прежней сетки становится i-й. Вместе с ячейками переезжают
        прочитанные миниатюры и выделение; прокрутка остаётся на месте.
        """
        selected = {self.cells[row] for row in self.selected_rows()}
        scroll = self.verticalScrollBar().value()
        flags = list(template) + [False] * (len(items) - len(template))
        cells = [self.cells[old] for old in order]
        for cell, (name, path), named in zip(cells, items, flags):
            cell.name, cell.path, cell.template = name, path, named
        self.model_.replace(cells)
        self._anchor = None
        self.doItemsLayout()
        self.verticalScrollBar().setValue(scroll)
        self.set_selection([cell.row for cell in selected])
        self.selection_changed.emit()
        self._start_loading()     # задача, которая ещё идёт, читает по старым именам

    # --- выделение ---------------------------------------------------------------------------

    def selected_rows(self) -> List[int]:
        return sorted(index.row() for index in self.selectedIndexes())

    @property
    def selection(self) -> Set[int]:
        return set(self.selected_rows())

    def selected_names(self) -> List[str]:
        return [self.cells[row].name for row in self.selected_rows()]

    def set_selection(self, rows: Sequence[int]) -> None:
        self.clearSelection()
        for row in rows:
            if 0 <= row < len(self.cells):
                self.selectionModel().select(self.model_.index(row), self.selectionModel().Select)

    def select_all(self) -> None:
        self.selectAll()

    def clear_selection(self) -> None:
        self.clearSelection()

    # --- размер ---------------------------------------------------------------------------------

    def _apply_grid_size(self) -> None:
        extra = 2 * PADDING + GAP
        self.setGridSize(QSize(self.cell_size + extra, self.cell_size + LABEL_HEIGHT + extra))

    def set_cell_size(self, size: int) -> None:
        size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        if size != self.cell_size:
            self.cell_size = size
            self._apply_grid_size()
            self.viewport().update()

    def set_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        self.viewport().update()

    def pixmap_for(self, cell: _Cell):
        """Картинка ячейки в текущем размере. Строится при первом показе, не при загрузке."""
        if not cell.image:
            return None
        if cell.pixmap is None or cell.pixmap_size != self.cell_size:
            cell.pixmap = to_pixmap(cell.image, self.cell_size, self.devicePixelRatioF())
            cell.pixmap_size = self.cell_size
        return cell.pixmap

    # --- мышь и клавиши ---------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        """Shift+щелчок выделяет ровно диапазон: Qt добавил бы его к выделенному раньше через Ctrl."""
        index = self.indexAt(event.pos())
        if index.isValid() and event.button() == Qt.LeftButton:
            if event.modifiers() & Qt.ShiftModifier and self._anchor is not None:
                low, high = sorted((self._anchor, index.row()))
                keep = self.selected_rows() if event.modifiers() & Qt.ControlModifier else []
                self.set_selection(keep + list(range(low, high + 1)))
                self.selectionModel().setCurrentIndex(index, self.selectionModel().NoUpdate)
                event.accept()
                return
            self._anchor = index.row()
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:
        """Ctrl+колесо — размер миниатюр. Без Ctrl прокрутка остаётся обычной."""
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.set_cell_size(self.cell_size + (WHEEL_STEP if delta > 0 else -WHEEL_STEP))
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.clearSelection()
        else:
            super().keyPressEvent(event)

    # --- загрузка миниатюр ------------------------------------------------------------------

    def loaded_count(self) -> int:
        return sum(1 for cell in self.cells if cell.image is not None)

    def _report(self) -> None:
        self.load_progress.emit(self.loaded_count(), len(self.cells))

    def _start_loading(self) -> None:
        self._generation += 1
        pending = [(cell, cell.path) for cell in self.cells if cell.image is None]
        if not pending:
            self._report()
            return
        if self.worker.busy():    # новая задача запустится, когда прервётся прежняя
            self._restart = True
            self.worker.cancel()
            return
        generation = self._generation
        open_cache = self.open_cache

        def job(post, cancelled):
            cache = open_cache()
            try:
                for cell, path in pending:
                    if cancelled():
                        return
                    try:
                        image = cache.thumbnail(path)
                        image.load()          # распаковка тоже долгая, пусть идёт здесь
                    except Exception:         # нечитаемое фото не должно ломать сетку
                        image = False
                    post((generation, cell, image))
            finally:
                cache.close()

        self.worker.start(job, self._on_loaded, self._on_load_finished)

    def _on_loaded(self, message) -> None:
        generation, cell, image = message
        if self._stopped or generation != self._generation:
            return
        cell.image = image
        self.model_.changed(cell)
        self._report()

    def _on_load_finished(self, _result, error: Optional[BaseException]) -> None:
        if self._stopped:
            return
        if self._restart:
            self._restart = False
            self._start_loading()
        elif error is not None:   # не открылся кэш: иначе ход загрузки так и висел бы незавершённым
            for cell in self.cells:
                if cell.image is None:
                    cell.image = False
            self.viewport().update()
            self._report()

    def stop(self) -> None:
        """Перед закрытием окна: сообщения задачи больше не принимаются, её кэш она закроет сама."""
        self._stopped = True
        self.worker.cancel()
        self.worker.wait(5)
