"""Пакетное переименование группы папок с предпросмотром.

Папки обрабатываются по одной. Для каждой показывается таблица «текущее имя → новое имя»
с миниатюрой и % уверенности. Неопознанные фото можно исправить: выбрать кандидата одним
щелчком или категорию из списка. Переименование выполняется только после подтверждения,
пишется в лог и может быть отменено.
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from .. import config as cfg, grouping, review_session  # noqa: F401 — cfg подменяют тесты
from ..review_session import ALTERNATIVES, MAX_THUMB, MIN_THUMB, THUMB_STEP, ReviewSession  # noqa: F401
from . import theme
from .photoview import LargeView
from .thumbgrid import to_photo

TITLE = "Переименование папок"
BUTTON_GAP = 4            # одинаковый зазор между всеми кнопками окна
SIDE_PADDING = 44         # полоса прокрутки, рамки и отступы правой колонки
MIN_SIDE_WIDTH, MAX_SIDE_WIDTH = 260, 460
# столбец таблицы → поле сортировки; у остальных столбцов имена совпадают
SORT_FIELDS = {"#0": review_session.SORT_PHOTO}


class Cancelled(Exception):
    pass


def _from_session(name: str) -> property:
    """Состояние живёт в ReviewSession, окно только обращается к нему."""
    return property(lambda self: getattr(self.session, name),
                    lambda self, value: setattr(self.session, name, value))


class BatchWindow(tk.Toplevel):
    group = _from_session("group")
    cache = _from_session("cache")
    log = _from_session("log")
    queue = _from_session("queue")
    total = _from_session("total")
    model = _from_session("model")
    match = _from_session("match")
    folder = _from_session("folder")
    categories = _from_session("categories")
    reference_categories = _from_session("reference_categories")
    reports = _from_session("reports")

    def __init__(self, master, group: grouping.FolderGroup, app_config, on_done=None, names=None):
        super().__init__(master)
        self.app_config = app_config
        self.session = ReviewSession(group, app_config, names)
        self.names = self.session.names
        self.on_done = on_done
        self._photos: Dict[str, tk.PhotoImage] = {}
        self._row_index: Dict[str, int] = {}
        self.thumb_size = int(getattr(app_config, "review_thumb", 96))
        self._busy = False
        self._cancel = False

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
        self.undo_btn = ttk.Button(bottom, text="Отменить переименование", command=self.undo_folder,
                                   state="disabled")
        self.skip_btn = ttk.Button(bottom, text="Пропустить папку", command=self.skip_folder)
        self.apply_btn = ttk.Button(bottom, command=self.apply_folder,
                                    text="Переименовать папку (%s)" % theme.shortcut_label(self, "s"))
        self.close_btn = ttk.Button(bottom, text="Закрыть", command=self.close)
        for button in (self.close_btn, self.apply_btn, self.skip_btn, self.undo_btn, self.undo_edit_btn):
            button.pack(side="right", padx=BUTTON_GAP)
        ttk.Label(bottom, textvariable=self.status_var, wraplength=520, justify="left").pack(
            side="left", fill="x", expand=True)

        body = ttk.Frame(self, padding=(10, 0, 10, 0))
        body.pack(fill="both", expand=True)

        table = ttk.Frame(body)        # размещается после правой панели, см. ниже
        self.style = ttk.Style(self)
        # текст вариантов прижат к левому краю: номера 1, 2 и 3 оказываются друг под другом
        self.style.configure("Candidate.TButton", anchor="w")
        self.tree = ttk.Treeview(table, columns=("new", "confidence", "status"), selectmode="extended",
                                 style="Review.Treeview")
        # по заголовкам столбцов сортируют щелчком: первый щелчок — по возрастанию, второй — наоборот
        for column, title in self.COLUMN_TITLES:
            self.tree.heading(column, text=title, command=lambda c=column: self.sort_by(c))
        self._show_sort_marks()
        # Границы столбцов можно тянуть мышью:
        # ни один столбец не растягивается, поэтому меняется только тот, который тянут,
        # а если столбцы перестали помещаться, снизу появляется полоса прокрутки.
        # Значения прижаты к левому краю — строка читается слева направо.
        self.tree.column("new", width=280, minwidth=120, anchor="w")
        self.tree.column("confidence", width=90, minwidth=70, anchor="w")
        self.tree.column("status", width=150, minwidth=90, anchor="w")
        self._apply_thumb_size()
        theme.fit_column_headers(self.tree)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.hscroll = ttk.Scrollbar(table, orient="horizontal")
        self.tree.configure(yscrollcommand=scroll.set)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.hscroll.grid(row=1, column=0, sticky="ew")
        self._update_hscroll = theme.fixed_columns(self.tree, self.hscroll)
        palette = theme.colors()
        self.tree.tag_configure("doubt", foreground=palette["warning"])
        self.tree.tag_configure("manual", foreground=palette["accent"])
        self.tree.tag_configure("skip", foreground=palette["muted"])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_row_select())
        # 1 и 2 — выбрать кандидата, 3 — не переименовывать: пометка «не переименовывать» нужна
        # чаще третьего кандидата, а под рукой должны быть три соседние клавиши
        for number in range(1, ALTERNATIVES + 1):
            self.tree.bind("<Key-%d>" % number, lambda e, n=number: self.apply_candidate(n))
        for key in ("<Key-%d>" % (ALTERNATIVES + 1), "<Key-0>"):
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
        # Ctrl+S переименовывает папку — как в редакторе эталона. Работает и когда курсор
        # в поле категории: своего Ctrl+S у поля нет
        theme.bind_shortcut(self, "s", self.apply_folder, in_fields=True)

        # Правая панель размещается ДО таблицы: при нехватке места Tk ужимает то, что
        # размещено позже, и раньше таблица съедала ширину у списка категорий
        self.side = side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="right", fill="y")
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
        self.search_entry = search_entry = ttk.Entry(side, textvariable=self.search_var)
        search_entry.pack(fill="x", pady=(BUTTON_GAP, BUTTON_GAP))
        buttons = ttk.Frame(side)
        buttons.pack(side="bottom", fill="x")      # кнопки не должны уезжать за край на низком экране
        sides_frame = ttk.Frame(buttons)
        sides_frame.pack(fill="x", pady=(BUTTON_GAP, 0))
        ttk.Label(sides_frame, text="Ракурсов (фото на одну деталь):").pack(side="left")
        self.sides_var = tk.IntVar(value=1)
        self.sides_spin = ttk.Spinbox(sides_frame, from_=1, to=10, width=4, textvariable=self.sides_var)
        self.sides_spin.pack(side="left", padx=6)
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
        self.categories_list.bind("<<ListboxSelect>>", lambda e: self._show_sides(force=True))
        for widget in (self.categories_list, search_entry):   # отмена работает, где бы ни был курсор
            self._bind_undo(widget)

        table.pack(side="left", fill="both", expand=True)

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
            self.session.load_model(self._progress)
        except Cancelled:
            self.close()
            return
        except OSError as exc:
            messagebox.showerror(TITLE, "Не удалось прочитать эталон:\n%s" % exc, parent=self)
            self.close()
            return
        finally:
            self._busy = False
        self._refresh_categories()
        self.next_folder()

    def next_folder(self) -> None:
        # Отмена последнего переименования остаётся доступной и после перехода к следующей папке:
        # отчёт по папке больше не показывается, и это единственная возможность передумать.
        if not self.session.advance():
            self.finish()
            return
        self.header_var.set(self.session.header_text())
        self.status_var.set("Чтение фото…")
        self._busy = True
        try:
            self.session.match_current(self._progress)
        except Cancelled:
            self.close()
            return
        except OSError as exc:
            messagebox.showerror(TITLE, "Папка недоступна:\n%s" % exc, parent=self)
            self.next_folder()
            return
        finally:
            self._busy = False
        self.resort()
        self._select_position(0)          # новая папка открывается с первой строки
        self.tree.yview_moveto(0)
        self.load_thumbnails()

    COLUMN_TITLES = (("#0", "Фото"), ("new", "Новое имя"),
                     ("confidence", "Уверенность"), ("status", "Состояние"))

    def sort_by(self, column: str) -> None:
        """Щелчок по заголовку столбца: сортировка по нему, повторный щелчок — в обратную сторону."""
        self.session.sort_by(SORT_FIELDS.get(column, column))
        self._show_sort_marks()
        self.render_rows()

    def resort(self) -> None:
        """Пересчитывает порядок строк по выбранному столбцу и показывает таблицу заново."""
        self.session.resort()
        self.render_rows()

    def _show_sort_marks(self) -> None:
        arrow = " ↑" if not self.session.sort_reverse else " ↓"
        for column, title in self.COLUMN_TITLES:
            current = SORT_FIELDS.get(column, column) == self.session.sort_column
            self.tree.heading(column, text=title + (arrow if current else ""))
        theme.fit_column_headers(self.tree)

    def render_rows(self) -> None:
        # выделение запоминается по фото: строки пересоздаются, и их идентификаторы меняются
        selected = set(self._selected_indexes())
        self.tree.delete(*self.tree.get_children())
        self._row_index.clear()
        for photo in self.session.display_photos():
            kind = review_session.row_kind(photo)
            iid = self.tree.insert("", "end", text=" " + photo.filename, values=review_session.row_values(photo),
                                   image=self._photos.get(photo.filename, ""), tags=(kind,) if kind else ())
            self._row_index[iid] = photo.index
            if photo.index in selected:
                self.tree.selection_add(iid)
        self._update_counts()

    def _update_counts(self) -> None:
        self.status_var.set(self.session.counts_text())

    def _apply_thumb_size(self) -> None:
        self.style.configure("Review.Treeview", rowheight=self.thumb_size + 8)
        self.tree.column("#0", width=self.thumb_size + 200, minwidth=120)
        if getattr(self, "_update_hscroll", None):   # размер фото меняет ширину первого столбца
            self._update_hscroll()

    def set_thumb_size(self, size: int) -> None:
        """Размер фото в строках таблицы. Миниатюры перерисовываются из кэша, файлы не перечитываются."""
        size = review_session.clamp_thumb_size(size)
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
            return to_photo(self.session.thumbnail(self.match.photos[index]), size)
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
                self._photos[photo.filename] = to_photo(self.session.thumbnail(photo), self.thumb_size)
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
        LargeView(self, self.folder.path, self.session.photo_names(), indexes[0], self.cache)
        return "break"

    # «<<Undo>>» — обязательно: на macOS Cmd+Z перехватывает стандартное меню «Правка»,
    # и обычная привязка к сочетанию клавиш до окна не доходит.
    def _bind_undo(self, widget) -> None:
        """Ctrl+Z (на macOS — Cmd+Z) работает, где бы ни был курсор, и в любой раскладке."""
        theme.bind_shortcut(widget, "z", self.undo_edit, in_fields=True)
        widget.bind("<<Undo>>", self.undo_edit)

    def undo_edit(self, event=None):
        """Ctrl+Z — отменяет последнюю ручную правку: и выбор цифрой, и Enter, и кнопки, и список."""
        indexes = self.session.undo_edit()
        if indexes is None:
            self.status_var.set("Отменять нечего")
            return "break"
        self.render_rows()
        self._select_photos(indexes)
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
        self.session.confirm(indexes)
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

    def _exhausted_categories(self) -> set:
        return self.session.exhausted_categories()

    def _alternatives(self, photo) -> List:
        return self.session.alternatives(photo)

    def apply_candidate(self, number: int) -> None:
        """Клавиши 1 и 2: каждому выбранному фото назначается его собственный вариант с этим номером."""
        indexes = self._selected_indexes()
        if not indexes:
            return
        position = self._selected_position()
        if not self.session.apply_candidate(indexes, number):
            return
        self.render_rows()
        self._select_position(None if position is None else position + 1)

    def _on_row_select(self) -> None:
        self._show_sides()          # выделили другое число фото — подсказка про ракурсы другая
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
            for number, candidate in enumerate(alternatives[:ALTERNATIVES], start=1):
                text = "%d — %s — %.0f%%" % (number, candidate.category, candidate.confidence)
                ttk.Button(self.candidates_frame, text=text,
                           style="Candidate.TButton",
                           command=lambda c=candidate.category: self.apply_category(c)).pack(
                    fill="x", pady=(0, BUTTON_GAP))
        ttk.Button(self.candidates_frame, text="%d — Не переименовывать" % (ALTERNATIVES + 1),
                   style="Candidate.TButton",
                   command=self.exclude_selected).pack(fill="x", pady=(0, BUTTON_GAP))

    def add_category(self) -> None:
        """Деталь, которой нет в эталоне: набранное в поиске имя сразу попадает в список.

        Отдельного окна с «ОК» нет: человек уже ввёл название, увидел пустой список —
        значит, такой категории нет, — и нажал «+».
        """
        typed = self.search_var.get().strip()
        if not typed:
            self.status_var.set("Наберите название категории в поле справа и нажмите «+»")
            self.search_entry.focus_set()
            return
        try:
            name = self.session.add_category(typed)
        except ValueError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return
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
        if self.session.is_reference_category(name):
            messagebox.showinfo(TITLE, "«%s» — категория эталона, она задаётся образцом.\n"
                                       "Удалить её можно только из эталона." % name, parent=self)
            return
        if not messagebox.askyesno(TITLE, "Удалить «%s» из списка категорий?" % name, parent=self):
            return
        self.session.remove_category(name)
        self._refresh_categories()

    def _select_category(self, name: str) -> None:
        for position in range(self.categories_list.size()):
            if self.categories_list.get(position) == name:
                self.categories_list.selection_clear(0, "end")
                self.categories_list.selection_set(position)
                self.categories_list.see(position)
                break
        self._show_sides()

    def _show_sides(self, force: bool = False) -> None:
        """Показывает, сколько ракурсов получит выбранная категория, — это можно исправить."""
        category = self._current_category()
        if category is None:
            return
        value = self.session.suggest_sides(category, len(self._selected_indexes()), self._sides_field(), force)
        if value is None:
            return
        self.sides_var.set(value)
        self.sides_spin.configure(state="disabled" if self.session.is_reference_category(category) else "normal")

    def _sides_field(self) -> Optional[int]:
        """Значение поля «Ракурсов»; None — в поле мусор."""
        try:
            return int(self.sides_var.get())
        except (tk.TclError, ValueError):
            return None

    def _current_category(self) -> Optional[str]:
        selection = self.categories_list.curselection()
        return self.categories_list.get(selection[0]) if selection else None

    def _refresh_categories(self) -> None:
        self.categories_list.delete(0, "end")
        for name in self.session.filter_categories(self.search_var.get()):
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
        position = self._selected_position()
        self.session.apply_category(indexes, category, self._sides_field())
        self.render_rows()
        self._select_position(None if position is None else position + 1)

    def exclude_selected(self) -> None:
        self.apply_category(None)

    # --- переименование ------------------------------------------------------------------

    def apply_folder(self) -> None:
        if self.match is None or str(self.apply_btn["state"]) == "disabled" or self._busy:
            return
        try:
            plan = self.session.build_plan()
        except OSError as exc:
            messagebox.showerror(TITLE, "Папка недоступна:\n%s" % exc, parent=self)
            return
        if not plan.pending:
            messagebox.showinfo(TITLE, "Переименовывать нечего.", parent=self)
            self.next_folder()
            return
        unresolved = self.session.unresolved_count()
        if unresolved:
            question = review_session.unresolved_question(unresolved, len(plan.pending))
            if not messagebox.askyesno(TITLE, question, parent=self):
                return
        summary = self.session.apply_plan(plan)
        self.undo_btn.configure(state="normal")
        self.status_var.set(summary)
        self.next_folder()

    def undo_folder(self) -> None:
        summary = self.session.undo_folder()
        if summary is None:
            return
        self.undo_btn.configure(state="disabled")
        self.status_var.set(summary)

    def skip_folder(self) -> None:
        self.session.skip_folder()
        self.next_folder()

    def finish(self) -> None:
        self.header_var.set("Готово")
        self.apply_btn.configure(state="disabled")
        self.skip_btn.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.status_var.set(self.session.finish_text())

    def close(self) -> None:
        if self._busy:
            self._cancel = True
            return
        self.session.close()
        if self.on_done:
            self.on_done()
        self.destroy()


