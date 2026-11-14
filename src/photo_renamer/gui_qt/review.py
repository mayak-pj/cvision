"""Пакетное переименование группы папок на PyQt5: таблица «текущее имя → новое имя» с миниатюрами.

Логика — в ReviewSession, та же, что у окна на tkinter; порядок работы и клавиши те же.
Чтение фото идёт в главном потоке с обработкой событий между кадрами: кэш (SQLite) принадлежит
потоку, в котором открыт, а сессия читает его и при подгрузке миниатюр.
"""

from typing import Dict, List, Optional

from PyQt5.QtCore import QEvent, QSize, Qt, QTimer
from PyQt5.QtGui import QFontMetrics, QIcon, QKeySequence
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton, QShortcut, QSizePolicy,
                             QSpinBox, QStyle, QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from .. import grouping, review_session
from ..review_session import ALTERNATIVES, STATUS_TEXT, THUMB_STEP, ReviewSession
from ..worker import Cancelled
from . import common, theme as theme_mod
from .common import MAX_SIDE_WIDTH, MAX_SIDES, MIN_SIDE_WIDTH  # noqa: F401 — тесты сверяют с ними ширину колонки
from .photoview import LargeView, to_pixmap

TITLE = "Переименование папок"
COLUMNS = (("Фото", review_session.SORT_PHOTO), ("Новое имя", review_session.SORT_NEW),
           ("Уверенность", review_session.SORT_CONFIDENCE), ("Состояние", review_session.SORT_STATUS))
COLUMN_PHOTO = 0
PHOTO_TEXT_WIDTH = 200    # сколько места в первом столбце остаётся под имя файла
NEW_NAME_WIDTH = 280
KIND_COLORS = {review_session.KIND_DOUBT: "warning", review_session.KIND_MANUAL: "accent",
               review_session.KIND_SKIP: "text_muted"}


class _ElidedLabel(QLabel):
    """Надпись, которая не распирает окно: длинный путь сокращается в середине, имя папки видно."""

    def __init__(self, text: str = ""):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.full_text = ""
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self.full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self) -> None:
        self.setText(QFontMetrics(self.font()).elidedText(self.full_text, Qt.ElideMiddle, self.width()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()


class _NoFocusFrame(QStyledItemDelegate):
    """Стиль Fusion обводит строку с фокусом рамкой с градиентом, и выделение выглядит иначе, чем в теме."""

    def paint(self, painter, option, index) -> None:
        option.state &= ~QStyle.State_HasFocus
        super().paint(painter, option, index)


class _ReviewTable(QTreeWidget):
    """Таблица фото со своими клавишами: цифры выбирают вариант, Enter подтверждает имя."""

    def __init__(self, window: "BatchWindow"):
        super().__init__()
        self.window_ = window

    def keyPressEvent(self, event) -> None:
        if event.modifiers() & ~Qt.KeypadModifier or not self.window_.handle_table_key(event.key()):
            super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        """Ctrl+колесо — размер фото. Без Ctrl прокрутка остаётся обычной."""
        if event.modifiers() & Qt.ControlModifier:
            step = THUMB_STEP if event.angleDelta().y() > 0 else -THUMB_STEP
            self.window_.set_thumb_size(self.window_.thumb_size + step)
            event.accept()
        else:
            super().wheelEvent(event)


class BatchWindow(QMainWindow):
    def __init__(self, parent, group: grouping.FolderGroup, app_config, theme: theme_mod.Theme, on_done=None,
                 names=None):
        super().__init__(parent)
        self.app_config = app_config
        self.theme = theme
        self.session = ReviewSession(group, app_config, names)
        self.on_done = on_done
        self.thumb_size = review_session.clamp_thumb_size(getattr(app_config, "review_thumb", 96))
        self._pixmaps: Dict[str, QIcon] = {}
        self._items: Dict[int, QTreeWidgetItem] = {}      # номер фото → строка таблицы
        self._thumb_queue: List[int] = []
        self._busy = False
        self._cancel = False
        self._closed = False
        self._finished = False

        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(TITLE)
        self.setMinimumSize(900, 600)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(1)
        self._thumb_timer.timeout.connect(self._load_next_thumbnail)
        self._build()
        self._set_icons()
        common.fit_to_screen(self)
        QTimer.singleShot(50, self.start)

    # --- построение окна ------------------------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        space = theme_mod.SPACE
        layout = QVBoxLayout(central)
        layout.setContentsMargins(space["md"], space["md"], space["md"], space["md"])
        layout.setSpacing(space["sm"])
        layout.addLayout(self._build_top())
        body = QHBoxLayout()
        body.setSpacing(space["md"])
        layout.addLayout(body, 1)
        self._build_table()
        body.addWidget(self.tree, 1)
        body.addWidget(self._build_side())
        layout.addLayout(self._build_bottom())
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.apply_folder)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo_edit)
        # отмена работает, где бы ни был курсор: поля ввода не забирают Ctrl+Z себе
        for field in (self.search_edit, self.sides_spin):
            field.installEventFilter(self)
        common.keep_focus_on_click(self)

    def _build_top(self) -> QHBoxLayout:
        top = QHBoxLayout()
        top.setSpacing(theme_mod.SPACE["sm"])
        self.header_label = _ElidedLabel("Подготовка…")
        self.header_label.setProperty("role", "title")
        top.addWidget(self.header_label, 1)
        top.addWidget(QLabel("Размер фото:"))
        self.smaller_btn = QToolButton()
        self.smaller_btn.setToolTip("Мельче (Ctrl+колесо)")
        self.smaller_btn.clicked.connect(lambda: self.set_thumb_size(self.thumb_size - THUMB_STEP))
        top.addWidget(self.smaller_btn)
        self.larger_btn = QToolButton()
        self.larger_btn.setToolTip("Крупнее (Ctrl+колесо)")
        self.larger_btn.clicked.connect(lambda: self.set_thumb_size(self.thumb_size + THUMB_STEP))
        top.addWidget(self.larger_btn)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(260)
        top.addWidget(self.progress)
        return top

    def _build_table(self) -> None:
        self.tree = _ReviewTable(self)
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setItemDelegate(_NoFocusFrame(self.tree))
        self.tree.setUniformRowHeights(True)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)  # строки высокие, по строке — рывками
        # Границы столбцов можно тянуть мышью: ни один столбец не растягивается, поэтому меняется
        # только тот, который тянут, а если столбцы не помещаются, внизу появляется полоса прокрутки
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionsMovable(False)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self.sort_by)
        self._show_sort_marks()
        metrics = self.tree.fontMetrics()
        # заголовки считаются со стрелкой сортировки, столбец состояния — по самой длинной надписи
        padding = 2 * theme_mod.SPACE["lg"]
        self.tree.setColumnWidth(1, NEW_NAME_WIDTH)
        self.tree.setColumnWidth(2, metrics.horizontalAdvance(COLUMNS[2][0] + " ↑") + padding)
        self.tree.setColumnWidth(3, max(metrics.horizontalAdvance(text) for text in STATUS_TEXT.values()) + padding)
        self._apply_thumb_size()
        self.tree.itemSelectionChanged.connect(self._on_row_select)
        self.tree.itemDoubleClicked.connect(lambda item, _column: self.open_large(item))

    def _build_side(self) -> QFrame:
        space = theme_mod.SPACE
        self.side = QFrame()
        self.side.setObjectName("card")
        self.side.setFixedWidth(MIN_SIDE_WIDTH)
        panel = QVBoxLayout(self.side)
        panel.setContentsMargins(space["md"], space["md"], space["md"], space["md"])
        panel.setSpacing(space["sm"])
        self.candidates = QVBoxLayout()
        self.candidates.setSpacing(space["xs"])
        panel.addLayout(self.candidates)
        panel.addWidget(common.line())

        header = QHBoxLayout()
        title = QLabel("Категория:")
        title.setProperty("role", "title")
        header.addWidget(title, 1)
        self.add_btn = QToolButton()
        self.add_btn.setToolTip("Добавить категорию в список")
        self.add_btn.clicked.connect(lambda: self.add_category())
        header.addWidget(self.add_btn)
        self.remove_btn = QToolButton()
        self.remove_btn.setToolTip("Удалить категорию из списка")
        self.remove_btn.clicked.connect(lambda: self.remove_category())
        header.addWidget(self.remove_btn)
        panel.addLayout(header)

        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(lambda _text: self._refresh_categories())
        panel.addWidget(self.search_edit)
        self.categories_list = QListWidget()
        self.categories_list.itemSelectionChanged.connect(lambda: self._show_sides(force=True))
        self.categories_list.itemDoubleClicked.connect(lambda _item: self.apply_selected_category())
        panel.addWidget(self.categories_list, 1)

        sides = QHBoxLayout()
        sides_label = QLabel("Ракурсов (фото на одну деталь):")
        sides_label.setWordWrap(True)
        sides.addWidget(sides_label, 1)
        self.sides_spin = QSpinBox()
        self.sides_spin.setRange(1, MAX_SIDES)
        self.sides_spin.setFixedWidth(72)
        sides.addWidget(self.sides_spin)
        panel.addLayout(sides)
        self.apply_category_btn = QPushButton("Применить к выбранным")
        self.apply_category_btn.clicked.connect(lambda: self.apply_selected_category())
        panel.addWidget(self.apply_category_btn)
        self.exclude_btn = QPushButton("Не переименовывать")
        self.exclude_btn.clicked.connect(lambda: self.exclude_selected())
        panel.addWidget(self.exclude_btn)
        return self.side

    def _build_bottom(self) -> QHBoxLayout:
        bottom = QHBoxLayout()
        bottom.setSpacing(theme_mod.SPACE["sm"])
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", True)
        # длинный путь в надписи не должен распирать окно: он переносится
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bottom.addWidget(self.status_label, 1)
        undo_key = QKeySequence("Ctrl+Z").toString(QKeySequence.NativeText)
        save_key = QKeySequence("Ctrl+S").toString(QKeySequence.NativeText)
        self.undo_edit_btn = self._bottom_button(bottom, "Отменить правку (%s)" % undo_key, self.undo_edit)
        self.undo_btn = self._bottom_button(bottom, "Отменить переименование", self.undo_folder)
        self.undo_btn.setEnabled(False)
        self.skip_btn = self._bottom_button(bottom, "Пропустить папку", self.skip_folder)
        self.apply_btn = self._bottom_button(bottom, "Переименовать папку (%s)" % save_key, self.apply_folder)
        self.apply_btn.setProperty("accent", True)
        self.close_btn = self._bottom_button(bottom, "Закрыть", self.close)
        return bottom

    @staticmethod
    def _bottom_button(row: QHBoxLayout, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(lambda _checked=False: slot())
        row.addWidget(button)
        return button

    def _set_icons(self) -> None:
        self.smaller_btn.setIcon(self.theme.icon("minus"))
        self.larger_btn.setIcon(self.theme.icon("plus"))
        self.add_btn.setIcon(self.theme.icon("plus"))
        self.remove_btn.setIcon(self.theme.icon("trash-2"))

    def set_theme(self, theme: theme_mod.Theme) -> None:
        """Оформление переключают в главном окне: значки и цвета строк рисуются заново."""
        self.theme = theme
        self._set_icons()
        if self.session.match is not None and not self._finished:
            self.render_rows()

    def eventFilter(self, watched, event) -> bool:
        """Своя отмена у полей ввода перехватила бы Ctrl+Z, и отменить правку фото было бы нельзя."""
        if event.type() == QEvent.ShortcutOverride and event.matches(QKeySequence.Undo):
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    # --- ход обработки ------------------------------------------------------------------

    def _progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        QApplication.processEvents()
        if self._cancel:
            raise Cancelled()

    def _set_busy(self, busy: bool) -> None:
        """Пока читаются фото, менять нечего: работает только «Закрыть», оно прерывает чтение."""
        self._busy = busy
        if busy:      # миниатюры прошлой папки больше не нужны, новые загрузятся после чтения
            self._thumb_timer.stop()
            self._thumb_queue = []
        for widget in (self.tree, self.side, self.smaller_btn, self.larger_btn, self.undo_edit_btn, self.undo_btn,
                       self.skip_btn, self.apply_btn):
            widget.setEnabled(not busy)
        if not busy:
            self.undo_btn.setEnabled(self.session.last_plan is not None)

    def _read(self, action, error_text: str) -> Optional[bool]:
        """Выполняет долгое чтение. True — прочитано, False — ошибка чтения, None — чтение прервано."""
        self._set_busy(True)
        try:
            action(self._progress)
            return True
        except Cancelled:
            return None
        except OSError as exc:
            self._set_busy(False)
            QMessageBox.critical(self, TITLE, "%s\n%s" % (error_text, exc))
            return False
        finally:
            self._set_busy(False)

    def start(self) -> None:
        if self._closed or self.session.model is not None or self._busy:   # защита от повторного запуска
            return
        if not self.session.group.reference_is_ready:
            QMessageBox.critical(self, TITLE, "Эталон группы ещё не переименован по шаблону.")
            self.close()
            return
        self.header_label.set_full_text("Чтение эталона: %s" % self.session.group.reference.path)
        if not self._read(self.session.load_model, "Не удалось прочитать эталон:"):
            self.close()
            return
        self._refresh_categories()
        self.next_folder()

    def next_folder(self) -> None:
        # Отмена последнего переименования остаётся доступной и после перехода к следующей папке:
        # отчёт по папке больше не показывается, и это единственная возможность передумать.
        while self.session.advance():
            self.header_label.set_full_text(self.session.header_text())
            self.status_label.setText("Чтение фото…")
            result = self._read(self.session.match_current, "Папка недоступна:")
            if result is None:
                self.close()
                return
            if result:
                self._show_folder()
                return
        self.finish()

    def _show_folder(self) -> None:
        self._pixmaps.clear()      # имена файлов в папках повторяются, а снимки в них другие
        self.session.resort()
        self.render_rows()
        self._select_position(0)   # новая папка открывается с первой строки
        self.tree.verticalScrollBar().setValue(0)
        self.tree.setFocus()
        self.load_thumbnails()

    # --- таблица ----------------------------------------------------------------------------

    def sort_by(self, column: int) -> None:
        """Щелчок по заголовку столбца: сортировка по нему, повторный щелчок — в обратную сторону."""
        if self.session.match is None or self._finished or self._busy:
            return
        self.session.sort_by(COLUMNS[column][1])
        self._show_sort_marks()
        self.render_rows()

    def _show_sort_marks(self) -> None:
        arrow = " ↑" if not self.session.sort_reverse else " ↓"
        self.tree.setHeaderLabels([title + (arrow if field == self.session.sort_column else "")
                                   for title, field in COLUMNS])

    def render_rows(self) -> None:
        # выделение запоминается по фото: строки создаются заново
        selected = set(self._selected_indexes())
        scroll = self.tree.verticalScrollBar().value()
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items.clear()
        for photo in self.session.display_photos():
            item = self._make_row(photo)
            self.tree.addTopLevelItem(item)
            self._items[photo.index] = item
            if photo.index in selected:
                item.setSelected(True)
        self.tree.blockSignals(False)
        self.tree.verticalScrollBar().setValue(scroll)
        self._update_counts()

    def _make_row(self, photo) -> QTreeWidgetItem:
        item = QTreeWidgetItem([photo.filename] + list(review_session.row_values(photo)))
        item.setSizeHint(COLUMN_PHOTO, QSize(1, self.thumb_size + theme_mod.SPACE["sm"]))
        icon = self._pixmaps.get(photo.filename)
        if icon is not None:
            item.setIcon(COLUMN_PHOTO, icon)
        color = KIND_COLORS.get(review_session.row_kind(photo))
        if color is not None:
            for column in range(len(COLUMNS)):
                item.setForeground(column, self.theme.qcolor(color))
        return item

    def _update_counts(self) -> None:
        self.status_label.setText(self.session.counts_text())

    def _apply_thumb_size(self) -> None:
        self.tree.setIconSize(QSize(self.thumb_size, self.thumb_size))
        self.tree.setColumnWidth(COLUMN_PHOTO, self.thumb_size + PHOTO_TEXT_WIDTH)

    def set_thumb_size(self, size: int) -> None:
        """Размер фото в строках таблицы. Миниатюры перерисовываются из кэша, файлы не перечитываются."""
        size = review_session.clamp_thumb_size(size)
        if size == self.thumb_size:
            return
        self.thumb_size = size
        self.app_config.review_thumb = size
        self._pixmaps.clear()
        self._apply_thumb_size()
        if self.session.match is not None and not self._finished:
            self.render_rows()
            self.load_thumbnails()

    def load_thumbnails(self) -> None:
        """Подгружает миниатюры по одной, не блокируя окно."""
        self._thumb_queue = [photo.index for photo in self.session.display_photos()]
        self._thumb_timer.start()

    def _load_next_thumbnail(self) -> None:
        if not self._thumb_queue:
            self._thumb_timer.stop()
            return
        index = self._thumb_queue.pop(0)
        photo = self.session.match.photos[index]
        try:
            pixmap = to_pixmap(self.session.thumbnail(photo), self.thumb_size, self.devicePixelRatioF())
        except Exception:      # файл не читается — строка остаётся без миниатюры
            return
        icon = QIcon(pixmap)
        icon.addPixmap(pixmap, QIcon.Selected)   # иначе выделенное фото затягивается цветом выделения
        self._pixmaps[photo.filename] = icon
        item = self._items.get(index)
        if item is not None:
            item.setIcon(COLUMN_PHOTO, icon)

    def open_large(self, item: QTreeWidgetItem) -> None:
        """Показывает фото крупно: стрелки листают папку, Esc закрывает."""
        index = next((k for k, row in self._items.items() if row is item), None)
        if index is not None and self.session.folder is not None:
            LargeView(self, self.session.folder.path, self.session.photo_names(), index)

    # --- выделение ----------------------------------------------------------------------------

    def _selected_indexes(self) -> List[int]:
        return [index for index, item in self._items.items() if item.isSelected()]

    def _selected_position(self) -> Optional[int]:
        """Место выбранной строки в таблице — не фото, а именно место."""
        rows = [self.tree.indexOfTopLevelItem(item) for item in self.tree.selectedItems()]
        return max(rows) if rows else None

    def _select_items(self, items: List[QTreeWidgetItem]) -> None:
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        if items:
            self.tree.setCurrentItem(items[0])
            for item in items:
                item.setSelected(True)
            self.tree.scrollToItem(items[0])
        self.tree.blockSignals(False)
        self._on_row_select()

    def _select_position(self, position: Optional[int]) -> None:
        """Выделяет строку, которая стоит на этом месте после перестроения таблицы.

        Исправленное фото может уехать на другое место, а на освободившееся встаёт следующий файл —
        его как раз надо проверить. Поэтому выделение держится за место, а не за фото.
        """
        count = self.tree.topLevelItemCount()
        if position is not None and count:
            self._select_items([self.tree.topLevelItem(min(position, count - 1))])

    def _on_row_select(self) -> None:
        self._show_sides()          # выделили другое число фото — подсказка про ракурсы другая
        self._clear_candidates()
        indexes = self._selected_indexes()
        if len(indexes) != 1:
            self.candidates.addWidget(QLabel("Выбрано фото: %d" % len(indexes)))
            return
        alternatives = self.session.alternatives(self.session.match.photos[indexes[0]])
        self.candidates.addWidget(QLabel("Похоже ещё на:" if alternatives else "Других вариантов нет"))
        for number, candidate in enumerate(alternatives[:ALTERNATIVES], start=1):
            self._candidate_button("%d — %s — %.0f%%" % (number, candidate.category, candidate.confidence),
                                   lambda _checked=False, c=candidate.category: self.apply_category(c))
        self._candidate_button("%d — Не переименовывать" % (ALTERNATIVES + 1),
                               lambda _checked=False: self.exclude_selected())

    def _clear_candidates(self) -> None:
        while self.candidates.count():
            widget = self.candidates.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _candidate_button(self, text: str, slot) -> None:
        button = QPushButton(text)
        button.setProperty("candidate", True)
        button.setToolTip(text)
        # фокус остаётся в таблице, чтобы следующее фото можно было разбирать с клавиатуры;
        # ширину задаёт панель: длинное название не должно её распирать
        button.setFocusPolicy(Qt.NoFocus)
        button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        button.clicked.connect(slot)
        self.candidates.addWidget(button)

    # --- правка ----------------------------------------------------------------------------

    def handle_table_key(self, key: int) -> bool:
        """Клавиши таблицы: 1–3 — варианты, 4 и 0, Delete, Backspace — не переименовывать, Enter — подтвердить."""
        if Qt.Key_1 <= key < Qt.Key_1 + ALTERNATIVES:
            self.apply_candidate(key - Qt.Key_1 + 1)
        elif key in (Qt.Key_1 + ALTERNATIVES, Qt.Key_0, Qt.Key_Delete, Qt.Key_Backspace):
            self.exclude_selected()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.confirm_selected()
        else:
            return False
        return True

    def _can_edit(self) -> bool:
        return self.session.match is not None and not self._busy and not self._finished

    def undo_edit(self) -> None:
        """Ctrl+Z — отменяет последнюю ручную правку: и выбор цифрой, и Enter, и кнопки, и список."""
        if not self._can_edit():
            return
        indexes = self.session.undo_edit()
        if indexes is None:
            self.status_label.setText("Отменять нечего")
            return
        self.render_rows()
        self._select_items([self._items[index] for index in indexes if index in self._items])

    def confirm_selected(self) -> None:
        """Enter — подтвердить предложенное имя как выбранное вручную и перейти к следующей строке."""
        indexes = self._selected_indexes()
        if not indexes or not self._can_edit():
            return
        position = self._selected_position()
        self.session.confirm(indexes)
        self.render_rows()
        self._select_position(position + 1)

    def apply_candidate(self, number: int) -> None:
        """Клавиши 1–3: каждому выбранному фото назначается его собственный вариант с этим номером."""
        indexes = self._selected_indexes()
        if not indexes or not self._can_edit():
            return
        position = self._selected_position()
        if self.session.apply_candidate(indexes, number):
            self.render_rows()
            self._select_position(position + 1)

    def apply_category(self, category: Optional[str]) -> None:
        indexes = self._selected_indexes()
        if not self._can_edit():
            return
        if not indexes:
            QMessageBox.information(self, TITLE, "Выберите фото в таблице.")
            return
        position = self._selected_position()
        self.session.apply_category(indexes, category, self.sides_spin.value())
        self.render_rows()
        self._select_position(position + 1)

    def exclude_selected(self) -> None:
        self.apply_category(None)

    # --- категории -------------------------------------------------------------------------

    def _current_category(self) -> Optional[str]:
        items = self.categories_list.selectedItems()
        return items[0].text() if items else None

    def _category_names(self) -> List[str]:
        return [self.categories_list.item(row).text() for row in range(self.categories_list.count())]

    def _refresh_categories(self) -> None:
        self.categories_list.blockSignals(True)
        self.categories_list.clear()
        self.categories_list.addItems(self.session.filter_categories(self.search_edit.text()))
        self.categories_list.blockSignals(False)
        self._fit_side_panel()

    def _fit_side_panel(self) -> None:
        names = self._category_names() or self.session.categories
        if names:
            self.side.setFixedWidth(common.side_width(self.categories_list, names))

    def add_category(self) -> None:
        """Деталь, которой нет в эталоне: набранное в поиске имя сразу попадает в список.

        Отдельного окна с «ОК» нет: человек уже ввёл название, увидел пустой список —
        значит, такой категории нет, — и нажал «+».
        """
        typed = self.search_edit.text().strip()
        if not typed:
            self.status_label.setText("Наберите название категории в поле справа и нажмите «+»")
            self.search_edit.setFocus()
            return
        try:
            name = self.session.add_category(typed)
        except ValueError as exc:
            QMessageBox.critical(self, TITLE, str(exc))
            return
        self.search_edit.clear()
        self._refresh_categories()
        self._select_category(name)

    def remove_category(self) -> None:
        """Удаляет имя из списка. Категории самого эталона не трогаются: их задаёт образец."""
        name = self._current_category()
        if name is None:
            QMessageBox.information(self, TITLE, "Выберите категорию в списке.")
            return
        if self.session.is_reference_category(name):
            QMessageBox.information(self, TITLE, "«%s» — категория эталона, она задаётся образцом.\n"
                                                 "Удалить её можно только из эталона." % name)
            return
        if self._ask("Удалить «%s» из списка категорий?" % name):
            self.session.remove_category(name)
            self._refresh_categories()

    def _select_category(self, name: str) -> None:
        self.categories_list.blockSignals(True)
        for item in self.categories_list.findItems(name, Qt.MatchExactly):
            self.categories_list.setCurrentItem(item)
            self.categories_list.scrollToItem(item)
        self.categories_list.blockSignals(False)
        self._show_sides()

    def _show_sides(self, force: bool = False) -> None:
        """Показывает, сколько ракурсов получит выбранная категория, — это можно исправить."""
        category = self._current_category()
        if category is None:
            return
        value = self.session.suggest_sides(category, len(self._selected_indexes()), self.sides_spin.value(), force)
        if value is not None:
            self.sides_spin.setValue(value)
            self.sides_spin.setEnabled(not self.session.is_reference_category(category))

    def apply_selected_category(self) -> None:
        category = self._current_category()
        if category is None:
            QMessageBox.information(self, TITLE, "Выберите категорию в списке справа.")
            return
        self.apply_category(category)

    # --- переименование ------------------------------------------------------------------

    def _ask(self, question: str) -> bool:
        return common.ask(self, TITLE, question)

    def apply_folder(self) -> None:
        if not self._can_edit():
            return
        try:
            plan = self.session.build_plan()
        except OSError as exc:
            QMessageBox.critical(self, TITLE, "Папка недоступна:\n%s" % exc)
            return
        if not plan.pending:
            QMessageBox.information(self, TITLE, "Переименовывать нечего.")
            self.next_folder()
            return
        unresolved = self.session.unresolved_count()
        if unresolved and not self._ask(review_session.unresolved_question(unresolved, len(plan.pending))):
            return
        self.session.apply_plan(plan)
        self.undo_btn.setEnabled(True)
        self.next_folder()

    def undo_folder(self) -> None:
        summary = self.session.undo_folder()
        if summary is not None:
            self.undo_btn.setEnabled(False)
            self.status_label.setText(summary)

    def skip_folder(self) -> None:
        if not self._can_edit():
            return
        self.session.skip_folder()
        self.next_folder()

    def finish(self) -> None:
        self._finished = True
        self._thumb_queue = []
        self.header_label.set_full_text("Готово")
        self.tree.clear()
        self._items.clear()
        self._clear_candidates()
        for widget in (self.tree, self.side, self.undo_edit_btn, self.skip_btn, self.apply_btn):
            widget.setEnabled(False)
        self.status_label.setText(self.session.finish_text())

    def closeEvent(self, event) -> None:
        if self._busy:             # чтение прервётся, и окно закроется само
            self._cancel = True
            event.ignore()
            return
        if not self._closed:
            self._closed = True
            self._thumb_timer.stop()
            self.session.close()
            if self.on_done:
                self.on_done()
        event.accept()
