"""Редактор эталона на PyQt5: выделить фото → выбрать категорию и число ракурсов → переименовать.

Логика — в EditorSession.
"""

from typing import Optional

from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
                             QProgressBar, QPushButton, QShortcut, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout,
                             QWidget)

from ..cache import PhotoCache
from ..editor_session import EditorSession
from ..names import NamesList
from ..worker import Worker
from . import common, theme as theme_mod
from .photoview import LargeView
from .thumbgrid import ThumbnailGrid

TITLE = "Эталон"
ZOOM_STEP = 40


class ReferenceEditor(QMainWindow):
    def __init__(self, parent, folder: str, app_config, theme: theme_mod.Theme, names: NamesList, on_done=None):
        super().__init__(parent)
        self.folder = folder
        self.app_config = app_config
        self.theme = theme
        self.on_done = on_done
        self.session = EditorSession(folder, app_config, names, self._open_cache())
        self.names = names
        self.worker = Worker(common.WorkerHost())
        self._started = False
        self._closed = False
        self._view: Optional[LargeView] = None     # открытое крупно фото: переименовывается оно

        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("%s: %s" % (TITLE, folder))
        self.setMinimumSize(900, 560)
        self._build()
        self._set_icons()
        common.fit_to_screen(self)
        QTimer.singleShot(50, self.start)      # окно видно сразу, список фото читается уже в нём

    def _open_cache(self) -> PhotoCache:
        return PhotoCache(self.app_config.cache_path(), self.app_config.cache_max_mb)

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
        self.grid = ThumbnailGrid(self.theme, self.worker, self._open_cache)
        self.grid.selection_changed.connect(self._on_selection)
        self.grid.photo_activated.connect(self._open_large)
        self.grid.load_progress.connect(self._on_load_progress)
        body.addWidget(self.grid, 1)
        body.addWidget(self._build_side())
        layout.addLayout(self._build_bottom())
        # Ctrl+A и Ctrl+Z в поле ввода остаются за полем (как было и в прежних окнах), Ctrl+S работает отовсюду:
        # набрать категорию и тут же нажать сочетание — обычный ход работы
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.rename_selected)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+A"), self, activated=self.grid.select_all)
        self.search_edit.installEventFilter(self)
        self.names_list.installEventFilter(self)
        common.keep_focus_on_click(self)
        self.grid.setFocus()

    def _build_top(self) -> QHBoxLayout:
        top = QHBoxLayout()
        top.setSpacing(theme_mod.SPACE["sm"])
        self.selection_label = QLabel("Выделено: 0")
        self.selection_label.setProperty("role", "title")
        top.addWidget(self.selection_label)
        # кнопок «Выделить всё» и «Снять выделение» нет: по отзыву с работы не нужны (Ctrl+A и Esc есть)
        top.addStretch(1)
        top.addWidget(QLabel("Размер миниатюр:"))
        self.smaller_btn = QToolButton()
        self.smaller_btn.setToolTip("Мельче (Ctrl+колесо)")
        self.smaller_btn.clicked.connect(lambda: self._zoom(-ZOOM_STEP))
        top.addWidget(self.smaller_btn)
        self.larger_btn = QToolButton()
        self.larger_btn.setToolTip("Крупнее (Ctrl+колесо)")
        self.larger_btn.clicked.connect(lambda: self._zoom(ZOOM_STEP))
        top.addWidget(self.larger_btn)
        return top

    def _build_side(self) -> QFrame:
        space = theme_mod.SPACE
        self.side = QFrame()
        self.side.setObjectName("card")
        self.side.setFixedWidth(common.MIN_SIDE_WIDTH)
        panel = QVBoxLayout(self.side)
        panel.setContentsMargins(space["md"], space["md"], space["md"], space["md"])
        panel.setSpacing(space["sm"])

        header = QHBoxLayout()
        title = QLabel("Категория:")
        title.setProperty("role", "title")
        header.addWidget(title, 1)
        self.add_btn = QToolButton()
        self.add_btn.setToolTip("Добавить категорию в список")
        self.add_btn.clicked.connect(lambda: self.add_name())
        header.addWidget(self.add_btn)
        self.remove_btn = QToolButton()
        self.remove_btn.setToolTip("Удалить категорию из списка")
        self.remove_btn.clicked.connect(lambda: self.remove_name())
        header.addWidget(self.remove_btn)
        panel.addLayout(header)

        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(lambda _text: self._refresh_names())
        panel.addWidget(self.search_edit)
        self.names_list = QListWidget()
        self.names_list.itemSelectionChanged.connect(self._on_name_selected)
        self.names_list.itemDoubleClicked.connect(lambda _item: self.rename_selected())
        panel.addWidget(self.names_list, 1)

        sides = QHBoxLayout()
        sides_label = QLabel("Ракурсов (фото на одну деталь):")
        sides_label.setWordWrap(True)
        sides.addWidget(sides_label, 1)
        self.sides_spin = QSpinBox()
        self.sides_spin.setRange(1, common.MAX_SIDES)
        self.sides_spin.setFixedWidth(72)
        sides.addWidget(self.sides_spin)
        panel.addLayout(sides)

        save_key = QKeySequence("Ctrl+S").toString(QKeySequence.NativeText)
        undo_key = QKeySequence("Ctrl+Z").toString(QKeySequence.NativeText)
        self.rename_btn = QPushButton("Переименовать выделенные (%s)" % save_key)
        self.rename_btn.setProperty("accent", True)
        self.rename_btn.clicked.connect(lambda: self.rename_selected())
        panel.addWidget(self.rename_btn)
        self.undo_btn = QPushButton("Отменить (%s)" % undo_key)
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(lambda: self.undo())
        panel.addWidget(self.undo_btn)
        panel.addWidget(common.line())
        self.done_btn = QPushButton("Готово")
        self.done_btn.clicked.connect(lambda: self.close())
        panel.addWidget(self.done_btn)
        return self.side

    def _build_bottom(self) -> QHBoxLayout:
        bottom = QHBoxLayout()
        bottom.setSpacing(theme_mod.SPACE["sm"])
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", True)
        # длинная надпись не должна распирать окно: она переносится
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bottom.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(220)
        bottom.addWidget(self.progress)
        return bottom

    def _set_icons(self) -> None:
        self.smaller_btn.setIcon(self.theme.icon("minus"))
        self.larger_btn.setIcon(self.theme.icon("plus"))
        self.add_btn.setIcon(self.theme.icon("plus"))
        self.remove_btn.setIcon(self.theme.icon("trash-2"))

    def set_theme(self, theme: theme_mod.Theme) -> None:
        """Оформление переключают в главном окне: значки и ячейки сетки рисуются заново."""
        self.theme = theme
        self._set_icons()
        self.grid.set_theme(theme)

    def eventFilter(self, watched, event) -> bool:
        """Enter в поиске и в списке переименовывает, стрелка вниз из поиска ведёт в список."""
        if event.type() == QEvent.KeyPress and not event.modifiers() & ~Qt.KeypadModifier:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.rename_selected()
                return True
            if watched is self.search_edit and event.key() == Qt.Key_Down:
                self.names_list.setFocus()
                self._select_first()
                return True
        return super().eventFilter(watched, event)

    # --- данные --------------------------------------------------------------------------

    def start(self) -> None:
        if self._closed or self._started:      # защита от повторного запуска
            return
        self._started = True
        self._refresh_names()
        try:
            self.session.load_folder()
        except OSError as exc:
            QMessageBox.critical(self, TITLE, "Не удалось прочитать папку:\n%s" % exc)
            return
        self.grid.set_items(self.session.items(), self.session.template_flags())
        self._update_status()
        if self.session.learn_from_reference():
            self._refresh_names()

    def _refresh_names(self) -> None:
        current = self.current_name()
        self.names_list.blockSignals(True)
        self.names_list.clear()
        names = self.names.search(self.search_edit.text())
        self.names_list.addItems(names)
        for item in self.names_list.findItems(current or "", Qt.MatchExactly) if current else ():
            self.names_list.setCurrentItem(item)
        self.names_list.blockSignals(False)
        self.side.setFixedWidth(common.side_width(self.names_list, names or self.names.names()))

    def current_name(self) -> Optional[str]:
        items = self.names_list.selectedItems()
        return items[0].text() if items else None

    def _select_first(self) -> None:
        if self.names_list.count():
            self.names_list.setCurrentRow(0)

    def _select_name(self, name: str) -> None:
        for item in self.names_list.findItems(name, Qt.MatchExactly):
            self.names_list.setCurrentItem(item)
            self.names_list.scrollToItem(item)
            self._on_name_selected()

    def _on_name_selected(self) -> None:
        name = self.current_name()
        if name:
            self.sides_spin.setValue(self.names.sides_for(name))

    def _on_selection(self) -> None:
        self.selection_label.setText("Выделено: %d" % len(self.grid.selected_rows()))

    def _on_load_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(0 if done >= total else done)
        self._update_status(None if done >= total else (done, total))

    def _update_status(self, loading=None) -> None:
        self.status_label.setText(self.session.status_text(loading))

    def _zoom(self, delta: int) -> None:
        self.grid.set_cell_size(self.grid.cell_size + delta)

    # --- действия ------------------------------------------------------------------------

    def add_name(self) -> None:
        """Добавляет то, что набрано в поле поиска: человек и так видит, что такой категории нет.

        Отдельного окна с «ОК» нет: имя уже введено, переспрашивать его нечего.
        """
        typed = self.search_edit.text().strip()
        if not typed:
            self.status_label.setText("Наберите название категории в поле справа и нажмите «+»")
            self.search_edit.setFocus()
            return
        try:
            name = self.session.add_name(typed, self.sides_spin.value())
        except ValueError as exc:
            QMessageBox.critical(self, TITLE, str(exc))
            return
        self.search_edit.clear()
        self._refresh_names()
        self._select_name(name)

    def remove_name(self) -> None:
        name = self.current_name()
        if not name:
            QMessageBox.information(self, TITLE, "Выберите категорию в списке.")
            return
        if self._ask("Удалить «%s» из списка категорий?" % name):
            self.session.remove_name(name)
            self._refresh_names()

    def _ask(self, question: str) -> bool:
        return common.ask(self, TITLE, question)

    def rename_selected(self) -> None:
        name = self.current_name()
        selected = self.grid.selected_names()
        if not name:
            QMessageBox.information(self, TITLE, "Выберите категорию в списке справа.")
            return
        if not selected:
            QMessageBox.information(self, TITLE, "Выделите фото слева.")
            return
        sides = self.sides_spin.value()
        try:
            assignment = self.session.plan_rename(selected, name, sides)
        except ValueError as exc:
            QMessageBox.critical(self, TITLE, str(exc))
            return
        if assignment.warnings:
            if not self._ask("\n".join(assignment.warnings) + "\n\nПродолжить переименование?"):
                return
        self.grid.pause_loading()          # читаемое сейчас фото на Windows не переименовать
        done, failed = self.session.apply_rename(assignment, name, sides)
        if done:
            self.undo_btn.setEnabled(True)
        self._apply_renames(done)
        if failed:
            QMessageBox.critical(self, TITLE, "Не удалось переименовать:\n" +
                                 "\n".join("%s: %s" % (item.old, item.message) for item in failed))
        self.grid.clear_selection()
        self._follow_view()

    def _apply_renames(self, done) -> None:
        """Обновляет имена в сетке и переставляет ячейки в порядок показа."""
        order = self.session.apply_renames(done)
        self.grid.reorder(order, self.session.items(), self.session.template_flags())
        self._update_status()
        if self._view is not None:
            self._view.set_photos(self.session.shown, {item.old: item.new for item in done})

    def undo(self) -> None:
        if not self.session.history:
            return
        self.grid.pause_loading()
        done = self.session.undo()
        if done is None:
            return
        self._apply_renames(done)
        if not self.session.history:
            self.undo_btn.setEnabled(False)
        self._follow_view()

    def _open_large(self, index: int) -> None:
        """Фото крупно. Листается как показано в сетке; открытое фото и есть выделенное: Ctrl+S и Ctrl+Z
        работают прямо в окне просмотра и переименовывают его (отзыв с работы: переименовывалось то,
        с которого начали листать)."""
        if self._view is not None:
            self._view.close()
        self._view = LargeView(self, self.folder, self.session.shown, index, on_show=self._select_photo,
                               keys={"Ctrl+S": self.rename_selected, "Ctrl+Z": self.undo})
        self._view.finished.connect(self._view_closed)       # закрыли крестиком или Esc

    def _view_closed(self, *_args) -> None:
        self._view = None

    def _select_photo(self, name: str) -> None:
        if name in self.session.shown:
            row = self.session.shown.index(name)
            self.grid.set_selection([row])
            self.grid.scrollTo(self.grid.model_.index(row))

    def _follow_view(self) -> None:
        if self._view is not None:
            self._select_photo(self._view.name)

    def closeEvent(self, event) -> None:
        if not self._closed:
            self._closed = True
            self.grid.stop()
            self.session.close()
            if self.on_done:
                self.on_done()
        event.accept()
