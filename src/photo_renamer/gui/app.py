"""Первое окно программы: папка проекта → анализ → группы похожих папок и их эталоны.

Обработка идёт строго последовательно, без потоков. Окно не «замирает»: обратные вызовы
прогресса обновляют интерфейс (update()), а кнопка «Прервать» останавливает анализ.
"""

import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk
from typing import Dict, List, Optional

from .. import config as cfg
from .. import grouping
from ..cache import PhotoCache
from ..names import NamesList
from . import theme
from .editor import ReferenceEditor
from .worker import Cancelled, Worker
from .review import BatchWindow

TITLE = "CV"

# Отметки в таблице пишутся словами: значки вроде «✔» на Windows 7 в tkinter выводятся не всюду.
# Галочка эталона: её ставит человек щелчком по столбцу, а программа — на самую
# переименованную папку группы
MARK_REFERENCE = "\u2713"
MARK_YES = "да"
MARK_NO = "нет"


def open_in_file_manager(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — открыть папку в Проводнике
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _percent(share: float) -> int:
    """Доля в процентах. 100 пишется только когда названо всё: 99.6 % — это ещё не готово."""
    value = int(round(share * 100))
    return min(value, 99) if share < 1 else value


def _tooltip(widget, text: str) -> None:
    """Подсказка при наведении (у tkinter своей нет)."""
    tip = {"window": None}

    def show(_event=None):
        if tip["window"] is not None:
            return
        window = tk.Toplevel(widget)
        window.wm_overrideredirect(True)
        window.attributes("-topmost", True)
        palette = theme.colors()
        tk.Label(window, text=text, background=palette["surface"], foreground=palette["text"],
                 relief="solid", borderwidth=1, padx=6, pady=3).pack()
        x = widget.winfo_rootx() + widget.winfo_width() // 2 - 120
        window.geometry("+%d+%d" % (max(x, 0), widget.winfo_rooty() + widget.winfo_height() + 4))
        tip["window"] = window

    def hide(_event=None):
        if tip["window"] is not None:
            tip["window"].destroy()
            tip["window"] = None

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")


class MainWindow(tk.Tk):
    def __init__(self, app_config: cfg.AppConfig, config_warnings: List[str] = ()):
        super().__init__()
        self.app_config = app_config
        self.names = NamesList(app_config.names_path())
        self.analysis: Optional[grouping.ProjectAnalysis] = None
        self.project_root = ""
        self._busy = False
        self._cancel = False
        self._close_requested = False
        self._item_folder: Dict[str, grouping.FolderInfo] = {}
        self._item_group: Dict[str, int] = {}
        self.worker = Worker(self)

        theme.apply(self, app_config.theme)
        theme.install_clipboard_keys(self)
        self.palette = theme.colors()
        self.title(TITLE)
        self.geometry("1150x650")
        self.minsize(850, 450)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self.root_var.set(self._load_state().get("project", ""))
        self._update_buttons()
        self.after_idle(self._widen_to_fit_columns)
        if config_warnings:
            messagebox.showwarning(TITLE, "Замечания к настройкам (config.json):\n\n" + "\n".join(config_warnings),
                                   parent=self)

    # --- построение окна ------------------------------------------------------------------

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Папка проекта:").pack(side="left")
        self.root_var = tk.StringVar()
        self.root_entry = ttk.Entry(top, textvariable=self.root_var)
        self.root_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.root_entry.bind("<Return>", lambda e: self.analyze())
        self.browse_btn = ttk.Button(top, text="Обзор…", command=self.browse)
        self.browse_btn.pack(side="left")
        self.analyze_btn = ttk.Button(top, text="Анализировать", command=self.analyze)
        self.analyze_btn.pack(side="left", padx=(6, 0))
        self.lamp = theme.LampButton(top, command=self.on_lamp)
        self.lamp.set(self.app_config.theme != "dark")
        self.lamp.pack(side="left", padx=(10, 0))
        _tooltip(self.lamp, "Оформление: лампочка горит — светлое, погашена — тёмное")

        hint = ("Программа находит все папки с фото, сравнивает их и объединяет похожие в группы. "
                "Для каждой группы нужен один эталон — папка, переименованная по шаблону. "
                "Галочка «Эталон» стоит на самой переименованной папке группы; её можно "
                "переставить щелчком, как и отметку «Переименовать».")
        # перенос по ширине окна, а не по заданному числу: иначе на узком окне подсказка
        # уходит за край, а на широком переносится там, где место ещё есть
        self.hint_label = ttk.Label(self, text=hint, foreground=self.palette["muted"],
                                    padding=(10, 0, 10, 6), wraplength=1100)
        self.hint_label.pack(fill="x")
        self.bind("<Configure>", self._on_resize)

        middle = ttk.Frame(self, padding=(10, 0, 10, 0))
        middle.pack(fill="both", expand=True)

        tree_frame = ttk.Frame(middle)     # размещается после правой панели, см. ниже
        self.tree = ttk.Treeview(tree_frame, columns=("reference", "rename", "count", "status", "similarity"),
                                 selectmode="browse")
        # Заголовки прижаты к тому же краю, что и значения: иначе подпись столбца стоит над
        # пустым местом, а значения — у другого края (замечание с рабочей машины).
        self.tree.heading("#0", text="Группа / папка", anchor="w")
        self.tree.heading("reference", text="Эталон", anchor="w")
        self.tree.heading("rename", text="Переименовать", anchor="w")
        self.tree.heading("count", text="Фото", anchor="w")
        self.tree.heading("status", text="Обработано", anchor="w")
        self.tree.heading("similarity", text="Сходство", anchor="w")
        # Значения всех столбцов прижаты к левому краю: взгляд читает строку слева направо,
        # и число фото не приходится искать у дальнего края.
        #
        # Ни один столбец не растягивается. Иначе Tk, меняя ширину столбца, забирает разницу
        # у растяжимого: потянешь границу «Фото» — на столько же ужмётся «Группа / папка»,
        # которую никто не трогал (замечание с рабочей машины). Теперь меняется только тот
        # столбец, который тянут, а остальные просто сдвигаются, как в Проводнике; если они
        # перестали помещаться, внизу появляется полоса прокрутки.
        self.tree.column("#0", width=360, minwidth=180)
        for column in ("reference", "rename", "count", "status", "similarity"):
            self.tree.column(column, anchor="w", minwidth=40)
        # ширина — по самой длинной надписи, которая в столбце бывает
        theme.fit_columns(self.tree, {"reference": MARK_REFERENCE, "rename": MARK_NO, "count": "9999",
                                      "status": "100%", "similarity": "высокое"})
        theme.fit_column_headers(self.tree)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.hscroll = ttk.Scrollbar(tree_frame, orient="horizontal")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.hscroll.grid(row=1, column=0, sticky="ew")
        self._update_hscroll = theme.fixed_columns(self.tree, self.hscroll)
        self._update_hscroll()
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_buttons())
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-1>", self._on_click)

        bold = font.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        self.tree.tag_configure("group", font=bold)
        self.tree.tag_configure("reference", font=bold)
        self.tree.tag_configure("not_ready", foreground=self.palette["warning"])
        self.tree.tag_configure("low", foreground=self.palette["error"])

        # Правая панель размещается ДО таблицы и получает ширину по самой длинной надписи
        # на кнопке. Иначе таблица забирала место себе, а надписи на кнопках обрезались
        # («Сделать эталоном груг»): при нехватке места Tk ужимает то, что размещено позже.
        side = ttk.Frame(middle, padding=(10, 0, 0, 0))
        side.pack(side="right", fill="y")
        ttk.Label(side, text="Выбранная папка").pack(anchor="w", pady=(0, 4))
        self.reference_btn = ttk.Button(side, text="Сделать эталоном группы", command=self.make_reference)
        self.reference_btn.pack(fill="x", pady=2)
        self.move_btn = ttk.Menubutton(side, text="Перенести в группу", style="Centered.TMenubutton")
        self.move_menu = tk.Menu(self.move_btn, tearoff=False, postcommand=self._fill_move_menu)
        self.move_btn["menu"] = self.move_menu
        self.move_btn.pack(fill="x", pady=2)
        self.open_btn = ttk.Button(side, text="Открыть папку", command=self.open_folder)
        self.open_btn.pack(fill="x", pady=2)
        ttk.Separator(side).pack(fill="x", pady=10)
        ttk.Label(side, text="Группа").pack(anchor="w", pady=(0, 4))
        self.edit_reference_btn = ttk.Button(side, text="Создать / изменить эталон…", command=self.edit_reference)
        self.edit_reference_btn.pack(fill="x", pady=2)
        self.rename_group_btn = ttk.Button(side, text="Переименовать папки группы…", command=self.rename_group)
        self.rename_group_btn.pack(fill="x", pady=2)
        ttk.Separator(side).pack(fill="x", pady=10)
        self.refresh_btn = ttk.Button(side, text="Обновить имена файлов", command=self.refresh)
        self.refresh_btn.pack(fill="x", pady=2)

        tree_frame.pack(side="left", fill="both", expand=True)

        bottom = ttk.Frame(self, padding=(10, 6, 10, 10))
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value="Выберите папку проекта и нажмите «Анализировать».")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        self.cancel_btn = ttk.Button(bottom, text="Прервать", command=self.cancel)
        self.progress = ttk.Progressbar(bottom, length=260, mode="determinate")

    def on_lamp(self, lit: bool) -> None:
        """Лампочка горит — светлое оформление, погашена — тёмное. Выбор запоминается."""
        mode = "light" if lit else "dark"
        self.app_config.theme = mode
        theme.apply(self, mode)
        self.palette = theme.colors()
        theme.apply_to_existing(self)
        for window in self.winfo_children():      # уже открытые окна редактора и переименования
            if isinstance(window, tk.Toplevel):
                window.configure(background=self.palette["background"])
                theme.apply_to_existing(window)
        self.tree.tag_configure("not_ready", foreground=self.palette["warning"])
        self.tree.tag_configure("low", foreground=self.palette["error"])
        self.lamp.redraw()
        self.render()
        try:
            cfg.save_config(self.app_config)
        except OSError as exc:
            self.status_var.set("Не удалось сохранить настройки: %s" % exc)

    # --- состояние между запусками ----------------------------------------------------------

    def _state_path(self) -> str:
        return os.path.join(os.path.dirname(self.app_config.cache_path()), "state.json")

    def _load_state(self) -> dict:
        try:
            with open(self._state_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._state_path()), exist_ok=True)
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump({"project": self.root_var.get().strip()}, f, ensure_ascii=False)
        except OSError:
            pass

    # --- анализ -------------------------------------------------------------------------------

    def browse(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Папка проекта", initialdir=self.root_var.get() or None)
        if path:
            self.root_var.set(os.path.normpath(path))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._cancel = False
        state = "disabled" if busy else "normal"
        for widget in (self.root_entry, self.browse_btn, self.analyze_btn):
            widget.configure(state=state)
        if busy:
            self.progress.pack(side="left", padx=6)
            self.cancel_btn.pack(side="left")
        else:
            self.progress.pack_forget()
            self.cancel_btn.pack_forget()
        self._update_buttons()

    def cancel(self) -> None:
        self._cancel = True
        self.worker.cancel()

    def analyze(self) -> None:
        """Анализ проекта. Чтение фото идёт в фоновой задаче, поэтому окно остаётся отзывчивым."""
        if self._busy:
            return
        root = self.root_var.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showerror(TITLE, "Папка не найдена:\n%s" % (root or "(не указана)"), parent=self)
            return
        self.project_root = root
        self._save_state()
        self._set_busy(True)
        self.status_var.set("Поиск папок с фото…")

        app_config = self.app_config

        def job(post, cancelled):
            # выполняется в отдельном потоке: только чтение файлов, ни одного обращения к окну
            cache = PhotoCache(app_config.cache_path(), app_config.cache_max_mb)

            def folder_started(number, total, path):
                post(("folder", number, total, path))
                if cancelled():
                    raise Cancelled()

            def photo_read(done, total):
                post(("progress", done, total))
                if cancelled():
                    raise Cancelled()

            extractor = app_config.extractor()
            try:
                return grouping.analyze_project(
                    [root], extractor, app_config.match.for_features(extractor.encoder is not None),
                    app_config.group_threshold,
                    feature_source=cache.extract_many, sort=app_config.sort, date_source=cache.shot_time,
                    on_folder=folder_started, on_progress=photo_read)
            finally:
                cache.close()

        self.worker.start(job, self._on_worker_message, self._on_analysis_done)

    def _on_worker_message(self, message) -> None:
        """Сообщения из фоновой задачи приходят сюда, в главный поток."""
        kind = message[0]
        if kind == "folder":
            _, number, total, path = message
            self.status_var.set("Папка %d из %d: %s" % (number, total, self._display_path(path)))
            self.progress.configure(maximum=1, value=0)
        elif kind == "progress":
            _, done, total = message
            self.progress.configure(maximum=max(total, 1), value=done)

    def _on_analysis_done(self, result, error) -> None:
        if isinstance(error, Cancelled):
            self.status_var.set("Анализ прерван.")
        elif error is not None:
            self.status_var.set("Ошибка анализа.")
            messagebox.showerror(TITLE, "Ошибка при анализе:\n%s" % error, parent=self)
        else:
            self.analysis = result
            self.status_var.set(self._summary())
        self._set_busy(False)
        self.render()
        if self._close_requested:
            self.destroy()

    def _summary(self) -> str:
        a = self.analysis
        need = sum(not g.reference_is_ready for g in a.groups)
        text = "Папок с фото: %d, групп: %d. " % (len(a.folders), len(a.groups))
        text += ("Нужно создать эталонов: %d." % need) if need else "Все эталоны готовы."
        if a.errors:
            text += " Недоступных папок: %d." % len(a.errors)
        return text

    # --- отображение ------------------------------------------------------------------------

    def _display_path(self, path: str) -> str:
        root = self.project_root
        if root:
            try:
                rel = os.path.relpath(path, root)
            except ValueError:  # другой диск на Windows
                return path
            if not rel.startswith(".."):
                return os.path.join(os.path.basename(os.path.normpath(root)), rel) if rel != "." else \
                    os.path.basename(os.path.normpath(root))
        return path

    def _rename_mark(self, group: grouping.FolderGroup, folder: grouping.FolderInfo) -> str:
        """Отметка «переименовывать ли папку». У эталона её нет: он образец, а не цель."""
        if folder is group.reference:
            return ""
        return MARK_YES if folder.will_rename else MARK_NO

    def _folder_status(self, group: grouping.FolderGroup, folder: grouping.FolderInfo) -> str:
        """Насколько папка переименована. В столбце только число: его смысл написан
        в заголовке «Обработано», а слово в каждой строке лишнее."""
        status = "%d%%" % _percent(folder.named_share)
        if folder.unreadable:
            status += "   не прочитано фото: %d" % folder.unreadable
        return status

    def _on_resize(self, event) -> None:
        if event.widget is self:
            self.hint_label.configure(wraplength=max(400, event.width - 30))

    def _widen_to_fit_columns(self) -> None:
        """Раздвигает окно при открытии, чтобы столбцы поместились целиком.

        Числом ширину окна не задать: на рабочей машине шрифт крупнее, и последний столбец
        («Сходство») при открытии не влезал. Шире экрана окно не делаем и никогда не сужаем.
        """
        self.update_idletasks()
        if self.winfo_width() < 400:          # окно ещё не разложено
            return
        scrollbar = self.tree.master.winfo_width() - self.tree.winfo_width()
        needed = (theme.columns_width(self.tree) + scrollbar
                  + self.reference_btn.master.winfo_width() + 30)
        width = min(max(self.winfo_width(), needed), int(self.winfo_screenwidth() * 0.95))
        if width > self.winfo_width():
            self.geometry("%dx%d" % (width, self.winfo_height()))

    def render(self) -> None:
        selected = self._selected_folder()
        self.tree.delete(*self.tree.get_children())
        self._item_folder.clear()
        self._item_group.clear()
        if self.analysis is None:
            self._update_buttons()
            return
        for k, group in enumerate(self.analysis.groups):
            ready = "эталон готов" if group.reference_is_ready else "нужно создать эталон"
            gid = self.tree.insert("", "end", text="Группа %d — %s" % (k + 1, ready),
                                   values=("", "", "", "папок: %d" % len(group.folders), ""), open=True,
                                   tags=("group",) if group.reference_is_ready else ("group", "not_ready"))
            self._item_group[gid] = k
            for folder in group.folders:
                similarity = grouping.similarity_label(grouping.similarity_to_reference(self.analysis, folder))
                tags = []
                if folder is group.reference:
                    tags.append("reference")
                    if not folder.is_reference:
                        tags.append("not_ready")
                if similarity == "низкое":
                    tags.append("low")
                iid = self.tree.insert(gid, "end", text=self._display_path(folder.path),
                                       values=(MARK_REFERENCE if folder is group.reference else "",
                                               self._rename_mark(group, folder),
                                               folder.count, self._folder_status(group, folder), similarity),
                                       tags=tuple(tags))
                self._item_folder[iid] = folder
                self._item_group[iid] = k
                if folder is selected:
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
        for e in self.analysis.errors:
            self.tree.insert("", "end", text="Недоступна: %s" % self._display_path(e.path),
                             values=("", "", "", e.message, ""), tags=("low",))
        self._update_buttons()

    # --- действия ------------------------------------------------------------------------------

    def _on_click(self, event) -> None:
        """Щелчок по столбцу «Эталон» или «Переименовать» — это отметка, а не просто выделение."""
        if self._busy or self.analysis is None:
            return
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        item = self.tree.identify_row(event.y)
        folder = self._item_folder.get(item)
        if folder is None:
            return
        column = self.tree.identify_column(event.x)
        if column == "#1":
            self.tree.selection_set(item)
            self.make_reference()
        elif column == "#2":
            self.toggle_rename(folder)

    def toggle_rename(self, folder: grouping.FolderInfo) -> None:
        """Снимает или ставит отметку «переименовывать эту папку».

        Нужна, когда программа сочла папку уже названной, а на деле её не переименовывали
        (или наоборот — папку назвали вручную и трогать её не надо).
        """
        group = self.analysis.groups[grouping.group_of(self.analysis, folder)]
        if folder is group.reference:
            return
        folder.include = not folder.will_rename
        self.render()
        self.status_var.set(self._summary())

    def _on_double_click(self, event) -> None:
        """Двойной щелчок открывает папку, но не в столбцах с отметками: там он их только дёргает."""
        if self.tree.identify_column(event.x) not in ("#1", "#2"):
            self.open_folder()

    def _selected_item(self) -> Optional[str]:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _selected_folder(self) -> Optional[grouping.FolderInfo]:
        item = self._selected_item()
        return self._item_folder.get(item) if item else None

    def _selected_group(self) -> Optional[int]:
        item = self._selected_item()
        return self._item_group.get(item) if item else None

    def _update_buttons(self) -> None:
        folder = None if self._busy else self._selected_folder()
        group = None if self._busy else self._selected_group()
        is_ref = folder is not None and self.analysis.groups[group].reference is folder
        self.reference_btn.configure(state="normal" if folder is not None and not is_ref else "disabled")
        self.move_btn.configure(state="normal" if folder is not None else "disabled")
        self.open_btn.configure(state="normal" if folder is not None else "disabled")
        self.edit_reference_btn.configure(state="normal" if group is not None else "disabled")
        can_rename = group is not None and self.analysis.groups[group].reference_is_ready
        self.rename_group_btn.configure(state="normal" if can_rename else "disabled")
        self.refresh_btn.configure(state="normal" if self.analysis is not None and not self._busy else "disabled")

    def _fill_move_menu(self) -> None:
        self.move_menu.delete(0, "end")
        folder = self._selected_folder()
        if folder is None:
            return
        current = grouping.group_of(self.analysis, folder)
        for k, group in enumerate(self.analysis.groups):
            label = "Группа %d (папок: %d)" % (k + 1, len(group.folders))
            self.move_menu.add_command(label=label, state="disabled" if k == current else "normal",
                                       command=lambda k=k: self.move_selected(k))
        self.move_menu.add_separator()
        self.move_menu.add_command(label="Новая группа", command=lambda: self.move_selected(None))

    def move_selected(self, target: Optional[int]) -> None:
        folder = self._selected_folder()
        if folder is None:
            return
        grouping.move_folder(self.analysis, folder, target)
        self.render()
        self.status_var.set(self._summary())

    def make_reference(self) -> None:
        folder = self._selected_folder()
        if folder is None:
            return
        grouping.set_reference(self.analysis, folder)
        self.render()
        self.status_var.set(self._summary())

    def open_folder(self) -> None:
        folder = self._selected_folder()
        if folder is not None:
            try:
                open_in_file_manager(folder.path)
            except OSError as exc:
                messagebox.showerror(TITLE, "Не удалось открыть папку:\n%s" % exc, parent=self)

    def refresh(self) -> None:
        if self.analysis is None:
            return
        cache = PhotoCache(self.app_config.cache_path(), self.app_config.cache_max_mb)
        try:
            grouping.refresh_names(self.analysis, sort=self.app_config.sort, date_source=cache.shot_time)
        finally:
            cache.close()
        self.render()
        self.status_var.set(self._summary())

    def edit_reference(self) -> None:
        """Открывает выделенную папку, а не эталон группы: человек выбрал её сам.

        Раньше кнопка всегда открывала эталон, и при выделенной соседней папке получалось,
        что программа «перепутала папки» (замечание с рабочей машины).
        """
        group = self._selected_group()
        if group is None:
            return
        folder = self._selected_folder() or self.analysis.groups[group].reference
        ReferenceEditor(self, folder.path, self.app_config, self.names, on_done=self.refresh)

    def rename_group(self) -> None:
        group = self._selected_group()
        if group is None:
            return
        target = self.analysis.groups[group]
        if not target.targets:
            messagebox.showinfo(TITLE, "В этой группе нет папок для переименования.", parent=self)
            return
        BatchWindow(self, target, self.app_config, on_done=self.refresh, names=self.names)

    def on_close(self) -> None:
        if self._busy or self.worker.busy():
            self._close_requested = True
            self._cancel = True
            self.worker.cancel()
            return
        self.destroy()

    def wait_for_worker(self, timeout: float = 600.0) -> None:
        """Дождаться окончания фоновой задачи (используется в тестах и при закрытии)."""
        self.worker.wait(timeout)


def main(argv=None) -> None:
    config, warnings = cfg.load_config()
    MainWindow(config, warnings).mainloop()
