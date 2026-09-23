"""Пакетное переименование группы папок с предпросмотром.

Папки обрабатываются по одной. Для каждой показывается таблица «текущее имя → новое имя»
с миниатюрой и % уверенности. Неопознанные фото можно исправить: выбрать кандидата одним
щелчком или категорию из списка. Переименование выполняется только после подтверждения,
пишется в лог и может быть отменено.
"""

import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog, ttk
from typing import Dict, List, Optional

from .. import config as cfg, grouping, matcher, names as names_mod, oplog, renamer
from ..cache import PhotoCache
from ..reference import category_key, load_reference
from ..report import build_report, format_report
from . import theme
from ..scanner import natural_key
from .photoview import LargeView
from .thumbgrid import to_photo

TITLE = "Переименование папок"
# Размер миниатюр в строках таблицы: по умолчанию крупный, меняется Ctrl+колесом и кнопками
# «−» и «+». Значение запоминается в настройках. Верхняя граница — размер миниатюры в кэше:
# крупнее пришлось бы заново читать исходные файлы (на сетевых дисках это долго).
MIN_THUMB, MAX_THUMB, THUMB_STEP = 48, 320, 24
BUTTON_GAP = 4            # одинаковый зазор между всеми кнопками окна
SIDE_PADDING = 44         # полоса прокрутки, рамки и отступы правой колонки
MIN_SIDE_WIDTH, MAX_SIDE_WIDTH = 260, 460
STATUS_TEXT = {
    matcher.STATUS_OK: "распознано",
    matcher.STATUS_SIDE_RULE: "по правилу сторон",
    matcher.STATUS_CHECK: "ПРОВЕРЬТЕ",
    matcher.STATUS_MANUAL: "выбрано вручную",
    matcher.STATUS_ALREADY_NAMED: "уже названо",
    matcher.STATUS_LOW_CONFIDENCE: "НЕ УВЕРЕНА",
    matcher.STATUS_NO_MATCH: "НЕ ПОХОЖЕ НИ НА ЧТО",
    matcher.STATUS_EXCLUDED: "не переименовывать",
    matcher.STATUS_UNREADABLE: "не прочитано",
}


class Cancelled(Exception):
    pass


class BatchWindow(tk.Toplevel):
    def __init__(self, master, group: grouping.FolderGroup, app_config, on_done=None, names=None):
        super().__init__(master)
        self.group = group
        self.app_config = app_config
        # список категорий общий с редактором эталона: иначе два окна держат по своей копии
        # и то, что добавлено в одном, затирается сохранением из другого
        self.names = names if names is not None else names_mod.NamesList(app_config.names_path())
        self.on_done = on_done
        self.cache = PhotoCache(app_config.cache_path(), app_config.cache_max_mb)
        self.log = oplog.RunLog(app_config.logs_path(), oplog.folder_label(group.reference.path) + "_группа")
        self.queue: List[grouping.FolderInfo] = list(group.targets)
        self.total = len(self.queue)
        self.done_folders = 0
        self.model: Optional[matcher.ReferenceModel] = None
        self.match: Optional[matcher.FolderMatch] = None
        self.folder: Optional[grouping.FolderInfo] = None
        self.categories: List[str] = []
        self._photos: Dict[str, tk.PhotoImage] = {}
        self._row_index: Dict[str, int] = {}
        self.thumb_size = int(getattr(app_config, "review_thumb", 96))
        self._busy = False
        self._cancel = False
        self._last_plan: Optional[renamer.RenamePlan] = None
        self._undo: List[List[tuple]] = []      # шаги ручных правок для Ctrl+Z

        self.title(TITLE)
        self.minsize(900, 600)
        theme.fit_to_screen(self)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.after(50, self.start)

    # --- построение окна --------------------------------------------------------------

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        self.header_var = tk.StringVar(value="Подготовка…")
        ttk.Label(top, textvariable=self.header_var, font="TkHeadingFont").pack(side="left")
        self.progress = ttk.Progressbar(top, length=260, mode="determinate")
        self.progress.pack(side="right")
        zoom = ttk.Frame(top)
        zoom.pack(side="right", padx=(0, BUTTON_GAP * 2))
        ttk.Label(zoom, text="Размер фото:").pack(side="left", padx=(0, BUTTON_GAP))
        ttk.Button(zoom, text="−", width=3,
                   command=lambda: self.set_thumb_size(self.thumb_size - THUMB_STEP)).pack(side="left")
        ttk.Button(zoom, text="+", width=3,
                   command=lambda: self.set_thumb_size(self.thumb_size + THUMB_STEP)).pack(side="left", padx=BUTTON_GAP)

        # Нижняя строка (состояние папки и кнопки) занимает место раньше остальных частей окна:
        # pack раздаёт высоту в порядке упаковки, и на невысоком экране без этого исчезла бы именно она.
        bottom = ttk.Frame(self, padding=(10, 6, 10, 10))
        bottom.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="")
        # кнопки упаковываются первыми и справа налево: тогда каждая получает свою ширину
        # и одинаковый зазор, а строка состояния занимает остаток и переносится по словам
        self.undo_edit_btn = ttk.Button(bottom, text="Отменить правку (Ctrl+Z)", command=self.undo_edit)
        self.undo_btn = ttk.Button(bottom, text="Отменить эту папку", command=self.undo_folder, state="disabled")
        self.skip_btn = ttk.Button(bottom, text="Пропустить папку", command=self.skip_folder)
        self.apply_btn = ttk.Button(bottom, text="Переименовать папку", command=self.apply_folder)
        self.close_btn = ttk.Button(bottom, text="Закрыть", command=self.close)
        for button in (self.close_btn, self.apply_btn, self.skip_btn, self.undo_btn, self.undo_edit_btn):
            button.pack(side="right", padx=BUTTON_GAP)
        ttk.Label(bottom, textvariable=self.status_var, wraplength=520, justify="left").pack(
            side="left", fill="x", expand=True)

        body = ttk.Frame(self, padding=(10, 0, 10, 0))
        body.pack(fill="both", expand=True)

        table = ttk.Frame(body)
        table.pack(side="left", fill="both", expand=True)
        self.style = ttk.Style(self)
        # текст вариантов прижат к правому краю: проценты оказываются друг под другом
        self.style.configure("Candidate.TButton", anchor="e")
        self.tree = ttk.Treeview(table, columns=("new", "confidence", "status"), selectmode="extended",
                                 style="Review.Treeview")
        # по заголовкам столбцов сортируют щелчком: первый щелчок — по возрастанию, второй — наоборот
        # по умолчанию сортируем по новому имени: одинаковые детали оказываются рядом,
        # и проверять их удобнее, чем в порядке съёмки
        self.sort_column, self.sort_reverse = "new", False
        for column, title in self.COLUMN_TITLES:
            self.tree.heading(column, text=title, command=lambda c=column: self.sort_by(c))
        self._show_sort_marks()
        # ширины столбцов заданы скромно: иначе таблица запрашивает больше места, чем есть,
        # и сжимает правую колонку со списком категорий. Границы столбцов можно тянуть мышью
        self.tree.column("new", width=280)
        self.tree.column("confidence", width=90, anchor="e", stretch=False)
        self.tree.column("status", width=150, stretch=False)
        self._apply_thumb_size()
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        palette = theme.colors()
        self.tree.tag_configure("doubt", foreground=palette["warning"])
        self.tree.tag_configure("manual", foreground=palette["accent"])
        self.tree.tag_configure("skip", foreground=palette["muted"])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_row_select())
        # 1 и 2 — выбрать кандидата, 3 — не переименовывать: пометка «не переименовывать» нужна
        # чаще третьего кандидата, а под рукой должны быть три соседние клавиши
        for number in (1, 2):
            self.tree.bind("<Key-%d>" % number, lambda e, n=number: self.apply_candidate(n))
        for key in ("<Key-3>", "<Key-0>"):
            self.tree.bind(key, lambda e: self.exclude_selected())
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):  # Ctrl+колесо — размер фото
            self.tree.bind(sequence, self._on_wheel)
        for key in ("<Delete>", "<BackSpace>"):
            self.tree.bind(key, lambda e: self.exclude_selected())
        self.tree.bind("<Double-Button-1>", self._open_large)   # посмотреть фото крупно
        # Enter — подтвердить предложенное имя и перейти к следующей строке
        self.tree.bind("<Return>", self.confirm_selected)
        self.tree.bind("<KP_Enter>", self.confirm_selected)
        self._bind_undo(self.tree)
        self._bind_undo(self)

        self.side = side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)      # ширину задаём сами — по длине названий категорий

        self.candidates_frame = ttk.Frame(side)
        self.candidates_frame.pack(fill="x", pady=(0, BUTTON_GAP * 2))
        header = ttk.Frame(side)
        header.pack(fill="x")
        ttk.Label(header, text="Категория:").pack(side="left")
        theme.IconButton(header, "delete", command=self.remove_category,
                         hint="Удалить категорию из списка").pack(side="right")
        theme.IconButton(header, "add", command=self.add_category,
                         hint="Добавить категорию в список").pack(side="right", padx=(0, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._refresh_categories())
        search_entry = ttk.Entry(side, textvariable=self.search_var)
        search_entry.pack(fill="x", pady=(BUTTON_GAP, BUTTON_GAP))
        buttons = ttk.Frame(side)
        buttons.pack(side="bottom", fill="x")      # кнопки не должны уезжать за край на низком экране
        ttk.Button(buttons, text="Применить к выбранным",
                   command=self.apply_selected_category).pack(fill="x", pady=(BUTTON_GAP, 0))
        ttk.Button(buttons, text="Не переименовывать",
                   command=self.exclude_selected).pack(fill="x", pady=(BUTTON_GAP, 0))
        list_frame = ttk.Frame(side)
        list_frame.pack(fill="both", expand=True)
        self.categories_list = tk.Listbox(list_frame, exportselection=False, activestyle="none", height=6)
        cat_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.categories_list.yview)
        self.categories_list.configure(yscrollcommand=cat_scroll.set)
        self.categories_list.pack(side="left", fill="both", expand=True)
        cat_scroll.pack(side="left", fill="y")
        self.categories_list.bind("<Double-Button-1>", lambda e: self.apply_selected_category())
        for widget in (self.categories_list, search_entry):   # отмена работает, где бы ни был курсор
            self._bind_undo(widget)

    # --- ход обработки ------------------------------------------------------------------

    def _pump(self) -> None:
        self.update()
        if self._cancel:
            raise Cancelled()

    def _progress(self, done: int, total: int) -> None:
        self.progress.configure(maximum=max(total, 1), value=done)
        self._pump()

    def start(self) -> None:
        if self.model is not None or self._busy:   # защита от повторного запуска
            return
        if not self.group.reference_is_ready:
            messagebox.showerror(TITLE, "Эталон группы ещё не переименован по шаблону.", parent=self)
            self.close()
            return
        self.header_var.set("Чтение эталона: %s" % self.group.reference.path)
        self._busy = True
        try:
            reference = load_reference(self.group.reference.path)
            self.model = matcher.ReferenceModel.build(reference, self.app_config.extractor(), self._progress,
                                                      feature_source=self.cache.extract_many)
        except Cancelled:
            self.close()
            return
        except OSError as exc:
            messagebox.showerror(TITLE, "Не удалось прочитать эталон:\n%s" % exc, parent=self)
            self.close()
            return
        finally:
            self._busy = False
        self.reference_categories = [c.name for c in self.model.categories]
        self.categories = self._all_categories()
        self.log.write_text("Эталон: %s\nПапок в группе: %d" % (self.group.reference.path, self.total))
        for warning in self.model.warnings:
            self.log.write_text("ВНИМАНИЕ: " + warning)
        self._refresh_categories()
        self.next_folder()

    def next_folder(self) -> None:
        self._last_plan = None
        self.undo_btn.configure(state="disabled")
        if not self.queue:
            self.finish()
            return
        self.folder = self.queue.pop(0)
        self.done_folders += 1
        self.header_var.set("Папка %d из %d: %s" % (self.done_folders, self.total, self.folder.path))
        self.status_var.set("Чтение фото…")
        self._busy = True
        try:
            self.match = matcher.match_folder(self.model, self.folder.path, self.folder.scan.photos,
                                             self.app_config.match, self._progress,
                                             feature_source=self.cache.extract_many,)
        except Cancelled:
            self.close()
            return
        except OSError as exc:
            messagebox.showerror(TITLE, "Папка недоступна:\n%s" % exc, parent=self)
            self.next_folder()
            return
        finally:
            self._busy = False
        self.render_rows()
        self.load_thumbnails()

    COLUMN_TITLES = (("#0", "Фото"), ("new", "Новое имя"),
                     ("confidence", "Уверенность"), ("status", "Состояние"))
    SORT_KEYS = {
        "order": lambda p: (p.index,),
        "#0": lambda p: (natural_key(p.filename), p.index),
        "new": lambda p: (natural_key(p.new_name or ""), p.index),
        "confidence": lambda p: (p.confidence, p.index),
        "status": lambda p: (STATUS_TEXT.get(p.status, ""), p.index),
    }

    def sort_by(self, column: str) -> None:
        """Щелчок по заголовку столбца: сортировка по нему, повторный щелчок — в обратную сторону."""
        self.sort_reverse = not self.sort_reverse if column == self.sort_column else False
        self.sort_column = column
        self._show_sort_marks()
        self.render_rows()

    def _show_sort_marks(self) -> None:
        arrow = " ↑" if not self.sort_reverse else " ↓"
        for column, title in self.COLUMN_TITLES:
            self.tree.heading(column, text=title + (arrow if column == self.sort_column else ""))

    def _display_photos(self) -> List:
        photos = list(self.match.photos)
        photos.sort(key=self.SORT_KEYS.get(self.sort_column, self.SORT_KEYS["order"]),
                    reverse=self.sort_reverse)
        return photos

    def render_rows(self) -> None:
        # выделение запоминается по фото: строки пересоздаются, и их идентификаторы меняются
        selected = set(self._selected_indexes())
        self.tree.delete(*self.tree.get_children())
        self._row_index.clear()
        for photo in self._display_photos():
            new = photo.new_name or ("" if photo.identified else "— не переименовывать —")
            tags = []
            if photo.status in (matcher.STATUS_LOW_CONFIDENCE, matcher.STATUS_NO_MATCH, matcher.STATUS_SIDE_RULE,
                                matcher.STATUS_CHECK):
                tags.append("doubt")
            elif photo.status == matcher.STATUS_MANUAL:
                tags.append("manual")
            elif photo.status in (matcher.STATUS_EXCLUDED, matcher.STATUS_UNREADABLE):
                tags.append("skip")
            iid = self.tree.insert("", "end", text=" " + photo.filename, values=(
                new, "%.0f%%" % photo.confidence if photo.confidence else "", STATUS_TEXT.get(photo.status, "")),
                image=self._photos.get(photo.filename, ""), tags=tuple(tags))
            self._row_index[iid] = photo.index
            if photo.index in selected:
                self.tree.selection_add(iid)
        self._update_counts()

    @staticmethod
    def _is_doubtful(photo) -> bool:
        return photo.status in (matcher.STATUS_CHECK, matcher.STATUS_LOW_CONFIDENCE, matcher.STATUS_NO_MATCH,
                                matcher.STATUS_SIDE_RULE, matcher.STATUS_UNREADABLE)

    def _update_counts(self) -> None:
        total = len(self.match.photos)
        rename = sum(1 for p in self.match.photos if p.new_name)
        unknown = len(self.match.unidentified())
        check = sum(1 for p in self.match.photos if self._is_doubtful(p))
        text = "Фото: %d, будет переименовано: %d, проверить: %d" % (total, rename, check)
        if unknown:
            text += ", не опознано: %d" % unknown
        if self.match.strict:
            text += "   (состав совпадает с эталоном: имя предложено каждому фото)"
        if self.folder is not None and self.folder.scan.order_differs:
            text += "   (порядок съёмки не совпадает с порядком имён — используется порядок съёмки)"
        self.status_var.set(text)

    def _apply_thumb_size(self) -> None:
        self.style.configure("Review.Treeview", rowheight=self.thumb_size + 8)
        self.tree.column("#0", width=self.thumb_size + 200)

    def set_thumb_size(self, size: int) -> None:
        """Размер фото в строках таблицы. Миниатюры перерисовываются из кэша, файлы не перечитываются."""
        size = max(MIN_THUMB, min(MAX_THUMB, int(size)))
        if size == self.thumb_size:
            return
        self.thumb_size = size
        self.app_config.review_thumb = size
        self._photos.clear()
        self._apply_thumb_size()
        self.render_rows()
        self.load_thumbnails()

    def _on_wheel(self, event) -> None:
        """Ctrl+колесо — размер фото. Без Ctrl прокрутка остаётся обычной."""
        if not event.state & 0x0004:
            return None
        delta = event.delta if event.delta else (120 if getattr(event, "num", 0) == 4 else -120)
        self.set_thumb_size(self.thumb_size + (THUMB_STEP if delta > 0 else -THUMB_STEP))
        return "break"

    def _thumbnail(self, index: int, size: int) -> Optional[tk.PhotoImage]:
        """Миниатюра фото папки нужного размера или None, если файл не читается."""
        if self.match is None or not 0 <= index < len(self.match.photos):
            return None
        try:
            image = self.cache.thumbnail(os.path.join(self.folder.path, self.match.photos[index].filename))
            return to_photo(image, size)
        except Exception:
            return None

    def load_thumbnails(self) -> None:
        """Подгружает миниатюры по одной, не блокируя окно."""
        rows = list(self._row_index.items())

        def load(position: int) -> None:
            if position >= len(rows) or self._cancel or self.match is None:
                return
            iid, index = rows[position]
            photo = self.match.photos[index]
            try:
                image = self.cache.thumbnail(os.path.join(self.folder.path, photo.filename))
                self._photos[photo.filename] = to_photo(image, self.thumb_size)
                if self.tree.exists(iid):
                    self.tree.item(iid, image=self._photos[photo.filename])
            except Exception:
                pass
            self.after(1, load, position + 1)

        self.after(1, load, 0)

    # --- правка ----------------------------------------------------------------------

    def _open_large(self, event=None):
        """Показывает выбранное фото крупно: стрелки листают папку, Esc закрывает."""
        indexes = self._selected_indexes()
        if event is not None and getattr(event, "y", None) is not None:
            row = self.tree.identify_row(event.y)
            if row in self._row_index:
                indexes = [self._row_index[row]]
        if not indexes or self.match is None or self.folder is None:
            return "break"
        names = [p.filename for p in self.match.photos]
        LargeView(self, self.folder.path, names, indexes[0], self.cache)
        return "break"

    # «<<Undo>>» — обязательно: на macOS Cmd+Z перехватывает стандартное меню «Правка»,
    # и обычная привязка к сочетанию клавиш до окна не доходит.
    UNDO_SEQUENCES = ("<Control-z>", "<Command-z>", "<Control-Z>", "<Command-Z>", "<<Undo>>")

    def _bind_undo(self, widget) -> None:
        for sequence in self.UNDO_SEQUENCES:
            widget.bind(sequence, self.undo_edit)

    _ABSENT = object()                       # признак «ручной правки для этого фото не было»

    def _remember(self, indexes: List[int]) -> None:
        """Запоминает, что было до правки: по Ctrl+Z всё вернётся одним шагом."""
        step = [(index, self.match.manual.get(index, self._ABSENT)) for index in indexes]
        if step:
            self._undo.append(step)

    def undo_edit(self, event=None):
        """Ctrl+Z — отменяет последнюю ручную правку: и выбор цифрой, и Enter, и кнопки, и список."""
        if not self._undo:
            self.status_var.set("Отменять нечего")
            return "break"
        step = self._undo.pop()
        for index, previous in step:
            if previous is self._ABSENT:
                self.match.manual.pop(index, None)
            else:
                self.match.manual[index] = previous
        matcher.solve(self.match)
        self.render_rows()
        self._select_photos([index for index, _ in step])
        self._update_counts()
        return "break"

    def _select_photos(self, indexes: List[int]) -> None:
        wanted = {index for index in indexes}
        rows = [iid for iid, photo_index in self._row_index.items() if photo_index in wanted]
        if rows:
            self.tree.selection_set(rows)
            self.tree.focus(rows[0])
            self.tree.see(rows[0])
            self._on_row_select()

    def confirm_selected(self, event=None):
        """Enter — подтвердить предложенное имя как выбранное вручную и перейти к следующей строке."""
        indexes = self._selected_indexes()
        if not indexes:
            return "break"
        position = self._selected_position()
        self._remember(indexes)
        confirmed = 0
        for index in indexes:
            photo = self.match.photos[index]
            if photo.identified and photo.status != matcher.STATUS_MANUAL:
                matcher.set_category(self.match, index, photo.category.key)
                confirmed += 1
        if not confirmed:
            self._undo.pop()                  # подтверждать было нечего — шага не было
        self.render_rows()
        # имя не меняется, значит строка осталась на своём месте: идём на строку ниже
        self._select_position(None if position is None else position + 1)
        return "break"

    def _selected_indexes(self) -> List[int]:
        return [self._row_index[iid] for iid in self.tree.selection() if iid in self._row_index]

    def _selected_rows(self) -> List[str]:
        return [iid for iid in self.tree.selection() if iid in self._row_index]

    def _selected_position(self) -> Optional[int]:
        """Место выбранной строки в таблице — не фото, а именно место."""
        rows = self.tree.get_children()
        current = self._selected_rows()
        if not rows or not current or current[-1] not in rows:
            return None
        return rows.index(current[-1])

    def _select_position(self, position: Optional[int]) -> None:
        """Выделяет строку, которая стоит на этом месте после перестроения таблицы.

        Исправленное фото уезжает на своё место по алфавиту, а на освободившееся встаёт
        следующий файл — и его как раз надо проверить: имя, которое было у исправленного,
        программа обычно передаёт ему. Поэтому выделение держится за место, а не за фото.
        """
        rows = self.tree.get_children()
        if position is None or not rows:
            return
        iid = rows[min(position, len(rows) - 1)]
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self._on_row_select()

    def _select_photo(self, index: Optional[int]) -> None:
        """Выделяет строку этого фото, где бы она после перестроения таблицы ни оказалась."""
        if index is None:
            return
        for iid, photo_index in self._row_index.items():
            if photo_index == index:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self._on_row_select()        # панель вариантов — для нового выбранного фото
                return

    def _settled_categories(self) -> Dict[str, int]:
        """Сколько фото каждой категории вы уже подтвердили: задали вручную или они уже названы."""
        settled: Dict[str, int] = {}
        for photo in self.match.photos:
            if photo.identified and photo.status in (matcher.STATUS_MANUAL, matcher.STATUS_ALREADY_NAMED):
                settled[photo.category.name] = settled.get(photo.category.name, 0) + 1
        return settled

    def _exhausted_categories(self) -> set:
        """Категории, которых в папке больше быть не может: состав совпадает с эталоном,
        а вы уже подтвердили столько фото, сколько их в эталоне.

        Считаются только подтверждённые вами фото, а не догадки программы: иначе неверная
        догадка закрывала бы категорию, и выбрать её стало бы негде.
        """
        if self.match is None or not self.match.strict:
            return set()
        capacity = {c.name: len(c.photos) for c in self.model.categories}
        settled = self._settled_categories()
        return {name for name, count in settled.items() if count >= capacity.get(name, 0) > 0}

    def _alternatives(self, photo) -> List:
        """Другие подходящие категории — без той, что уже в столбце «Новое имя», и без тех,
        что по составу эталона уже закрыты вашими же пометками.

        Предлагать то же самое, что программа и так выбрала, незачем: если имя верное, его
        просто оставляют. А категория, которой в папке ровно столько же, сколько вы уже
        отметили, третий раз встретиться не может — место в списке она занимает зря.
        """
        current = photo.category.name if photo.identified else None
        exhausted = self._exhausted_categories() - {current}
        return [c for c in photo.candidates if c.category != current and c.category not in exhausted]

    def apply_candidate(self, number: int) -> None:
        """Клавиши 1 и 2: каждому выбранному фото назначается его собственный вариант с этим номером."""
        indexes = self._selected_indexes()
        if not indexes:
            return
        position = self._selected_position()
        self._remember(indexes)
        applied = 0
        for index in indexes:
            candidates = self._alternatives(self.match.photos[index])
            if len(candidates) >= number:
                matcher.set_category(self.match, index, category_key(candidates[number - 1].category))
                applied += 1
        if not applied:
            self._undo.pop()                  # ничего не изменилось — и отменять нечего
            return
        self.render_rows()
        self._select_position(position)

    def _on_row_select(self) -> None:
        for widget in self.candidates_frame.winfo_children():
            widget.destroy()
        indexes = self._selected_indexes()
        if len(indexes) != 1:
            ttk.Label(self.candidates_frame, text="Выбрано фото: %d" % len(indexes)).pack(anchor="w")
            return
        photo = self.match.photos[indexes[0]]
        alternatives = self._alternatives(photo)
        if not alternatives:
            ttk.Label(self.candidates_frame, text="Других вариантов нет").pack(anchor="w")
        else:
            ttk.Label(self.candidates_frame, text="Похоже ещё на:").pack(anchor="w")
            for number, candidate in enumerate(alternatives[:2], start=1):   # 3 занята под отказ
                text = "%d — %s — %.0f%%" % (number, candidate.category, candidate.confidence)
                ttk.Button(self.candidates_frame, text=text,
                           style="Candidate.TButton",
                           command=lambda c=candidate.category: self.apply_category(c)).pack(
                    fill="x", pady=(0, BUTTON_GAP))
        ttk.Button(self.candidates_frame, text="3 — Не переименовывать", style="Candidate.TButton",
                   command=self.exclude_selected).pack(fill="x", pady=(0, BUTTON_GAP))

    def _all_categories(self) -> List[str]:
        """Категории эталона плюс добавленные вручную (names.txt рядом с программой)."""
        extra = [name for name in self.names.names() if name not in self.reference_categories]
        return self.reference_categories + sorted(extra)

    def add_category(self) -> None:
        """Деталь, которой нет в эталоне: имя добавляется в список и сразу доступно для выбора."""
        name = simpledialog.askstring(TITLE, "Новая категория:", parent=self,
                                      initialvalue=self.search_var.get().strip())
        if not name or not name.strip():
            return
        try:
            # число ракурсов не спрашиваем: для знакомого имени оно уже запомнено, для нового
            # берётся 1. Ракурсы задаются в редакторе эталона, где их видно по самим фото.
            name = self.names.add(name, self.names.sides_for(name))
        except ValueError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return
        self.names.save()
        if self.match is not None:
            matcher.add_category(self.match, name, self.names.sides_for(name))
        self.categories = self._all_categories()
        self.search_var.set("")
        self._refresh_categories()
        self._select_category(name)

    def remove_category(self) -> None:
        """Удаляет имя из списка. Категории самого эталона не трогаются: их задаёт образец."""
        selection = self.categories_list.curselection()
        if not selection:
            messagebox.showinfo(TITLE, "Выберите категорию в списке.", parent=self)
            return
        name = self.categories_list.get(selection[0])
        if name in self.reference_categories:
            messagebox.showinfo(TITLE, "«%s» — категория эталона, она задаётся образцом.\n"
                                       "Удалить её можно только из эталона." % name, parent=self)
            return
        if not messagebox.askyesno(TITLE, "Удалить «%s» из списка категорий?" % name, parent=self):
            return
        self.names.remove(name)
        self.names.save()
        self.categories = self._all_categories()
        self._refresh_categories()

    def _select_category(self, name: str) -> None:
        for position in range(self.categories_list.size()):
            if self.categories_list.get(position) == name:
                self.categories_list.selection_clear(0, "end")
                self.categories_list.selection_set(position)
                self.categories_list.see(position)
                break

    def _refresh_categories(self) -> None:
        self.categories_list.delete(0, "end")
        for name in names_mod.filter_names(self.search_var.get(), self.categories):
            self.categories_list.insert("end", name)
        self._fit_side_panel()

    def _fit_side_panel(self) -> None:
        """Ширина правой колонки — по самому длинному названию категории.

        Названия бывают длинными («Панель USB, VGA, кнопок и индикации»), и обрезанный
        список читать невозможно. Ширина считается по шрифту списка, а не в «знаках»:
        у пропорционального шрифта знаки разной ширины.
        """
        names = list(self.categories_list.get(0, "end")) or list(self.categories)
        if not names:
            return
        font = tkfont.Font(font=self.categories_list.cget("font"))
        needed = max(font.measure(name) for name in names) + SIDE_PADDING
        self.side.configure(width=max(MIN_SIDE_WIDTH, min(needed, MAX_SIDE_WIDTH)))

    def apply_selected_category(self) -> None:
        selection = self.categories_list.curselection()
        if not selection:
            messagebox.showinfo(TITLE, "Выберите категорию в списке справа.", parent=self)
            return
        self.apply_category(self.categories_list.get(selection[0]))

    def apply_category(self, category: Optional[str]) -> None:
        indexes = self._selected_indexes()
        if not indexes:
            messagebox.showinfo(TITLE, "Выберите фото в таблице.", parent=self)
            return
        key = None
        if category is not None:
            key = (matcher.add_category(self.match, category, self.names.sides_for(category))
                   if category not in self.reference_categories else category_key(category))
        position = self._selected_position()
        self._remember(indexes)
        for index in indexes:
            matcher.set_category(self.match, index, key)
        self.render_rows()
        self._select_position(position)

    def exclude_selected(self) -> None:
        self.apply_category(None)

    # --- переименование ------------------------------------------------------------------

    def apply_folder(self) -> None:
        if self.match is None:
            return
        try:
            plan = renamer.build_plan(self.match)
        except OSError as exc:
            messagebox.showerror(TITLE, "Папка недоступна:\n%s" % exc, parent=self)
            return
        if not plan.pending:
            messagebox.showinfo(TITLE, "Переименовывать нечего.", parent=self)
            self.next_folder()
            return
        # фото, помеченные «не переименовывать», и нечитаемые файлы — это осознанный выбор,
        # спрашивать про них незачем; предупреждаем только о тех, что программа не разобрала
        unresolved = len([p for p in self.match.unidentified()
                          if p.status not in (matcher.STATUS_EXCLUDED, matcher.STATUS_UNREADABLE)])
        if unresolved:
            question = ("Не опознано фото: %d. Их имена останутся прежними, а у остальных номера\n"
                        "будут идти по порядку съёмки без учёта пропущенных — потом будет сложно\n"
                        "понять, какой ракурс к какой детали.\n\n"
                        "Лучше разобрать их сейчас: включите «Показать только требующие проверки»\n"
                        "и назначьте категории клавишами 1 и 2.\n\n"
                        "Всё равно переименовать %d файл(ов)?") % (unresolved, len(plan.pending))
            if not messagebox.askyesno(TITLE, question, parent=self):
                return
        elif not messagebox.askyesno(TITLE, "Переименовать %d файл(ов) в папке\n%s?"
                                     % (len(plan.pending), self.folder.path), parent=self):
            return
        renamer.apply_plan(plan, self.log)
        self._last_plan = plan
        report = build_report(self.match, len(self.folder.scan.excluded), self.app_config.problem_categories)
        text = format_report(report, plan, applied=True)
        self.log.write_text(text)
        self.undo_btn.configure(state="normal")
        self.status_var.set("Переименовано: %d, пропущено: %d, ошибок: %d"
                            % (plan.count(oplog.RESULT_OK), plan.count(oplog.RESULT_SKIPPED),
                               plan.count(oplog.RESULT_ERROR)))
        ReportDialog(self, text, on_next=self.next_folder)

    def undo_folder(self) -> None:
        if self._last_plan is None:
            return
        done = [item for item in self._last_plan.items if item.result == oplog.RESULT_OK]
        plan = renamer.RenamePlan(self._last_plan.folder,
                                  [renamer.RenameItem(i.new, i.old, i.category, None, "отмена") for i in
                                   reversed(done)])
        renamer.apply_plan(plan, self.log)
        self._last_plan = None
        self.undo_btn.configure(state="disabled")
        self.status_var.set("Переименование папки отменено: %d файл(ов)" % plan.count(oplog.RESULT_OK))

    def skip_folder(self) -> None:
        if self.folder is not None:
            self.log.record(self.folder.path, oplog.ACTION_KEEP, oplog.RESULT_SKIPPED,
                            message="папка пропущена пользователем")
        self.next_folder()

    def finish(self) -> None:
        self.header_var.set("Готово")
        self.status_var.set("Обработано папок: %d. Лог: %s" % (self.done_folders, self.log.csv_path))
        self.apply_btn.configure(state="disabled")
        self.skip_btn.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        messagebox.showinfo(TITLE, "Группа обработана.\n\nЛог: %s\nОтчёт: %s"
                            % (self.log.csv_path, self.log.txt_path), parent=self)

    def close(self) -> None:
        if self._busy:
            self._cancel = True
            return
        self.log.close()
        self.cache.close()
        try:                       # выбранный размер фото должен сохраниться до следующего запуска
            cfg.save_config(self.app_config)
        except OSError:
            pass
        if self.on_done:
            self.on_done()
        self.destroy()


class ReportDialog(tk.Toplevel):
    """Отчёт по папке после переименования."""

    def __init__(self, master, text: str, on_next=None):
        super().__init__(master)
        self.on_next = on_next
        self.title("Отчёт по папке")
        self.geometry("900x560")
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        widget = tk.Text(frame, wrap="word", height=25)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        widget.configure(yscrollcommand=scroll.set)
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        widget.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Остаться в папке", command=self.destroy).pack(side="left")
        ttk.Button(buttons, text="Следующая папка", command=self.go_next).pack(side="right")
        self.transient(master)
        self.focus_set()

    def go_next(self) -> None:
        callback = self.on_next
        self.destroy()
        if callback:
            callback()
