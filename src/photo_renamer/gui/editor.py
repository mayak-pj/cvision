"""Редактор эталона: выделить фото → выбрать категорию и число ракурсов → переименовать.

Слева сетка миниатюр папки (порядок съёмки), справа список категорий с поиском.
Каждое переименование — только os.rename в той же папке, записывается в лог,
отменяется по Ctrl+Z.
"""

import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import List, Optional

from .. import editing, features as feat, oplog, renamer
from ..cache import PhotoCache
from ..names import NamesList
from ..reference import is_template_name, load_reference
from ..scanner import list_photos
from . import theme
from .thumbgrid import MAX_SIZE, MIN_SIZE, ThumbnailGrid, to_photo
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


class ReferenceEditor(tk.Toplevel):
    def __init__(self, master, folder: str, app_config, names: NamesList, on_done=None):
        super().__init__(master)
        self.folder = folder
        self.app_config = app_config
        self.names = names
        self.on_done = on_done
        self.cache = PhotoCache(app_config.cache_path(), app_config.cache_max_mb)
        self.worker = Worker(self)
        self.log = oplog.RunLog(app_config.logs_path(), "эталон_" + oplog.folder_label(folder))
        self.photos: List[str] = []          # имена в порядке съёмки (порядок не меняется)
        self.history: List[List[renamer.RenameItem]] = []

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
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-a>", lambda e: self.grid_widget.select_all())

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
        ttk.Label(side, text="Категория").pack(anchor="w")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._refresh_names())
        search = ttk.Entry(side, textvariable=self.search_var)
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

        buttons = ttk.Frame(side)
        buttons.pack(fill="x", pady=4)
        ttk.Button(buttons, text="Добавить…", command=self.add_name).pack(side="left", expand=True, fill="x")
        ttk.Button(buttons, text="Удалить", command=self.remove_name).pack(side="left", expand=True, fill="x",
                                                                           padx=(4, 0))

        sides_frame = ttk.Frame(side)
        sides_frame.pack(fill="x", pady=(8, 4))
        ttk.Label(sides_frame, text="Ракурсов (фото на одну деталь):").pack(side="left")
        self.sides_var = tk.IntVar(value=1)
        ttk.Spinbox(sides_frame, from_=1, to=10, width=4, textvariable=self.sides_var).pack(side="left", padx=6)

        self.rename_btn = ttk.Button(side, text="Переименовать выделенные", command=self.rename_selected)
        self.rename_btn.pack(fill="x", pady=(6, 2))
        self.undo_btn = ttk.Button(side, text="Отменить (Ctrl+Z)", command=self.undo, state="disabled")
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
            scan = list_photos(self.folder, sort=self.app_config.sort, date_source=self.cache.shot_time)
        except OSError as exc:
            messagebox.showerror(TITLE, "Не удалось прочитать папку:\n%s" % exc, parent=self)
            self.photos = []
            return
        self.photos = list(scan.photos)
        self.order_differs = scan.order_differs
        self.grid_widget.set_items(self._items(), self._template_flags())
        self._refresh_names()
        self._update_status()

    def _items(self):
        return [(name, os.path.join(self.folder, name)) for name in self.photos]

    def _template_flags(self):
        return [is_template_name(name) for name in self.photos]

    def _learn_from_reference(self) -> None:
        """Если в папке уже есть названные фото, запоминаем их категории и число ракурсов."""
        try:
            reference = load_reference(self.folder)
        except OSError:
            return
        categories = [(c.name, c.sides) for c in reference.categories.values()
                      if any(is_template_name(p.filename) for p in c.photos)]
        if categories:
            self.names.learn_from(categories)
            self.names.save()
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
        named = sum(self._template_flags())
        text = "Фото: %d, названо по шаблону: %d, осталось: %d" % (len(self.photos), named, len(self.photos) - named)
        if getattr(self, "order_differs", False):
            text += "   (порядок съёмки не совпадает с порядком имён — показан порядок съёмки)"
        if loading:
            text += "   загрузка миниатюр: %d из %d" % loading
        self.status_var.set(text)

    def _zoom(self, delta: int) -> None:
        self.grid_widget.set_cell_size(min(MAX_SIZE, max(MIN_SIZE, self.grid_widget.cell_size + delta)))

    # --- действия ------------------------------------------------------------------------

    def add_name(self) -> None:
        name = simpledialog.askstring(TITLE, "Новая категория:", parent=self,
                                      initialvalue=self.search_var.get().strip())
        if not name or not name.strip():
            return
        try:
            name = self.names.add(name, self.sides_var.get())
        except ValueError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return
        self.names.save()
        self.search_var.set("")
        self._refresh_names()
        for i in range(self.names_list.size()):
            if self.names_list.get(i) == name:
                self.names_list.selection_clear(0, "end")
                self.names_list.selection_set(i)
                self.names_list.see(i)
                break

    def remove_name(self) -> None:
        name = self.current_name()
        if not name:
            return
        if messagebox.askyesno(TITLE, "Удалить «%s» из списка категорий?" % name, parent=self):
            self.names.remove(name)
            self.names.save()
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
            assignment = editing.assign(self.folder, self.photos, selected, name, self.sides_var.get())
        except ValueError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return

        if assignment.warnings:
            question = "\n".join(assignment.warnings) + "\n\nПродолжить переименование?"
            if not messagebox.askyesno(TITLE, question, parent=self):
                return
        renamer.apply_plan(assignment.plan, self.log)
        done = [item for item in assignment.plan.items if item.result == oplog.RESULT_OK]
        failed = [item for item in assignment.plan.items if item.result == oplog.RESULT_ERROR]
        if done:
            self.history.append(done)
            self.undo_btn.configure(state="normal")
        self.names.remember_sides(name, self.sides_var.get())
        self.names.save()
        self._apply_renames(done)
        if failed:
            messagebox.showerror(TITLE, "Не удалось переименовать:\n" +
                                 "\n".join("%s: %s" % (i.old, i.message) for i in failed), parent=self)
        self.grid_widget.clear_selection()

    def _apply_renames(self, done) -> None:
        """Обновляет имена в сетке, сохраняя порядок съёмки."""
        mapping = {item.old: item.new for item in done}
        self.photos = [mapping.get(name, name) for name in self.photos]
        self.grid_widget.update_names(self._items(), self._template_flags())
        self._update_status()

    def undo(self) -> None:
        if not self.history:
            return
        done = self.history.pop()
        plan = editing.undo_plan(self.folder, done)
        renamer.apply_plan(plan, self.log)
        self._apply_renames([item for item in plan.items if item.result == oplog.RESULT_OK])
        if not self.history:
            self.undo_btn.configure(state="disabled")

    def _open_large(self, index: int) -> None:
        LargeView(self, self.folder, self.photos, index, self.cache)

    def close(self) -> None:
        self.worker.cancel()
        self.worker.wait(5)
        self.log.close()
        self.cache.close()
        if self.on_done:
            self.on_done()
        self.destroy()
