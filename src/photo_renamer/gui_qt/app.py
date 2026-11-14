"""Первое окно программы на PyQt5: папка проекта → анализ → группы похожих папок и их эталоны.

Логика — в ProjectSession, та же, что у окна на tkinter. Чтение фото идёт в фоновой задаче.
"""

import os
import subprocess
import sys
import traceback
from typing import List, Optional

from PyQt5.QtCore import QCoreApplication, QLibraryInfo, QLocale, QSize, Qt, QTranslator
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QApplication, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
                             QSizePolicy, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import config as cfg
from ..cache import PhotoCache
from ..names import NamesList
from ..project_session import MARK_NO, MARK_REFERENCE, ProjectSession
from ..worker import Cancelled, Worker
from . import common, theme as theme_mod
from .editor import ReferenceEditor
from .review import BatchWindow

TITLE = "CV"
COLUMNS = ("Группа / папка", "Эталон", "Переименовать", "Фото", "Обработано", "Сходство")
COLUMN_REFERENCE, COLUMN_RENAME = 1, 2
# самая длинная надпись, которая в столбце бывает: ширина столбца считается по ней
COLUMN_SAMPLES = {1: MARK_REFERENCE, 2: MARK_NO, 3: "9999", 4: "100%", 5: "высокое"}
HINT = ("Программа находит все папки с фото, сравнивает их и объединяет похожие в группы. "
        "Для каждой группы нужен один эталон — папка, переименованная по шаблону. "
        "Галочка «Эталон» стоит на самой переименованной папке группы; её можно "
        "переставить щелчком, как и отметку «Переименовать».")


def open_in_file_manager(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — открыть папку в Проводнике
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class _RowItem(QTreeWidgetItem):
    """Строка таблицы вместе с тем, к какой группе и папке она относится."""

    def __init__(self, row):
        super().__init__([row.text] + [str(value) for value in row.values])
        self.group = row.group
        self.folder = row.folder


class _ProjectTree(QTreeWidget):
    """Таблица папок. Первый щелчок двойного уже переставил отметку и перестроил таблицу: без этой
    проверки Qt принял бы второй за новый щелчок, и отметка вернулась бы обратно."""

    def mouseDoubleClickEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        if item is not None and item.folder is not None and self.columnAt(event.x()) in (COLUMN_REFERENCE,
                                                                                         COLUMN_RENAME):
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, app_config: cfg.AppConfig, config_warnings: List[str] = ()):
        super().__init__()
        self.app_config = app_config
        self.session = ProjectSession(app_config)
        # список категорий один на все окна: иначе у каждого своя копия, и сохранение затирает чужое
        self.names = NamesList(app_config.names_path())
        self._busy = False
        self._close_requested = False
        self.worker = Worker(common.WorkerHost())
        self.theme = self._apply_theme(theme_mod.resolve(app_config.theme, QApplication.instance()))

        self.setWindowTitle(TITLE)
        self.setMinimumSize(850, 450)
        self._build()
        self._set_icons()
        self.root_edit.setText(self.session.load_state().get("project", ""))
        self._update_buttons()
        self._widen_to_fit_columns()
        if config_warnings:
            QMessageBox.warning(self, TITLE, "Замечания к настройкам (config.json):\n\n" + "\n".join(config_warnings))

    @property
    def analysis(self):
        return self.session.analysis

    def _apply_theme(self, name: str) -> theme_mod.Theme:
        folder = os.path.join(os.path.dirname(self.app_config.cache_path()), "theme")
        return theme_mod.apply(QApplication.instance(), name, folder)

    # --- построение окна ------------------------------------------------------------------

    def _build(self) -> None:
        toolbar = QToolBar("Проект")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(theme_mod.ICON_SIZE["md"], theme_mod.ICON_SIZE["md"]))
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        toolbar.addWidget(QLabel("Папка проекта: "))
        self.root_edit = QLineEdit()
        self.root_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.root_edit.returnPressed.connect(self.analyze)
        toolbar.addWidget(self.root_edit)
        self.browse_action = QAction("Обзор…", self, triggered=self.browse)
        self.analyze_action = QAction("Анализировать", self, triggered=self.analyze)
        toolbar.addAction(self.browse_action)
        toolbar.addAction(self.analyze_action)
        self.lamp = QToolButton()
        self.lamp.setCheckable(True)
        self.lamp.setChecked(self.theme.name == "light")
        self.lamp.setToolTip("Оформление: лампочка горит — светлое, погашена — тёмное")
        self.lamp.clicked.connect(self.on_lamp)
        toolbar.addWidget(self.lamp)

        central = QWidget()
        self.setCentralWidget(central)
        space = theme_mod.SPACE
        layout = QVBoxLayout(central)
        layout.setContentsMargins(space["md"], space["sm"], space["md"], space["md"])
        layout.setSpacing(space["sm"])
        hint = QLabel(HINT)
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        layout.addWidget(hint)

        middle = QHBoxLayout()
        middle.setSpacing(space["md"])
        layout.addLayout(middle, 1)
        self.tree = _ProjectTree()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setUniformRowHeights(True)
        # Ни один столбец не растягивается: меняется только тот, который тянут, а остальные
        # сдвигаются, как в Проводнике; если они не помещаются, внизу появляется полоса прокрутки
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionsMovable(False)
        self.tree.setColumnWidth(0, 360)
        metrics = self.tree.fontMetrics()
        for column, sample in COLUMN_SAMPLES.items():
            # запас — на отступы ячейки и заголовка
            width = max(metrics.horizontalAdvance(sample), metrics.horizontalAdvance(COLUMNS[column]))
            self.tree.setColumnWidth(column, width + 2 * theme_mod.SPACE["lg"])
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.itemClicked.connect(self._on_click)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        middle.addWidget(self.tree, 1)

        side = QFrame()
        side.setObjectName("card")
        side.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        panel = QVBoxLayout(side)
        panel.setContentsMargins(space["md"], space["md"], space["md"], space["md"])
        panel.setSpacing(space["sm"])
        middle.addWidget(side)
        self.side = side

        panel.addWidget(self._section("Выбранная папка"))
        self.reference_btn = self._button(panel, "Сделать эталоном группы", self.make_reference)
        self.move_btn = self._button(panel, "Перенести в группу", None)
        self.move_menu = QMenu(self.move_btn)
        self.move_menu.aboutToShow.connect(self._fill_move_menu)
        self.move_btn.setMenu(self.move_menu)
        self.open_btn = self._button(panel, "Открыть папку", self.open_folder)
        panel.addWidget(common.line())
        panel.addWidget(self._section("Группа"))
        self.edit_reference_btn = self._button(panel, "Создать / изменить эталон…", self.edit_reference)
        self.rename_group_btn = self._button(panel, "Переименовать папки группы…", self.rename_group)
        self.rename_group_btn.setProperty("accent", True)
        panel.addWidget(common.line())
        self.refresh_btn = self._button(panel, "Обновить имена файлов", self.refresh)
        panel.addStretch(1)

        status = self.statusBar()
        status.setSizeGripEnabled(False)
        self.status_label = QLabel("Выберите папку проекта и нажмите «Анализировать».")
        status.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(260)
        self.progress.hide()
        status.addPermanentWidget(self.progress)
        self.cancel_btn = QPushButton("Прервать")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.hide()
        status.addPermanentWidget(self.cancel_btn)

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "title")
        return label

    @staticmethod
    def _button(panel: QVBoxLayout, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        if slot is not None:
            button.clicked.connect(lambda _checked=False: slot())
        panel.addWidget(button)
        return button

    def _set_icons(self) -> None:
        """Значки рисуются в цвете темы, поэтому при её смене рисуются заново."""
        self.browse_action.setIcon(self.theme.icon("folder-open"))
        self.analyze_action.setIcon(self.theme.icon("search"))
        self.lamp.setIcon(self.theme.icon("lightbulb" if self.lamp.isChecked() else "lightbulb-off"))
        self.cancel_btn.setIcon(self.theme.icon("square"))
        self.refresh_btn.setIcon(self.theme.icon("refresh-cw"))

    def _widen_to_fit_columns(self) -> None:
        """Ширина при открытии — такая, чтобы столбцы поместились целиком, но не шире экрана."""
        columns = sum(self.tree.columnWidth(k) for k in range(self.tree.columnCount()))
        needed = columns + self.side.sizeHint().width() + 4 * theme_mod.SPACE["lg"]
        width = max(1150, needed)
        screen = QApplication.primaryScreen()
        if screen is not None:
            width = min(width, int(screen.availableGeometry().width() * 0.95))
        self.resize(width, 650)

    def on_lamp(self, lit: bool) -> None:
        """Лампочка горит — светлое оформление, погашена — тёмное. Выбор запоминается."""
        mode = "light" if lit else "dark"
        self.app_config.theme = mode
        self.theme = self._apply_theme(mode)
        self._set_icons()
        self.render()
        for window in self._child_windows():
            window.set_theme(self.theme)
        try:
            cfg.save_config(self.app_config)
        except OSError as exc:
            self.status_label.setText("Не удалось сохранить настройки: %s" % exc)

    # --- анализ -------------------------------------------------------------------------------

    def browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка проекта", self.root_edit.text())
        if path:
            self.root_edit.setText(os.path.normpath(path))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (self.root_edit, self.browse_action, self.analyze_action):
            widget.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self._update_buttons()

    def cancel(self) -> None:
        self.worker.cancel()

    def analyze(self) -> None:
        if self._busy:
            return
        root = self.root_edit.text().strip()
        if not root or not os.path.isdir(root):
            QMessageBox.critical(self, TITLE, "Папка не найдена:\n%s" % (root or "(не указана)"))
            return
        self.session.project_root = root
        self.session.save_state(root)
        self._set_busy(True)
        self.status_label.setText("Поиск папок с фото…")
        job = self.session.analysis_job(root, PhotoCache)
        self.worker.start(job, self._on_worker_message, self._on_analysis_done)

    def _on_worker_message(self, message) -> None:
        """Сообщения из фоновой задачи приходят сюда, в главный поток."""
        kind = message[0]
        if kind == "folder":
            _, number, total, path = message
            self.status_label.setText("Папка %d из %d: %s" % (number, total, self.session.display_path(path)))
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        elif kind == "progress":
            _, done, total = message
            self.progress.setRange(0, max(total, 1))
            self.progress.setValue(done)

    def _on_analysis_done(self, result, error) -> None:
        if isinstance(error, Cancelled):
            self.status_label.setText("Анализ прерван.")
        elif error is not None:
            self.status_label.setText("Ошибка анализа.")
            QMessageBox.critical(self, TITLE, "Ошибка при анализе:\n%s" % error)
        else:
            self.session.analysis = result
            self.status_label.setText(self.session.summary())
        self._set_busy(False)
        self.render()
        if self._close_requested:
            self.close()

    # --- отображение ------------------------------------------------------------------------

    def render(self) -> None:
        selected = self._selected_folder()
        scroll = self.tree.verticalScrollBar().value()
        self.tree.blockSignals(True)
        self.tree.clear()
        for row in self.session.rows():
            parent = self._insert_row(None, row, selected)
            for child in row.children:
                self._insert_row(parent, child, selected)
            parent.setExpanded(True)
        self.tree.blockSignals(False)
        self.tree.verticalScrollBar().setValue(scroll)
        self._update_buttons()

    def _insert_row(self, parent: Optional[QTreeWidgetItem], row, selected) -> QTreeWidgetItem:
        item = _RowItem(row)
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        if {"group", "reference"} & set(row.tags):
            font = item.font(0)
            font.setBold(True)
            for column in range(len(COLUMNS)):
                item.setFont(column, font)
        color = "danger" if "low" in row.tags else "warning" if "not_ready" in row.tags else None
        if color is not None:
            for column in range(len(COLUMNS)):
                item.setForeground(column, self.theme.qcolor(color))
        if row.folder is not None and row.folder is selected:
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        return item

    # --- действия ------------------------------------------------------------------------------

    def _on_click(self, item: QTreeWidgetItem, column: int) -> None:
        """Щелчок по столбцу «Эталон» или «Переименовать» — это отметка, а не просто выделение."""
        if self._busy or self.analysis is None:
            return
        folder = item.folder
        if folder is None:
            return
        if column == COLUMN_REFERENCE:
            self.make_reference()
        elif column == COLUMN_RENAME:
            self.toggle_rename(folder)

    def toggle_rename(self, folder) -> None:
        if not self.session.toggle_rename(folder):
            return
        self.render()
        self.status_label.setText(self.session.summary())

    def _on_double_click(self, _item: QTreeWidgetItem, column: int) -> None:
        """Двойной щелчок открывает папку, но не в столбцах с отметками: там он их только дёргает."""
        if column not in (COLUMN_REFERENCE, COLUMN_RENAME):
            self.open_folder()

    def _selected_item(self) -> Optional[QTreeWidgetItem]:
        items = self.tree.selectedItems()
        return items[0] if items else None

    def _selected_folder(self):
        item = self._selected_item()
        return item.folder if item is not None else None

    def _selected_group(self) -> Optional[int]:
        item = self._selected_item()
        return item.group if item is not None else None

    def _update_buttons(self) -> None:
        folder = None if self._busy else self._selected_folder()
        group = None if self._busy else self._selected_group()
        actions = self.session.available_actions(folder, group)
        for button, action in ((self.reference_btn, "make_reference"), (self.move_btn, "move"),
                               (self.open_btn, "open"), (self.edit_reference_btn, "edit_reference"),
                               (self.rename_group_btn, "rename_group")):
            button.setEnabled(actions[action])
        self.refresh_btn.setEnabled(self.analysis is not None and not self._busy)

    def _fill_move_menu(self) -> None:
        self.move_menu.clear()
        folder = self._selected_folder()
        if folder is None:
            return
        for k, label, enabled in self.session.move_targets(folder):
            action = self.move_menu.addAction(label, lambda k=k: self.move_selected(k))
            action.setEnabled(enabled)
        self.move_menu.addSeparator()
        self.move_menu.addAction("Новая группа", lambda: self.move_selected(None))

    def move_selected(self, target: Optional[int]) -> None:
        folder = self._selected_folder()
        if folder is None:
            return
        self.session.move_folder(folder, target)
        self.render()
        self.status_label.setText(self.session.summary())

    def make_reference(self) -> None:
        folder = self._selected_folder()
        if folder is None:
            return
        self.session.make_reference(folder)
        self.render()
        self.status_label.setText(self.session.summary())

    def open_folder(self) -> None:
        folder = self._selected_folder()
        if folder is not None:
            try:
                open_in_file_manager(folder.path)
            except OSError as exc:
                QMessageBox.critical(self, TITLE, "Не удалось открыть папку:\n%s" % exc)

    def refresh(self) -> None:
        if self.analysis is None:
            return
        self.session.refresh(PhotoCache)
        self.render()
        self.status_label.setText(self.session.summary())

    def edit_reference(self) -> None:
        """Открывает выделенную папку, а не эталон группы: человек выбрал её сам."""
        group = self._selected_group()
        if group is None:
            return
        folder = self._selected_folder() or self.analysis.groups[group].reference
        window = ReferenceEditor(self, folder.path, self.app_config, self.theme, self.names, on_done=self.refresh)
        window.show()

    def rename_group(self) -> None:
        group = self._selected_group()
        if group is None:
            return
        target = self.analysis.groups[group]
        if not target.targets:
            QMessageBox.information(self, TITLE, "В этой группе нет папок для переименования.")
            return
        window = BatchWindow(self, target, self.app_config, self.theme, on_done=self.refresh, names=self.names)
        window.show()

    def _child_windows(self) -> list:
        return self.findChildren(BatchWindow) + self.findChildren(ReferenceEditor)

    def closeEvent(self, event) -> None:
        if self._busy or self.worker.busy():
            self._close_requested = True
            self.worker.cancel()
            event.ignore()
            return
        # дочернее окно закрывается как обычно: иначе его лог и кэш остались бы незакрытыми
        for window in self._child_windows():
            window.on_done = None
            if not window.close():      # оно ещё читает фото и закроется само, когда прервётся
                event.ignore()
                return
        event.accept()

    def wait_for_worker(self, timeout: float = 600.0) -> None:
        """Дождаться окончания фоновой задачи (используется в тестах и при закрытии)."""
        self.worker.wait(timeout)


def _show_error(kind, error, trace) -> None:
    """Без этого PyQt5 аварийно завершает программу при любой ошибке в обработчике щелчка."""
    traceback.print_exception(kind, error, trace)
    QMessageBox.critical(None, TITLE, "Ошибка программы:\n%s" % error)


def qt_plugins_dir() -> str:
    """Папка модулей Qt внутри пакета PyQt5 (пустая строка, если её нет)."""
    import PyQt5

    folder = os.path.join(os.path.dirname(os.path.abspath(PyQt5.__file__)), "Qt5", "plugins")
    return folder if os.path.isdir(os.path.join(folder, "platforms")) else ""


def create_application() -> QApplication:
    """QApplication, который запускается и из папки с русскими буквами.

    PyQt5 сообщает Qt путь к модулям не в Юникоде, и из такой папки Qt их не находит: процесс
    обрывается с «no Qt platform plugin could be initialized» (так было у Digger на Windows 7).
    Поэтому путь задаётся явно, до создания приложения.
    """
    app = QApplication.instance()
    if app is not None:
        return app
    plugins = qt_plugins_dir()
    if plugins:
        QCoreApplication.addLibraryPath(plugins)
    app = QApplication(sys.argv[:1])
    translator = QTranslator(app)       # русские подписи стандартных окон Qt
    if translator.load(QLocale("ru_RU"), "qtbase", "_", QLibraryInfo.location(QLibraryInfo.TranslationsPath)):
        app.installTranslator(translator)
    return app


def main(config: Optional[cfg.AppConfig] = None, warnings: List[str] = ()) -> None:
    if config is None:
        config, warnings = cfg.load_config()
    sys.excepthook = _show_error
    app = create_application()
    window = MainWindow(config, warnings)
    window.show()
    app.exec_()
