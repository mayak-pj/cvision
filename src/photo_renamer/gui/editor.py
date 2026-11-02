"""Редактор эталона: выделить фото → выбрать категорию и число ракурсов → переименовать.

Слева сетка миниатюр папки (порядок съёмки), справа список категорий с поиском.
Каждое переименование — только os.rename в той же папке, записывается в лог,
отменяется по Ctrl+Z.
"""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from ..cache import PhotoCache
from ..editor_session import EditorSession
from ..names import NamesList
from . import theme
from .photoview import LargeView
from .thumbgrid import MAX_SIZE, MIN_SIZE, ThumbnailGrid
from .worker import Worker

TITLE = "Эталон"


class _ThumbnailLoader:
    """Источник миниатюр для фоновой задачи: вызывается как функция, умеет закрыть свой кэш."""

    def __init__(self, cache: PhotoCache):
        self.cache = cache

    def __call__(self, path: str):
        return self.cache.thumbnail(path)

    def close(self) -> None:
        self.cache.close()


def _from_session(name: str) -> property:
    """Состояние живёт в EditorSession, окно только обращается к нему."""
    return property(lambda self: getattr(self.session, name),
                    lambda self, value: setattr(self.session, name, value))


class ReferenceEditor(tk.Toplevel):
    cache = _from_session("cache")
    log = _from_session("log")
    photos = _from_session("photos")
    shown = _from_session("shown")
    history = _from_session("history")

    def __init__(self, master, folder: str, app_config, names: NamesList, on_done=None):
        super().__init__(master)
        self.folder = folder
        self.app_config = app_config
        self.names = names
        self.on_done = on_done
        self.session = EditorSession(folder, app_config, names,
                                     PhotoCache(app_config.cache_path(), app_config.cache_max_mb))
        self.worker = Worker(self)

        self.title("%s: %s" % (TITLE, folder))
        self.minsize(900, 560)
        theme.fit_to_screen(self)     # не под заголовком окна: иначе верхняя панель перекрыта
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.lift()
        self.focus_force()
        self._load_folder()
        self._learn_from_reference()
        theme.bind_shortcut(self, "z", self.undo)
        theme.bind_shortcut(self, "a", self.grid_widget.select_all)
        # переименование работает и когда курсор в поле поиска: своего Ctrl+S у поля нет,
        # а набрать категорию и тут же нажать сочетание — обычный ход работы
        theme.bind_shortcut(self, "s", self.rename_selected, in_fields=True)

    # --- построение окна -----------------------------------------------------------------

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=(10, 8, 10, 4))
        toolbar.pack(fill="x")
        self.selection_var = tk.StringVar(value="Выделено: 0")
        ttk.Label(toolbar, textvariable=self.selection_var).pack(side="left")
        ttk.Button(toolbar, text="Выделить всё", command=lambda: self.grid_widget.select_all()).pack(side="left",
                                                                                                     padx=(12, 2))
        ttk.Button(toolbar, text="Снять выделение", command=lambda: self.grid_widget.clear_selection()).pack(
            side="left", padx=2)
        ttk.Label(toolbar, text="Размер миниатюр (Ctrl+колёсико):").pack(side="left", padx=(20, 4))
        ttk.Button(toolbar, text="−", width=3, command=lambda: self._zoom(-40)).pack(side="left")
        ttk.Button(toolbar, text="+", width=3, command=lambda: self._zoom(40)).pack(side="left", padx=(2, 0))

        body = ttk.Frame(self, padding=(10, 0, 10, 0))
        body.pack(fill="both", expand=True)
        self.grid_widget = ThumbnailGrid(body, load_thumbnail=self.cache.thumbnail,
                                         on_selection_change=self._on_selection,
                                         on_activate=self._open_large,
                                         on_load_progress=self._on_load_progress,
                                         worker=self.worker, make_loader=self._make_loader)
        self.grid_widget.pack(side="left", fill="both", expand=True)

        side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="left", fill="y")
        side.configure(width=330)
        header = ttk.Frame(side)
        header.pack(fill="x")
        ttk.Label(header, text="Категория").pack(side="left")
        theme.IconButton(header, "delete", command=self.remove_name,
                         hint="Удалить категорию из списка").pack(side="right")
        theme.IconButton(header, "add", command=self.add_name,
                         hint="Добавить категорию в список").pack(side="right", padx=(0, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._refresh_names())
        self.search_entry = search = ttk.Entry(side, textvariable=self.search_var)
        search.pack(fill="x", pady=(2, 4))
        search.bind("<Down>", lambda e: (self.names_list.focus_set(), self._select_first()))
        search.bind("<Return>", lambda e: self.rename_selected())

        list_frame = ttk.Frame(side)
        list_frame.pack(fill="both", expand=True)
        self.names_list = tk.Listbox(list_frame, exportselection=False, activestyle="none", height=18)
        names_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.names_list.yview)
        self.names_list.configure(yscrollcommand=names_scroll.set)
        self.names_list.pack(side="left", fill="both", expand=True)
        names_scroll.pack(side="left", fill="y")
        self.names_list.bind("<<ListboxSelect>>", lambda e: self._on_name_selected())
        self.names_list.bind("<Return>", lambda e: self.rename_selected())
        self.names_list.bind("<Double-Button-1>", lambda e: self.rename_selected())

        sides_frame = ttk.Frame(side)
        sides_frame.pack(fill="x", pady=(8, 4))
        ttk.Label(sides_frame, text="Ракурсов (фото на одну деталь):").pack(side="left")
        self.sides_var = tk.IntVar(value=1)
        ttk.Spinbox(sides_frame, from_=1, to=10, width=4, textvariable=self.sides_var).pack(side="left", padx=6)

        self.rename_btn = ttk.Button(side, command=self.rename_selected,
                                     text="Переименовать выделенные (%s)" % theme.shortcut_label(self, "s"))
        self.rename_btn.pack(fill="x", pady=(6, 2))
        self.undo_btn = ttk.Button(side, command=self.undo, state="disabled",
                                   text="Отменить (%s)" % theme.shortcut_label(self, "z"))
        self.undo_btn.pack(fill="x", pady=2)
        ttk.Separator(side).pack(fill="x", pady=8)
        ttk.Button(side, text="Готово", command=self.close).pack(fill="x")

        bottom = ttk.Frame(self, padding=(10, 6, 10, 10))
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(bottom, length=220, mode="determinate")
        self.progress.pack(side="left")

    # --- данные --------------------------------------------------------------------------

    def _make_loader(self):
        """Вызывается внутри фонового потока: открывает свой кэш, потому что соединение SQLite
        принадлежит создавшему его потоку."""
        return _ThumbnailLoader(PhotoCache(self.app_config.cache_path(), self.app_config.cache_max_mb))

    def _load_folder(self) -> None:
        try:
            self.session.load_folder()
        except OSError as exc:
            messagebox.showerror(TITLE, "Не удалось прочитать папку:\n%s" % exc, parent=self)
            return
        self.grid_widget.set_items(self._items(), self._template_flags())
        self._refresh_names()
        self._update_status()

    def _items(self):
        return self.session.items()

    def _template_flags(self):
        return self.session.template_flags()

    def _learn_from_reference(self) -> None:
        if self.session.learn_from_reference():
            self._refresh_names()

    def _refresh_names(self) -> None:
        current = self.current_name()
        self.names_list.delete(0, "end")
        for name in self.names.search(self.search_var.get()):
            self.names_list.insert("end", name)
        if current:
            for i in range(self.names_list.size()):
                if self.names_list.get(i) == current:
                    self.names_list.selection_set(i)
                    break

    def _select_first(self) -> None:
        if self.names_list.size():
            self.names_list.selection_clear(0, "end")
            self.names_list.selection_set(0)
            self._on_name_selected()

    def current_name(self) -> Optional[str]:
        selection = self.names_list.curselection()
        return self.names_list.get(selection[0]) if selection else None

    def _on_name_selected(self) -> None:
        name = self.current_name()
        if name:
            self.sides_var.set(self.names.sides_for(name))

    def _on_selection(self) -> None:
        self.selection_var.set("Выделено: %d" % len(self.grid_widget.selection))

    def _on_load_progress(self, done: int, total: int) -> None:
        self.progress.configure(maximum=max(total, 1), value=done)
        if done >= total:
            self.progress.configure(value=0)
        self._update_status(loading=None if done >= total else (done, total))

    def _update_status(self, loading=None) -> None:
        self.status_var.set(self.session.status_text(loading))

    def _zoom(self, delta: int) -> None:
        self.grid_widget.set_cell_size(min(MAX_SIZE, max(MIN_SIZE, self.grid_widget.cell_size + delta)))

    # --- действия ------------------------------------------------------------------------

    def add_name(self) -> None:
        """Добавляет то, что набрано в поле поиска: человек и так видит, что такой категории нет.

        Отдельного окна с «ОК» нет: имя уже введено, переспрашивать его нечего.
        """
        typed = self.search_var.get().strip()
        if not typed:
            self.status_var.set("Наберите название категории в поле справа и нажмите «+»")
            self.search_entry.focus_set()
            return
        try:
            name = self.session.add_name(typed, self.sides_var.get())
        except ValueError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return
        self.search_var.set("")
        self._refresh_names()
        self._select_name(name)

    def _select_name(self, name: str) -> None:
        for i in range(self.names_list.size()):
            if self.names_list.get(i) == name:
                self.names_list.selection_clear(0, "end")
                self.names_list.selection_set(i)
                self.names_list.see(i)
                self._on_name_selected()
                break

    def remove_name(self) -> None:
        name = self.current_name()
        if not name:
            messagebox.showinfo(TITLE, "Выберите категорию в списке.", parent=self)
            return
        if messagebox.askyesno(TITLE, "Удалить «%s» из списка категорий?" % name, parent=self):
            self.session.remove_name(name)
            self._refresh_names()

    def rename_selected(self) -> None:
        name = self.current_name()
        selected = self.grid_widget.selected_names()
        if not name:
            messagebox.showinfo(TITLE, "Выберите категорию в списке справа.", parent=self)
            return
        if not selected:
            messagebox.showinfo(TITLE, "Выделите фото слева.", parent=self)
            return
        try:
            sides = self.sides_var.get()
            assignment = self.session.plan_rename(selected, name, sides)
        except ValueError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return

        if assignment.warnings:
            question = "\n".join(assignment.warnings) + "\n\nПродолжить переименование?"
            if not messagebox.askyesno(TITLE, question, parent=self):
                return
        done, failed = self.session.apply_rename(assignment, name, sides)
        if done:
            self.undo_btn.configure(state="normal")
        self._apply_renames(done)
        if failed:
            messagebox.showerror(TITLE, "Не удалось переименовать:\n" +
                                 "\n".join("%s: %s" % (i.old, i.message) for i in failed), parent=self)
        self.grid_widget.clear_selection()

    def _apply_renames(self, done) -> None:
        """Обновляет имена в сетке и переставляет ячейки в порядок показа."""
        order = self.session.apply_renames(done)
        self.grid_widget.reorder(order, self._items(), self._template_flags())
        self._update_status()

    def undo(self) -> None:
        done = self.session.undo()
        if done is None:
            return
        self._apply_renames(done)
        if not self.history:
            self.undo_btn.configure(state="disabled")

    def _open_large(self, index: int) -> None:
        LargeView(self, self.folder, self.shown, index, self.cache)   # листается как показано

    def close(self) -> None:
        self.worker.cancel()
        self.worker.wait(5)
        self.session.close()
        if self.on_done:
            self.on_done()
        self.destroy()
