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

from .. import config as cfg, grouping, matcher, names as names_mod, oplog, renamer
from ..cache import PhotoCache
from ..reference import category_key, load_reference
from ..report import build_report, format_report
from . import theme
from .thumbgrid import to_photo

TITLE = "Переименование папок"
# Размер миниатюр в строках таблицы: по умолчанию крупный, меняется Ctrl+колесом и кнопками
# «−» и «+». Значение запоминается в настройках. Верхняя граница — размер миниатюры в кэше:
# крупнее пришлось бы заново читать исходные файлы (на сетевых дисках это долго).
MIN_THUMB, MAX_THUMB, THUMB_STEP = 48, 320, 24
PREVIEW_SIZE = 260          # крупный показ выбранного фото справа
NEIGHBOUR_SIZE = 96         # соседние кадры под ним
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
        self._preview_photos: List[tk.PhotoImage] = []   # ссылки, иначе tkinter покажет пустоту
        self._row_index: Dict[str, int] = {}
        self.thumb_size = int(getattr(app_config, "review_thumb", 96))
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
        zoom = ttk.Frame(top)
        zoom.pack(side="right", padx=(0, 12))
        ttk.Label(zoom, text="Размер фото:").pack(side="left", padx=(0, 4))
        ttk.Button(zoom, text="−", width=3,
                   command=lambda: self.set_thumb_size(self.thumb_size - THUMB_STEP)).pack(side="left")
        ttk.Button(zoom, text="+", width=3,
                   command=lambda: self.set_thumb_size(self.thumb_size + THUMB_STEP)).pack(side="left", padx=(2, 0))

        body = ttk.Frame(self, padding=(10, 0, 10, 0))
        body.pack(fill="both", expand=True)

        table = ttk.Frame(body)
        table.pack(side="left", fill="both", expand=True)
        self.style = ttk.Style(self)
        self.tree = ttk.Treeview(table, columns=("new", "confidence", "status"), selectmode="extended",
                                 style="Review.Treeview")
        self.tree.heading("#0", text="Фото")
        self.tree.heading("new", text="Новое имя")
        self.tree.heading("confidence", text="Уверенность")
        self.tree.heading("status", text="Состояние")
        self.tree.column("new", width=330)
        self.tree.column("confidence", width=100, anchor="e", stretch=False)
        self.tree.column("status", width=170, stretch=False)
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
        for number in range(1, 4):  # 1, 2, 3 — выбрать кандидата с этим номером
            self.tree.bind("<Key-%d>" % number, lambda e, n=number: self.apply_candidate(n))
        self.tree.bind("<Key-0>", lambda e: self.exclude_selected())
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):  # Ctrl+колесо — размер фото
            self.tree.bind(sequence, self._on_wheel)
        for key in ("<Delete>", "<BackSpace>"):
            self.tree.bind(key, lambda e: self.exclude_selected())

        side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="left", fill="y")

        # крупный показ выбранного фото и соседних кадров: по ним видно, что это за деталь
        # и к какой серии относится снимок (соседи важны, когда сомнительные строки подняты наверх)
        preview = ttk.LabelFrame(side, text="Выбранное фото", padding=4)
        preview.pack(fill="x")
        self.preview_image = ttk.Label(preview, anchor="center")
        self.preview_image.pack()
        self.preview_caption = ttk.Label(preview, wraplength=PREVIEW_SIZE + 40, justify="center",
                                         font="TkHeadingFont")
        self.preview_caption.pack(fill="x")
        self.neighbours_frame = ttk.Frame(preview)
        self.neighbours_frame.pack(fill="x", pady=(4, 0))

        ttk.Label(side, foreground="#555555", wraplength=300, justify="left",
                  text="Клавиши в таблице: 1, 2, 3 — выбрать кандидата, 0 — не переименовывать. "
                       "Ctrl+колесо — размер фото.").pack(anchor="w", pady=(6, 6))
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
        self.categories_list = tk.Listbox(list_frame, exportselection=False, activestyle="none", height=8, width=34)
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
        self._show_preview(None)

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

    def _apply_thumb_size(self) -> None:
        self.style.configure("Review.Treeview", rowheight=self.thumb_size + 8)
        self.tree.column("#0", width=self.thumb_size + 280)

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

    def _describe(self, index: int) -> str:
        """Как подписано фото: новое имя, если есть, иначе состояние."""
        if not 0 <= index < len(self.match.photos):
            return "—"
        photo = self.match.photos[index]
        if photo.new_name:
            return photo.new_name
        if photo.identified:
            return photo.filename + " (имя не меняется)"
        return photo.filename + " — " + STATUS_TEXT.get(photo.status, "")

    def _show_preview(self, index: Optional[int]) -> None:
        """Крупный показ выбранного фото и соседних кадров по порядку съёмки.

        Соседи важны, когда сомнительные строки подняты наверх: рядом в таблице стоят
        не соседние кадры, а по соседним кадрам как раз видно, к какой серии относится снимок.
        """
        for widget in self.neighbours_frame.winfo_children():
            widget.destroy()
        self._preview_photos = []
        if index is None:
            self.preview_image.configure(image="", text="Выберите фото в таблице")
            self.preview_caption.configure(text="")
            return
        big = self._thumbnail(index, PREVIEW_SIZE)
        self.preview_image.configure(image=big or "", text="" if big else "фото не прочитано")
        if big is not None:
            self._preview_photos.append(big)
        self.preview_caption.configure(text=self._describe(index))
        for shift, title in ((-1, "до"), (1, "после")):
            cell = ttk.Frame(self.neighbours_frame)
            cell.pack(side="left", expand=True)
            small = self._thumbnail(index + shift, NEIGHBOUR_SIZE)
            image_label = ttk.Label(cell, image=small or "", text="" if small else "—", anchor="center")
            image_label.pack()
            if small is not None:
                self._preview_photos.append(small)
            ttk.Label(cell, text="%s: %s" % (title, self._describe(index + shift)),
                      wraplength=NEIGHBOUR_SIZE + 40, justify="center").pack()

    def _on_row_select(self) -> None:
        for widget in self.candidates_frame.winfo_children():
            widget.destroy()
        indexes = self._selected_indexes()
        if len(indexes) != 1:
            self._show_preview(None)
            ttk.Label(self.candidates_frame, text="Выбрано фото: %d" % len(indexes)).pack(anchor="w")
            return
        photo = self.match.photos[indexes[0]]
        if not photo.candidates:
            ttk.Label(self.candidates_frame, text="Кандидатов нет").pack(anchor="w")
        else:
            ttk.Label(self.candidates_frame, text="Похоже на:").pack(anchor="w")
            for number, candidate in enumerate(photo.candidates, start=1):
                text = "%d — %s — %.0f%%" % (number, candidate.category, candidate.confidence)
                ttk.Button(self.candidates_frame, text=text,
                           command=lambda c=candidate.category: self.apply_category(c)).pack(fill="x", pady=1)
        self._show_preview(photo.index)

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
        unresolved = len(self.match.unidentified())
        if unresolved:
            question = ("Не опознано фото: %d. Их имена останутся прежними, а у остальных номера\n"
                        "будут идти по порядку съёмки без учёта пропущенных — потом будет сложно\n"
                        "понять, какой ракурс к какой детали.\n\n"
                        "Лучше разобрать их сейчас: включите «Показать только требующие проверки»\n"
                        "и назначьте категории клавишами 1, 2, 3 или 0.\n\n"
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
