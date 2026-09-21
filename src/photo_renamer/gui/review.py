"""Пакетное переименование группы папок с предпросмотром.

Папки обрабатываются по одной. Для каждой показывается таблица «текущее имя → новое имя»
с миниатюрой и % уверенности. Неопознанные фото можно исправить: выбрать кандидата одним
щелчком или категорию из списка. Переименование выполняется только после подтверждения,
пишется в лог и может быть отменено.
"""

import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from .. import grouping, matcher, names as names_mod, oplog, renamer
from ..cache import PhotoCache
from ..reference import category_key, load_reference
from ..report import build_report, format_report
from . import theme
from .thumbgrid import to_photo

TITLE = "Переименование папок"
ROW_THUMB = 56
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
    def __init__(self, master, group: grouping.FolderGroup, app_config, on_done=None):
        super().__init__(master)
        self.group = group
        self.app_config = app_config
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
        self._busy = False
        self._cancel = False
        self._last_plan: Optional[renamer.RenamePlan] = None

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

        body = ttk.Frame(self, padding=(10, 0, 10, 0))
        body.pack(fill="both", expand=True)

        table = ttk.Frame(body)
        table.pack(side="left", fill="both", expand=True)
        style = ttk.Style(self)
        style.configure("Review.Treeview", rowheight=ROW_THUMB + 8)
        self.tree = ttk.Treeview(table, columns=("new", "confidence", "status"), selectmode="extended",
                                 style="Review.Treeview")
        self.tree.heading("#0", text="Фото")
        self.tree.heading("new", text="Новое имя")
        self.tree.heading("confidence", text="Уверенность")
        self.tree.heading("status", text="Состояние")
        self.tree.column("#0", width=330)
        self.tree.column("new", width=330)
        self.tree.column("confidence", width=100, anchor="e", stretch=False)
        self.tree.column("status", width=170, stretch=False)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        palette = theme.colors()
        self.tree.tag_configure("doubt", foreground=palette["warning"])
        self.tree.tag_configure("manual", foreground=palette["accent"])
        self.tree.tag_configure("skip", foreground=palette["muted"])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_row_select())
        for number in range(1, 4):  # 1, 2, 3 — выбрать кандидата с этим номером
            self.tree.bind("<Key-%d>" % number, lambda e, n=number: self.apply_candidate(n))
        self.tree.bind("<Key-0>", lambda e: self.exclude_selected())
        for key in ("<Delete>", "<BackSpace>"):
            self.tree.bind(key, lambda e: self.exclude_selected())

        side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="left", fill="y")
        ttk.Label(side, text="Выбранные фото").pack(anchor="w")
        ttk.Label(side, foreground="#555555", wraplength=300, justify="left",
                  text="Клавиши в таблице: 1, 2, 3 — выбрать кандидата, 0 — не переименовывать. "
                       "После выбора переход к следующей строке.").pack(anchor="w", pady=(0, 6))
        self.only_doubtful = tk.BooleanVar(value=False)
        ttk.Checkbutton(side, text="Показать только требующие проверки", variable=self.only_doubtful,
                        command=self.render_rows).pack(anchor="w")
        self.sort_doubtful = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="Сомнительные — первыми", variable=self.sort_doubtful,
                        command=self.render_rows).pack(anchor="w", pady=(0, 6))
        self.candidates_frame = ttk.Frame(side)
        self.candidates_frame.pack(fill="x", pady=(4, 8))
        ttk.Label(side, text="Категория из эталона:").pack(anchor="w")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._refresh_categories())
        ttk.Entry(side, textvariable=self.search_var).pack(fill="x", pady=(2, 4))
        list_frame = ttk.Frame(side)
        list_frame.pack(fill="both", expand=True)
        self.categories_list = tk.Listbox(list_frame, exportselection=False, activestyle="none", height=16, width=34)
        cat_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.categories_list.yview)
        self.categories_list.configure(yscrollcommand=cat_scroll.set)
        self.categories_list.pack(side="left", fill="both", expand=True)
        cat_scroll.pack(side="left", fill="y")
        self.categories_list.bind("<Double-Button-1>", lambda e: self.apply_selected_category())
        ttk.Button(side, text="Применить к выбранным", command=self.apply_selected_category).pack(fill="x", pady=2)
        ttk.Button(side, text="Не переименовывать", command=self.exclude_selected).pack(fill="x", pady=2)

        bottom = ttk.Frame(self, padding=(10, 6, 10, 10))
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        self.undo_btn = ttk.Button(bottom, text="Отменить эту папку", command=self.undo_folder, state="disabled")
        self.undo_btn.pack(side="left", padx=4)
        self.skip_btn = ttk.Button(bottom, text="Пропустить папку", command=self.skip_folder)
        self.skip_btn.pack(side="left", padx=4)
        self.apply_btn = ttk.Button(bottom, text="Переименовать папку", command=self.apply_folder)
        self.apply_btn.pack(side="left")
        self.close_btn = ttk.Button(bottom, text="Закрыть", command=self.close)
        self.close_btn.pack(side="left", padx=(12, 0))

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
        self.categories = [c.name for c in self.model.categories]
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

    def _display_photos(self) -> List:
        photos = [p for p in self.match.photos if not self.only_doubtful.get() or self._is_doubtful(p)]
        if self.sort_doubtful.get():  # сомнительные первыми, внутри — порядок съёмки
            photos.sort(key=lambda p: (not self._is_doubtful(p), p.index))
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
                self._photos[photo.filename] = to_photo(image, ROW_THUMB)
                if self.tree.exists(iid):
                    self.tree.item(iid, image=self._photos[photo.filename])
            except Exception:
                pass
            self.after(1, load, position + 1)

        self.after(1, load, 0)

    # --- правка ----------------------------------------------------------------------

    def _selected_indexes(self) -> List[int]:
        return [self._row_index[iid] for iid in self.tree.selection() if iid in self._row_index]

    def _selected_rows(self) -> List[str]:
        return [iid for iid in self.tree.selection() if iid in self._row_index]

    def _next_photo(self) -> Optional[int]:
        """Фото, стоящее в таблице сразу за выбранным (запоминается ДО перестроения таблицы:
        после правки строка перестаёт быть сомнительной и уезжает вниз)."""
        rows = self.tree.get_children()
        current = self._selected_rows()
        if not rows or not current or current[-1] not in rows:
            return None
        position = rows.index(current[-1])
        return self._row_index[rows[position + 1]] if position + 1 < len(rows) else None

    def _select_photo(self, index: Optional[int]) -> None:
        if index is None:
            return
        for iid, photo_index in self._row_index.items():
            if photo_index == index:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                return

    def apply_candidate(self, number: int) -> None:
        """Клавиши 1–3: каждому выбранному фото назначается его собственный кандидат с этим номером."""
        indexes = self._selected_indexes()
        if not indexes:
            return
        following = self._next_photo()
        applied = 0
        for index in indexes:
            candidates = self.match.photos[index].candidates
            if len(candidates) >= number:
                matcher.set_category(self.match, index, category_key(candidates[number - 1].category))
                applied += 1
        if applied:
            self.render_rows()
            self._select_photo(following)

    def _on_row_select(self) -> None:
        for widget in self.candidates_frame.winfo_children():
            widget.destroy()
        indexes = self._selected_indexes()
        if len(indexes) != 1:
            ttk.Label(self.candidates_frame, text="Выбрано фото: %d" % len(indexes)).pack(anchor="w")
            return
        photo = self.match.photos[indexes[0]]
        if not photo.candidates:
            ttk.Label(self.candidates_frame, text="Кандидатов нет").pack(anchor="w")
            return
        ttk.Label(self.candidates_frame, text="Похоже на:").pack(anchor="w")
        for number, candidate in enumerate(photo.candidates, start=1):
            text = "%d — %s — %.0f%%" % (number, candidate.category, candidate.confidence)
            ttk.Button(self.candidates_frame, text=text,
                       command=lambda c=candidate.category: self.apply_category(c)).pack(fill="x", pady=1)

    def _refresh_categories(self) -> None:
        self.categories_list.delete(0, "end")
        for name in names_mod.filter_names(self.search_var.get(), self.categories):
            self.categories_list.insert("end", name)

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
        key = None if category is None else category_key(category)
        following = self._next_photo()
        for index in indexes:
            matcher.set_category(self.match, index, key)
        self.render_rows()
        self._select_photo(following)

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
        if not messagebox.askyesno(TITLE, "Переименовать %d файл(ов) в папке\n%s?"
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
